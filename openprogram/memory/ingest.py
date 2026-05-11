"""Two-step wiki ingest — analyze, then generate.

Implements the Karpathy / nashsu LLM-Wiki ingest protocol on top of the
local builtin memory provider. A "source" can be either:

  * a finished conversation (list of messages from SessionDB), or
  * a hand-written markdown blob (future, when we wire a manual
    ``memory_ingest`` tool)

Pipeline:

  Step 1 — Analysis
      LLM reads (source content + ``purpose.md`` + ``index.md``) and
      outputs a structured analysis: key entities, key concepts,
      arguments, connections to existing wiki, contradictions,
      recommendations.

  Step 2 — Generation
      LLM takes the analysis + (``SCHEMA.md`` + ``purpose.md`` +
      ``index.md`` + ``overview.md``) and emits FILE blocks for each
      page to create or update. Optionally emits REVIEW blocks for
      contradictions / duplicates / missing pages / suggestions that
      need human judgement.

The orchestrator parses FILE/REVIEW blocks, writes pages to disk,
appends a log entry, regenerates overview.md, and persists REVIEW
items for the (future) UI to surface.

Why two calls instead of one: the analysis step is allowed to "think
out loud" without the pressure of producing valid wiki page output;
the generation step then translates a settled analysis into deterministic
FILE block format. Empirically much better grounded than one-shot ingest
that has to invent structure mid-stream.

Lifted from nashsu/llm_wiki (`src/lib/ingest.ts:buildAnalysisPrompt`
and `:buildGenerationPrompt`) and condensed into Python with our naming.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT_TEMPLATE = """\
You are an expert wiki analyst. Read the source below and produce a
structured analysis. Do NOT output chain-of-thought or hidden reasoning;
reason internally and write only the concise final analysis.

Your analysis must cover, as markdown sections:

## Key Entities
List people, products, tools, organizations mentioned. For each:
- Name and type
- Role in the source (central vs peripheral)
- Whether it likely already exists in the wiki (check the index)

## Key Concepts
List ideas, techniques, phenomena, frameworks. For each:
- Name and one-line definition
- Why it matters in this source
- Whether it likely already exists in the wiki

## Main Arguments & Findings
- Core claims, decisions, lessons learned
- Evidence supporting them
- How strong the evidence is

## Connections to Existing Wiki
- What existing pages does this source relate to?
- Does it strengthen, challenge, or extend existing knowledge?

## Contradictions & Tensions
- Does anything in this source conflict with existing wiki content?
- Are there internal tensions or caveats?

## Recommendations
- What wiki pages should be created or updated?
- What should be emphasised vs de-emphasised?
- Any open questions worth flagging for the user?

Be thorough but concise. Focus on what's genuinely important.

---

## Wiki Purpose
{purpose}

---

## Current Wiki Index
{index}

---

## Source
{source}
"""


GENERATION_PROMPT_TEMPLATE = """\
You are a wiki maintainer. Based on the analysis provided, generate or
update wiki files. Do NOT output chain-of-thought or hidden reasoning;
output only the requested FILE/REVIEW blocks.

## What to generate

1. A source summary page at **wiki/sources/{source_slug}.md** (MUST use this exact path).
2. Entity pages in wiki/entities/ for new key entities the analysis identified.
3. Concept pages in wiki/concepts/ for new key concepts.
4. An updated **wiki/index.md** — preserve every existing entry; add new entries to their category.
5. A log entry to append to **wiki/log.md**, format:
       ## [{today}] ingest | {source_title}
       - created entities/<slug>.md
       - updated concepts/<slug>.md
       - …
6. An updated **wiki/overview.md** — 2-4 paragraph summary of the WHOLE wiki, refreshed to reflect this new source.

## FILE block format (parser is strict)

```
FILE: wiki/<kind>/<slug>.md
---
type: <entity|concept|source|procedure|user|query|synthesis|overview>
title: <human-readable>
tags: [<tag1>, <tag2>]
related: [<slug>, <slug>]
created: <YYYY-MM-DD>
updated: {today}
sources: ["{source_slug}"]
---

# <title>

<body markdown — use [[wikilink]] in body for cross-references>
END
```

