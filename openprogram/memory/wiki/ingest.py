"""Two-step wiki ingest — analyse, then write (agentic).

Hybrid design pulling from both upstream sources:

  * Two-step analyse → generate flow (nashsu/llm_wiki).
  * Agentic write step using standard ``read``/``write``/``edit``
    tools rather than a custom FILE block protocol (Research-Agent-
    Harness). The agent reads ``AGENTS.md`` and ``SCHEMA.md`` from
    the vault on every run, browses the folder tree, then writes /
    edits pages in place.
  * Post-write ``enrich`` pass that adds ``[[wikilinks]]`` via a
    JSON substitution prompt (nashsu).
  * Per-ingest git commit (RAH).
  * REVIEW-queue side-channel for human-judgement items (nashsu).

Pipeline:

  1. Python collects: conversation transcript + today's journal
     notes + folder tree + AGENTS/SCHEMA/purpose/index + existing
     wiki page bodies for context.
  2. Step 1 — Analysis: LLM emits structured analysis (key entities /
     concepts / arguments / contradictions / recommendations).
  3. Step 2 — Generation: an agentic ``runtime.exec`` call hands the
     analysis to an agent with the file toolset; the agent reads
     existing pages, writes new ones, edits index/log/overview,
     returns a short markdown report.
  4. Python runs the enrich pass over the modified pages, then
     ``git_commit`` to snapshot.
  5. Any REVIEW items the agent flagged get persisted to
     ``.state/review-queue.json``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .. import store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


ANALYSIS_PROMPT = """\
You are an expert wiki analyst. Read the source below and produce a
structured analysis. Reason internally; output only the final analysis.

## Key Entities
List named things mentioned. For each: name, role, whether likely
already in the wiki (check the index).

## Key Concepts
Abstract ideas / techniques. For each: name, one-line definition,
why it matters here.

## Procedures / How-tos
Step-by-step workflows the source describes.

## User-facing facts
Preferences, dislikes, role, communication style — anything about
the user themselves.

## Main Arguments & Findings
Core claims, decisions, lessons learned. Evidence strength.

## Connections to Existing Wiki
Which existing pages does this relate to? Which to extend?

## Contradictions & Tensions
Does anything conflict with existing wiki content?

## Recommendations
What pages to create / update. Suggested folder placement.
What to flag for human review (contradictions / duplicates / gaps).

Be concise. Focus on what's genuinely durable.

---

## Wiki purpose (governs scope)
{purpose}

---

## Current wiki index
{index}

---

## Current folder tree
{tree}

---

## Source: {source_title}

{source}
"""


GENERATION_INSTRUCTIONS = """\
You are the wiki-maintainer agent. Apply the analysis below to the
wiki at the vault root.

Vault root: {vault_root}
Source slug: {source_slug}
Today: {today}

═══════════════════════════════════════════════════════════════
READ FIRST
═══════════════════════════════════════════════════════════════

1. `{vault_root}/AGENTS.md` — your governance.
2. `{vault_root}/SCHEMA.md` — the page schema (7 type values,
   frontmatter format, folder-hierarchy rules, wikilink syntax).
3. `{vault_root}/purpose.md` — scope rules.
4. `{vault_root}/index.md` — what already exists.

═══════════════════════════════════════════════════════════════
WHAT TO DO
═══════════════════════════════════════════════════════════════

For each piece of durable knowledge in the analysis:

* Decide its `type:` (entity / concept / procedure / user / source
  / query / synthesis).
* Decide its folder location. Extend the existing tree — don't
  start parallel branches for the same theme.
* If a relevant page exists, READ it, then EDIT to merge the new
  content into existing prose (don't dump a bullet list at the
  bottom).
* If no relevant page exists, WRITE a new page. Use folder form
  (`<Name>/<Name>.md`) when the topic will plausibly have children;
  bare leaf (`<Name>.md`) otherwise.
* Frontmatter must include `type:`. Add `sources: ["{source_slug}"]`
  and `related: [...]` (bare filenames) when meaningful.
* Body: Wikipedia-style prose with `[[wikilinks]]`. No YAML claim lists.

Then maintain bookkeeping:

* Update `{vault_root}/index.md` — preserve every existing line, add
  new pages to their `type:` section.
* Append one entry to `{vault_root}/log.md`:
      ## [{today}] ingest | {source_title}
      - <one line per page changed>
* Update `{vault_root}/overview.md` — 2-4 paragraph TL;DR of the wiki.
* OPTIONAL: write a session-summary page at
  `{vault_root}/Sessions/{source_slug}.md` with
  `type: source` + `session_id` + `date` frontmatter, only if the
  conversation has lasting reference value.

═══════════════════════════════════════════════════════════════
REVIEW QUEUE (optional, end of output)
═══════════════════════════════════════════════════════════════

If you noticed things that need human judgement, append a JSON
block at the END of your reply (after the report), wrapped in
exactly these markers:

<<<REVIEW_QUEUE>>>
[
  {{"kind": "contradiction", "title": "...", "detail": "..."}},
  {{"kind": "duplicate",     "title": "...", "detail": "..."}},
  {{"kind": "missing-page",  "title": "...", "detail": "..."}},
  {{"kind": "suggestion",    "title": "...", "detail": "..."}}
]
<<<END>>>

Empty list / no block = no review items. Don't make trivial reviews.

═══════════════════════════════════════════════════════════════
RETURN
═══════════════════════════════════════════════════════════════

A short markdown report — which pages you created, which you
updated, the why behind each. No long preamble.

═══════════════════════════════════════════════════════════════
ANALYSIS (apply this to the wiki)
═══════════════════════════════════════════════════════════════

{analysis}

═══════════════════════════════════════════════════════════════
SOURCE (for reference; don't re-quote it)
═══════════════════════════════════════════════════════════════

{source}
"""


REVIEW_BLOCK_RE = re.compile(
    r"<<<REVIEW_QUEUE>>>\s*(?P<json>\[.*?\])\s*<<<END>>>",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Source rendering
# ---------------------------------------------------------------------------


def _render_conversation(
    messages: Iterable[dict[str, Any]], *, max_chars: int = 16_000,
) -> str:
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
    return text


def _read_journal_for(date_iso: str) -> str:
    path = store.journal_for(date_iso)
    if not path.exists():
        return "(no journal notes today)"
    body = path.read_text(encoding="utf-8").strip()
    return body or "(empty)"


def _session_slug(session_id: str, today: str) -> str:
    short = session_id.replace("local_", "")[:10]
    return f"session-{short}-{today}"


def _read_or_default(path: Path, default: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


# ---------------------------------------------------------------------------
# Runtime + LLM bridges
# ---------------------------------------------------------------------------


def _build_runtime() -> Any | None:
    try:
        from openprogram.agents.runtime_registry import _build_autodetect
    except Exception:
        return None
    try:
        return _build_autodetect()
    except Exception as e:  # noqa: BLE001
        logger.warning("memory ingest: no runtime available (%s)", e)
        return None


def _llm_callable_from_runtime(runtime: Any):
    """Return a ``(system, user) -> str`` callable backed by ``runtime``.

    Used by the analysis step (no tools needed — we just want text)
    and by ``enrich`` which expects this shape.
    """
    def _call(system: str, user: str) -> str:
        content = []
        if system:
            content.append({"type": "text", "text": system})
        content.append({"type": "text", "text": user})
        return runtime.exec(content=content, tools=[], max_iterations=1)
    return _call


# ---------------------------------------------------------------------------
# Main entrypoints
# ---------------------------------------------------------------------------


def ingest_session(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    runtime: Any | None = None,
) -> dict[str, Any]:
    """Run two-step ingest over a finished conversation.

    Returns ``{ok, report, n_review_items, commit_hash, enrich, error}``.
    """
    runtime = runtime or _build_runtime()
    if runtime is None:
        return {"ok": False, "error": "no runtime configured for ingest"}

    today = datetime.now().strftime("%Y-%m-%d")
    vault_root = store.wiki_dir()
    slug = _session_slug(session_id, today)

    source = _render_conversation(messages)
    journal = _read_journal_for(today)
    if journal and journal != "(no journal notes today)":
        source = (
            source
            + f"\n\n---\n\n## Short-term notes from {today}\n\n"
            + journal
        )

    purpose = _read_or_default(vault_root / "purpose.md", "(no purpose)")
    index = _read_or_default(vault_root / "index.md", "(empty index)")
    from .helpers import folder_tree
    tree = folder_tree(vault_root) or "(empty vault)"
    source_title = f"Session {session_id} ({today})"

    # ── Step 1: analysis ────────────────────────────────────────────────
    analysis_prompt = ANALYSIS_PROMPT.format(
        purpose=purpose,
        index=index,
        tree=tree,
        source=source,
        source_title=source_title,
    )
    try:
        analysis = runtime.exec(
            content=[{"type": "text", "text": analysis_prompt}],
            tools=[],
            max_iterations=1,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"analysis: {e}"}
    if not analysis or not analysis.strip():
        return {"ok": False, "error": "analysis returned empty"}

    # ── Step 2: agentic write ───────────────────────────────────────────
    gen_prompt = GENERATION_INSTRUCTIONS.format(
        vault_root=str(vault_root),
        source_slug=slug,
        source_title=source_title,
        today=today,
        analysis=analysis,
        source=source,
    )
    try:
        report = runtime.exec(
            content=[{"type": "text", "text": gen_prompt}],
            max_iterations=40,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"generation: {e}"}

    # ── Parse REVIEW queue ──────────────────────────────────────────────
    review_items = _parse_review_block(report)
    if review_items:
        _persist_reviews(review_items, source_slug=slug, ts=today)

    # ── Enrich wikilinks (two directions) ────────────────────────────────
    # We don't know exactly which pages changed (agent decided), so
    # use `git status` against the vault repo to find them. Then:
    #   * outbound: for every touched page, scan its body for plain-
    #     text mentions of EXISTING wiki pages and add `[[wikilink]]`s
    #   * inbound:  for every NEWLY-CREATED page (status "??"), scan
    #     the rest of the vault for unlinked mentions of it and
    #     convert validated ones to `[[name]]`. Without this pass new
    #     pages stay link-orphans.
    enrich_stats: dict[str, Any] = {"skipped": True}
    try:
        from . import enrich
        touched, created = _git_touched_pages(vault_root)
        if touched:
            llm = _llm_callable_from_runtime(runtime)
            out_stats = enrich.enrich_pages(touched, llm=llm)

            inbound_pages = 0
            inbound_links = 0
            for new_page in created:
                r = enrich.enrich_inbound_for_new_page(new_page, llm=llm)
                if r.get("ok"):
                    inbound_links += int(r.get("linked", 0) or 0)
                    inbound_pages += int(r.get("pages_changed", 0) or 0)

            enrich_stats = {
                "outbound_pages_changed": out_stats.get("pages_changed", 0),
                "outbound_links_added": out_stats.get("links_added", 0),
                "inbound_pages_changed": inbound_pages,
                "inbound_links_added": inbound_links,
                "new_pages_processed": len(created),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("enrich pass failed (non-fatal): %s", e)
        enrich_stats = {"error": str(e)}

    # ── Git commit ──────────────────────────────────────────────────────
    commit_info: dict[str, Any] = {}
    try:
        from . import ops as wiki_ops
        commit_info = wiki_ops.git_commit(f"ingest: {slug}")
    except Exception as e:  # noqa: BLE001
        commit_info = {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "report": report,
        "n_review_items": len(review_items),
        "enrich": enrich_stats,
        "commit": commit_info,
    }


def ingest_session_by_id(session_id: str) -> dict[str, Any]:
    try:
        from openprogram.agent.session_db import default_db
    except Exception as e:
        return {"ok": False, "error": f"session_db unavailable: {e}"}
    try:
        messages = default_db().get_branch(session_id)
    except Exception as e:
        return {"ok": False, "error": f"load session: {e}"}
    if not messages:
        return {"ok": False, "error": "session has no messages"}
    return ingest_session(session_id, messages)


# ---------------------------------------------------------------------------
# REVIEW queue
# ---------------------------------------------------------------------------


def _parse_review_block(report: str) -> list[dict[str, str]]:
    m = REVIEW_BLOCK_RE.search(report or "")
    if not m:
        return []
    try:
        items = json.loads(m.group("json"))
    except json.JSONDecodeError:
        return []
    out: list[dict[str, str]] = []
    if not isinstance(items, list):
        return []
    for it in items:
        if isinstance(it, dict) and "kind" in it and "title" in it:
            out.append({
                "kind": str(it.get("kind", "")),
                "title": str(it.get("title", "")),
                "detail": str(it.get("detail", "")),
            })
    return out


def _persist_reviews(reviews: list[dict], *, source_slug: str, ts: str) -> None:
    qpath = store.review_queue_path()
    qpath.parent.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    if qpath.exists():
        try:
            items = json.loads(qpath.read_text(encoding="utf-8"))
        except Exception:
            items = []
    next_id = max((it.get("id", 0) for it in items), default=0) + 1
    for r in reviews:
        items.append({
            "id": next_id,
            "kind": r.get("kind"),
            "title": r.get("title", ""),
            "detail": r.get("detail", ""),
            "source_slug": source_slug,
            "created_at": ts,
            "resolved": False,
        })
        next_id += 1
    qpath.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Git helpers (find pages the agent just touched)
# ---------------------------------------------------------------------------


def _git_touched_pages(root: Path) -> tuple[list[Path], list[Path]]:
    """Return ``(all_touched, newly_created)`` content pages since last commit.

    * ``all_touched`` — every modified or new ``.md`` file (governance
      docs excluded). Used for outbound enrichment.
    * ``newly_created`` — subset whose git status is ``??`` (untracked).
      Used for inbound enrichment (we only scan inbound for genuinely
      new pages so we don't re-process edits to existing ones).
    """
    import subprocess
    if not (root / ".git").exists():
        return [], []
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True, capture_output=True, timeout=15, text=True,
        ).stdout
    except Exception:
        return [], []
    touched: list[Path] = []
    created: list[Path] = []
    skip_names = set(store.GOVERNANCE_PAGES)
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        rel = line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if not rel.endswith(".md"):
            continue
        p = root / rel
        if p.name in skip_names:
            continue
        if not p.exists():
            continue
        touched.append(p)
        if status.strip() == "??":  # untracked = new file
            created.append(p)
    return touched, created