Rules:
- Each FILE block starts with `FILE: <path>` on its own line.
- Then the COMPLETE file content (frontmatter + body).
- Then `END` on its own line.
- Use bare slugs in `related:` (no `wiki/`, no `.md`, no `[[ ]]`).
- Use [[wikilink]] only in the BODY, never frontmatter.
- Filenames: kebab-case.

## REVIEW block format (optional)

After all FILE blocks, emit zero or more REVIEW blocks for things that
need human judgement:

```
REVIEW: <kind>
TITLE: <one-line summary>
DETAIL: <what the LLM noticed>
OPTIONS: Create Page | Skip
END
```

Allowed kinds:
- `contradiction` — analysis found conflicts with existing wiki content
- `duplicate` — an entity / concept may already exist under a different name
- `missing-page` — important concept referenced but lacks its own page
- `suggestion` — follow-ups, related sources worth pulling in

Don't emit trivial reviews.

## Wiki schema (follow it exactly)
{schema}

---

## Wiki Purpose
{purpose}

---

## Current Wiki Index (preserve existing entries)
{index}

---

## Current Overview (refresh this one)
{overview}

---

## Existing Wiki Pages (merge into these — do NOT discard existing content)
{existing_pages}

---

## Analysis
{analysis}

---

## Source
{source}
"""


# ---------------------------------------------------------------------------
# FILE / REVIEW block parser
# ---------------------------------------------------------------------------

FILE_BLOCK_RE = re.compile(
    r"^FILE:\s*(?P<path>[^\n]+)\n(?P<body>.*?)\n^END\s*$",
    re.MULTILINE | re.DOTALL,
)
REVIEW_BLOCK_RE = re.compile(
    r"^REVIEW:\s*(?P<kind>[^\n]+)\n(?P<body>.*?)\n^END\s*$",
    re.MULTILINE | re.DOTALL,
)


def parse_blocks(raw: str) -> tuple[list[dict], list[dict]]:
    """Split the generation output into file writes and review items.

    Tolerant: trims surrounding whitespace, ignores text outside any
    FILE/REVIEW block (lets the LLM include a short preamble).
    """
    files: list[dict] = []
    for m in FILE_BLOCK_RE.finditer(raw):
        path = m.group("path").strip()
        body = m.group("body").rstrip()
        files.append({"path": path, "body": body})
    reviews: list[dict] = []
    for m in REVIEW_BLOCK_RE.finditer(raw):
        kind = m.group("kind").strip()
        fields: dict[str, str] = {"kind": kind}
        for line in m.group("body").splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
        reviews.append(fields)
    return files, reviews


# ---------------------------------------------------------------------------
# Helpers — read wiki context the prompts need
# ---------------------------------------------------------------------------


def _read_or_default(path: Path, default: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


def _wiki_context() -> dict[str, str]:
    wiki = store.wiki_dir()
    return {
        "schema": _read_or_default(wiki / "SCHEMA.md", "(no schema)"),
        "purpose": _read_or_default(wiki / "purpose.md", "(no purpose)"),
        "index": _read_or_default(wiki / "index.md", "(empty index)"),
        "overview": _read_or_default(wiki / "overview.md", "(empty overview)"),
    }


def _existing_pages_dump(max_total_chars: int = 30_000) -> str:
    """Concatenate every existing wiki page body (other than the
    protocol docs) so the generation step can MERGE rather than
    overwrite. Without this the LLM only sees the index summaries
    and silently rewrites pages from scratch — which destroys the
    "compounding" property the whole pattern depends on.

    Truncates the oldest pages first if total length exceeds the
    char budget so the most recently-touched pages always survive.
    """
    wiki = store.wiki_dir()
    skip = {"SCHEMA.md", "purpose.md", "index.md", "log.md", "overview.md", "reflections.md"}
    pages: list[tuple[float, str, str]] = []
    for p in wiki.rglob("*.md"):
        if p.name in skip:
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = p.relative_to(wiki).as_posix()
        pages.append((p.stat().st_mtime, rel, body))
    if not pages:
        return "(no existing wiki pages yet)"
    pages.sort(key=lambda r: -r[0])  # newest first
    out: list[str] = []
    used = 0
    for _mtime, rel, body in pages:
        block = f"### Existing page `wiki/{rel}`\n\n{body}\n\n---\n"
        if used + len(block) > max_total_chars and out:
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


def _safe_path(rel_path: str) -> Path | None:
    """Return the absolute wiki path for ``rel_path`` if it's inside the
    wiki dir. Returns None for paths that try to escape, would overwrite
    SCHEMA.md / purpose.md, or otherwise look unsafe.
    """
    wiki_root = store.wiki_dir().resolve()
    # The LLM is asked to emit "wiki/<kind>/<slug>.md". Strip the wiki/
    # prefix if present so we always resolve relative to wiki_root.
    rel = rel_path.strip()
    if rel.startswith("wiki/"):
        rel = rel[len("wiki/"):]
    target = (wiki_root / rel).resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError:
        return None
    # Never let the ingest stomp the protocol docs.
    protected = {"SCHEMA.md", "purpose.md"}
    if target.name in protected and target.parent == wiki_root:
        return None
    if not target.name.endswith(".md"):
        return None
    return target


# ---------------------------------------------------------------------------
# Source rendering — turn a conversation into ingest input
# ---------------------------------------------------------------------------


def render_messages_as_source(
    messages: Iterable[dict[str, Any]],
    *,
    session_id: str,
    max_chars: int = 12000,
) -> tuple[str, str, str]:
    """Render a message list into (source_text, slug, title) for ingest.

    Truncates content from the tail (most-recent biased) to keep the
    LLM prompt under control; matches Hermes / nashsu's approach.
    """
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text", p)) if isinstance(p, dict) else str(p)
                for p in content
            )
        content = str(content).strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = "... [truncated head] ...\n\n" + text[-max_chars:]
    today = datetime.now().strftime("%Y-%m-%d")

    # Pull in TODAY's short-term file as a signal-boost. It's what the
    # LLM bothered to memory_note during the session, so it's a curated
    # highlights track. Append it AFTER the truncation since it's small
    # and high-signal — don't let it get cut.
    try:
        st_path = store.short_term_for(today)
        if st_path.exists():
            st_body = st_path.read_text(encoding="utf-8").strip()
            if st_body:
                text = (
                    text
                    + "\n\n---\n\n"
                    + f"## Short-term notes from {today} "
                    + "(LLM-flagged highlights during the session)\n\n"
                    + st_body
                )
    except Exception:
        pass

    short_sid = session_id.replace("local_", "")[:10]
    slug = f"session-{short_sid}-{today}"
    title = f"Session {short_sid} ({today})"
    return text, slug, title


# ---------------------------------------------------------------------------
# The two-step ingest entry point
# ---------------------------------------------------------------------------


def ingest_source(
    *,
    source_text: str,
    source_slug: str,
    source_title: str,
    llm: Callable[[str, str], str],
) -> dict[str, Any]:
    """Run the two-step ingest and return a summary of what changed.

    ``llm(system_prompt, user_text) -> str`` is supplied by the caller
    so this module stays provider-agnostic. Use
    ``openprogram.memory.llm_bridge.build_default_llm()`` to get one.

    Side effects:
      * writes / overwrites pages in ``wiki/``
      * appends a log entry to ``wiki/log.md``
      * persists pending REVIEW items to ``.state/review-queue.json``

    Returns ``{ok, n_files, n_reviews, log_entry, error}``.
    """
    ctx = _wiki_context()
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Step 1: analysis ------------------------------------------------
    analysis_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        purpose=ctx["purpose"],
        index=ctx["index"],
        source=source_text,
    )
    try:
        analysis = llm("", analysis_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest analysis call failed: %s", exc)
        return {"ok": False, "error": f"analysis: {exc}"}
    if not analysis or not analysis.strip():
        return {"ok": False, "error": "analysis returned empty"}

    # --- Step 2: generation ---------------------------------------------
    gen_prompt = GENERATION_PROMPT_TEMPLATE.format(
        schema=ctx["schema"],
        purpose=ctx["purpose"],
        index=ctx["index"],
        overview=ctx["overview"],
        existing_pages=_existing_pages_dump(),
        analysis=analysis,
        source=source_text,
        source_slug=source_slug,
        source_title=source_title,
        today=today,
    )
    try:
        generation = llm("", gen_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest generation call failed: %s", exc)
        return {"ok": False, "error": f"generation: {exc}"}

    files, reviews = parse_blocks(generation)
    if not files:
        logger.warning("ingest produced 0 FILE blocks — model output didn't follow the protocol")
        return {
            "ok": False,
            "error": "no FILE blocks parsed",
            "raw_preview": generation[:400],
        }

    # --- Apply writes ----------------------------------------------------
    written: list[str] = []
    skipped: list[str] = []
    for f in files:
        target = _safe_path(f["path"])
        if target is None:
            skipped.append(f["path"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["body"] + "\n", encoding="utf-8")
        written.append(str(target.relative_to(store.wiki_dir())))

    # --- Append log ------------------------------------------------------
    log_lines = [f"## [{today}] ingest | {source_title}"]
    for w in written:
        log_lines.append(f"- wrote {w}")
    log_entry = "\n".join(log_lines) + "\n"
    _append_log(log_entry)

    # --- Persist review items -------------------------------------------
    if reviews:
        _persist_reviews(reviews, source_slug=source_slug, ts=today)

    # --- Enrich wikilinks on the pages we just wrote ---------------------
    # Skip the bookkeeping pages — index/log/overview have their own
    # cross-link rules; only the content pages get post-processing.
    enrich_stats: dict[str, Any] = {"skipped": True}
    try:
        from . import enrich
        skip_names = {"index.md", "log.md", "overview.md"}
        content_paths = [
            store.wiki_dir() / w for w in written
            if Path(w).name not in skip_names
        ]
        if content_paths:
            enrich_stats = enrich.enrich_pages(content_paths, llm=llm)
            logger.info(
                "memory: enriched %d/%d pages with wikilinks (links_added=%d)",
                enrich_stats.get("pages_changed", 0),
                len(content_paths),
                enrich_stats.get("links_added", 0),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich-wikilinks pass failed (non-fatal): %s", exc)
        enrich_stats = {"error": str(exc)}

    return {
        "ok": True,
        "n_files": len(written),
        "n_reviews": len(reviews),
        "enrich": enrich_stats,
        "log_entry": log_entry,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Log / review persistence
# ---------------------------------------------------------------------------


def _append_log(entry: str) -> None:
    path = store.wiki_dir() / "log.md"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    if not existing.strip():
        existing = "# Wiki log\n\n"
    # Newest first — readers `grep "^## \\["` then `tail` or `head`.
    path.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")


def _persist_reviews(reviews: list[dict], *, source_slug: str, ts: str) -> None:
    """Append review items to ``.state/review-queue.json``.

    Read-only consumers (future web UI / lint tool) load this file and
    show items to the user with the predefined OPTIONS for resolution.
    """
    import json
    qpath = store.state_dir() / "review-queue.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    if qpath.exists():
        try:
            items = json.loads(qpath.read_text(encoding="utf-8"))
        except Exception:
            items = []
    next_id = (max((it.get("id", 0) for it in items), default=0) + 1)
    for r in reviews:
        items.append({
            "id": next_id,
            "kind": r.get("kind"),
            "title": r.get("title", ""),
            "detail": r.get("detail", ""),
            "options": r.get("options", "Create Page | Skip"),
            "search": r.get("search", ""),
            "source_slug": source_slug,
            "created_at": ts,
            "resolved": False,
        })
        next_id += 1
    qpath.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Convenience — ingest a session by ID
# ---------------------------------------------------------------------------


def ingest_session(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    llm: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Render a conversation as a source and run :func:`ingest_source`."""
    if llm is None:
        from .llm_bridge import build_default_llm
        llm = build_default_llm()
    if llm is None:
        return {"ok": False, "error": "no LLM configured"}
    text, slug, title = render_messages_as_source(messages, session_id=session_id)
    return ingest_source(
        source_text=text,
        source_slug=slug,
        source_title=title,
        llm=llm,
    )
