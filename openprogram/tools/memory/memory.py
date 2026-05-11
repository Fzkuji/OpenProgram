"""Memory tools — talk to the persistent, machine-wide memory subsystem.

This module exposes four tools to the agent:

* ``memory_note``    — record a fact in today's short-term file
* ``memory_recall``  — search wiki + recent short-term, return raw snippets
* ``memory_reflect`` — synthesize a coherent answer across all memories (LLM)
* ``wiki_get``       — fetch a full wiki page by slug or alias

The agent does not directly write wiki pages. Promotion is handled by
the sleep process during background consolidation.
"""
from __future__ import annotations

import json
from typing import Any

from openprogram.memory import index, short_term, wiki
from openprogram.memory.builtin.recall import recall_for_prompt
from openprogram.memory.provider import sanitize_context


# ── memory_note ──────────────────────────────────────────────────────────────

NOTE_NAME = "memory_note"
NOTE_DESC = (
    "Record a fact, observation, preference, or decision in long-term memory. "
    "Use this when you learn something likely to matter in future conversations: "
    "user preferences, environment facts, project conventions, lessons learned. "
    "Skip transient debugging context, file diffs, or one-off questions. The "
    "note is appended to today's short-term file and promoted to the wiki by the "
    "background sleep process when the same fact recurs across sessions."
)

NOTE_SPEC: dict[str, Any] = {
    "name": NOTE_NAME,
    "description": NOTE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "One sentence, factual, atomic, < 200 chars.",
            },
            "type": {
                "type": "string",
                "enum": ["user-pref", "env", "project", "procedure", "fact", "observation"],
                "description": "What kind of fact this is.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 lowercase tags for retrieval.",
            },
            "confidence": {
                "type": "number",
                "description": "0.0-1.0, how confident this is durable.",
            },
        },
        "required": ["text"],
    },
}


def note(
    text: str | None = None,
    type: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    **_: Any,
) -> str:
    text = (text or "").strip()
    if not text:
        return "Error: memory_note requires `text`."
    if len(text) > 400:
        return f"Error: text too long ({len(text)} chars). Keep it under 200."
    kind = (type or "fact").strip()
    tag_list = [str(t).lower() for t in (tags or []) if t][:3]
    conf = max(0.0, min(1.0, float(confidence if confidence is not None else 0.7)))
    short_term.append_text(text, type=kind, tags=tag_list, confidence=conf)
    return f"Noted: ({kind}) {text}"


# ── memory_recall ────────────────────────────────────────────────────────────

RECALL_NAME = "memory_recall"
RECALL_DESC = (
    "Search persistent memory for relevant facts. Returns raw snippets from the "
    "wiki and recent short-term notes ranked by BM25 relevance. Use this when "
    "the user asks about things you might have learned before, or when you need "
    "to ground a response in prior context. For a synthesized answer rather "
    "than raw snippets, use memory_reflect."
)

RECALL_SPEC: dict[str, Any] = {
    "name": RECALL_NAME,
    "description": RECALL_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for. Free-text; multiple keywords OK.",
            },
            "wiki_k": {
                "type": "integer",
                "description": "Max wiki hits to return (default 5).",
            },
            "short_k": {
                "type": "integer",
                "description": "Max short-term hits to return (default 5).",
            },
            "short_days": {
                "type": "integer",
                "description": "Limit short-term search to the last N days (default 30).",
            },
        },
        "required": ["query"],
    },
}


def recall(
    query: str | None = None,
    wiki_k: int | None = None,
    short_k: int | None = None,
    short_days: int | None = None,
    **_: Any,
) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: memory_recall requires `query`."
    text = recall_for_prompt(
        query,
        wiki_k=int(wiki_k) if wiki_k else 5,
        short_k=int(short_k) if short_k else 5,
        short_days=int(short_days) if short_days else 30,
    )
    if not text:
        return f"No memories matched {query!r}."
    return sanitize_context(text)


# ── memory_reflect ───────────────────────────────────────────────────────────

REFLECT_NAME = "memory_reflect"
REFLECT_DESC = (
    "Synthesize a reasoned answer from across all stored memories. Unlike "
    "memory_recall (raw snippets), this collects relevant material and asks "
    "the model to produce a coherent response. More expensive — use only when "
    "raw snippets aren't enough. Common case: cross-cutting questions like "
    "'what does the user typically prefer for X?'."
)

REFLECT_SPEC: dict[str, Any] = {
    "name": REFLECT_NAME,
    "description": REFLECT_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question to reflect on.",
            },
        },
        "required": ["query"],
    },
}


def reflect(query: str | None = None, **_: Any) -> str:
    """Reflect collects 10x more recall than the regular tool and asks the
    caller's LLM to synthesize. The actual LLM call happens in the agent
    runtime that owns this tool — here we just return a structured payload
    the runtime can feed forward."""
    query = (query or "").strip()
    if not query:
        return "Error: memory_reflect requires `query`."
    raw = recall_for_prompt(query, wiki_k=10, short_k=10, short_days=90)
    if not raw:
        return f"No memories to reflect on for {query!r}."
    return (
        f"Reflection sources for {query!r}:\n\n{sanitize_context(raw)}\n\n"
        "(Synthesize a coherent answer from the above. Note conflicts. Cite sources.)"
    )


# ── memory_get ───────────────────────────────────────────────────────────────

# Unified read entry. Resolves a target by shape:
#   - "YYYY-MM-DD"             → short-term file for that day
#   - "entities/claude-max-..." → wiki page at that path
#   - "claude-max-proxy"       → wiki page by alias / slug
# Replaces the old `wiki_get` (kept as alias for back-compat).

GET_NAME = "memory_get"
GET_DESC = (
    "Fetch a memory page. Accepts either a wiki slug "
    "(e.g. 'entities/claude-max-proxy' or just 'claude-max-proxy') or a "
    "short-term date ('YYYY-MM-DD'). Returns the full markdown content. "
    "Use this after `memory_browse` to pull the page you identified."
)

GET_SPEC: dict[str, Any] = {
    "name": GET_NAME,
    "description": GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Wiki slug (e.g. 'claude-max-proxy', 'entities/openprogram') "
                    "or short-term date ('YYYY-MM-DD')."
                ),
            },
        },
        "required": ["target"],
    },
}


_DATE_RE_STR = r"^\d{4}-\d{2}-\d{2}$"


def memory_get(target: str | None = None, **_: Any) -> str:
    import re
    from openprogram.memory import store
    from openprogram.memory.schema import render_wiki_page

    target = (target or "").strip()
    if not target:
        return "Error: memory_get requires `target`."

    # ── Short-term day ────────────────────────────────────────────────
    if re.match(_DATE_RE_STR, target):
        path = store.short_term_for(target)
        if not path.exists():
            return f"No short-term file for {target}."
        return path.read_text(encoding="utf-8")

    # ── Wiki page by explicit path ────────────────────────────────────
    if "/" in target:
        rel = target
        if rel.startswith("wiki/"):
            rel = rel[len("wiki/"):]
        if not rel.endswith(".md"):
            rel = rel + ".md"
        wpath = store.wiki_dir() / rel
        if wpath.exists():
            return wpath.read_text(encoding="utf-8")
        # fall through to alias resolution using last segment
        target = rel.rsplit("/", 1)[-1].removesuffix(".md")

    # ── Wiki page by alias / slug ─────────────────────────────────────
    page = wiki.find(target)
    if page is not None:
        return render_wiki_page(page)

    # ── Last-ditch: raw filename search across wiki ───────────────────
    for p in store.wiki_dir().rglob(f"{target}.md"):
        return p.read_text(encoding="utf-8")
    return f"No memory matches {target!r}. Try `memory_browse` first."


# Back-compat aliases — old code references WIKI_GET_NAME etc.
WIKI_GET_NAME = GET_NAME
WIKI_GET_SPEC = GET_SPEC
wiki_get = memory_get


# ── memory_browse ────────────────────────────────────────────────────────────

# Karpathy / nashsu LLM-Wiki pattern: instead of injecting recall results,
# the agent BROWSES the wiki by reading the index first and then drilling
# into specific pages. This tool returns the index and is the recommended
# starting point whenever the agent suspects long-term knowledge exists.

BROWSE_NAME = "memory_browse"
BROWSE_DESC = (
    "Return the wiki index (`index.md`). Use this as the first step when "
    "you suspect there's relevant long-term knowledge: read the index, "
    "pick the page(s) most likely to help, then use `wiki_get` on each. "
    "Faster and more grounded than `memory_recall` for navigating known "
    "structure; recall is a fallback when you don't know which slugs exist."
)

BROWSE_SPEC: dict[str, Any] = {
    "name": BROWSE_NAME,
    "description": BROWSE_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_browse(**_: Any) -> str:
    """Return the unified memory catalog: wiki index (topic axis) +
    recent short-term days (time axis). One call gives the LLM
    everything it needs to decide what to drill into.
    """
    from openprogram.memory import store, short_term

    parts: list[str] = []

    # --- Wiki index (topic axis) ---------------------------------------
    idx = store.wiki_dir() / "index.md"
    try:
        wiki_idx = idx.read_text(encoding="utf-8").strip()
    except OSError:
        wiki_idx = ""
    parts.append("=== Wiki (topic index) ===\n")
    if wiki_idx:
        parts.append(wiki_idx)
    else:
        parts.append(
            "(empty — nothing ingested yet. Use `memory_note` during "
            "conversation; the next session-end / sleep will ingest.)"
        )
    parts.append("")

    # --- Short-term recent days (time axis) ----------------------------
    parts.append("=== Short-term (recent days) ===\n")
    files = sorted(store.short_term_dir().glob("*.md"))[-14:]
    if not files:
        parts.append("(no short-term notes yet)")
    else:
        for f in reversed(files):  # newest first
            date = f.stem
            try:
                entries = short_term.read_day(date)
            except Exception:
                entries = []
            count = len(entries)
            preview = ""
            if entries:
                first_text = (entries[0].text or "").strip().replace("\n", " ")
                if len(first_text) > 80:
                    first_text = first_text[:77] + "..."
                preview = f" — {first_text}"
            parts.append(f"- {date} ({count} entries){preview}")

    parts.append("")
    parts.append(
        "Read with `memory_get(\"<wiki-slug>\")` (e.g. "
        "`entities/claude-max-proxy`) or `memory_get(\"<YYYY-MM-DD>\")` "
        "for a short-term day."
    )
    return "\n".join(parts)


# ── memory_lint ──────────────────────────────────────────────────────────────

# Health-check the wiki: orphans, broken links, missing concepts, contradictions.
# Returns a structured report; the agent (or the user via the future Memory UI)
# decides what to act on.

LINT_NAME = "memory_lint"
LINT_DESC = (
    "Run a wiki health check. Reports orphan pages, broken `[[wikilinks]]`, "
    "pages with no outbound links, and the pending review queue. Use when "
    "the user asks 'how's my memory looking' or before a deliberate cleanup."
)

LINT_SPEC: dict[str, Any] = {
    "name": LINT_NAME,
    "description": LINT_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_lint(**_: Any) -> str:
    """Run structural lint + return any persisted review items."""
    import json
    import re
    from openprogram.memory import store

    wiki_root = store.wiki_dir()
    md_files: list[tuple[str, str]] = []  # (slug, body)
    for p in wiki_root.rglob("*.md"):
        if p.name in ("SCHEMA.md", "purpose.md"):
            continue
        rel = p.relative_to(wiki_root).with_suffix("")
        try:
            md_files.append((str(rel), p.read_text(encoding="utf-8")))
        except OSError:
            continue
    slug_set = {s.lower() for s, _ in md_files}
    slug_set.update(s.split("/", 1)[-1].lower() for s, _ in md_files)

    wikilink_re = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")
    inbound: dict[str, int] = {}
    outbound: dict[str, int] = {}
    broken: list[tuple[str, str]] = []
    for slug, body in md_files:
        slug_l = slug.lower()
        outbound.setdefault(slug_l, 0)
        for m in wikilink_re.finditer(body):
            target = m.group(1).strip().lower()
            outbound[slug_l] += 1
            inbound[target] = inbound.get(target, 0) + 1
            if target not in slug_set:
                broken.append((slug, target))

    orphans: list[str] = []
    no_outlinks: list[str] = []
    for slug, _ in md_files:
        sl = slug.lower()
        # Skip protocol pages
        if sl in ("index", "log", "overview", "purpose", "schema", "reflections"):
            continue
        if inbound.get(sl, 0) == 0 and inbound.get(sl.split("/", 1)[-1], 0) == 0:
            orphans.append(slug)
        if outbound.get(sl, 0) == 0:
            no_outlinks.append(slug)

    # Pending review items from ingest
    reviews: list[dict] = []
    qpath = store.state_dir() / "review-queue.json"
    if qpath.exists():
        try:
            reviews = [r for r in json.loads(qpath.read_text(encoding="utf-8"))
                       if not r.get("resolved")]
        except Exception:
            reviews = []

    lines = [
        f"# Wiki lint report",
        f"",
        f"Pages: {len(md_files)}",
        f"Orphan pages (no inbound links): {len(orphans)}",
        f"Pages with no outbound links: {len(no_outlinks)}",
        f"Broken `[[wikilinks]]`: {len(broken)}",
        f"Pending review items from ingest: {len(reviews)}",
        f"",
    ]
    if orphans:
        lines.append("## Orphans")
        lines += [f"- `{s}`" for s in orphans[:20]]
        lines.append("")
    if broken:
        lines.append("## Broken wikilinks")
        lines += [f"- `{s}` → `[[{t}]]` (target missing)" for s, t in broken[:20]]
        lines.append("")
    if no_outlinks:
        lines.append("## No outbound links")
        lines += [f"- `{s}`" for s in no_outlinks[:20]]
        lines.append("")
    if reviews:
        lines.append("## Pending reviews (ingest flagged for human judgement)")
        for r in reviews[:20]:
            lines.append(f"- [{r.get('kind')}] {r.get('title','')} (id={r.get('id')})")
        lines.append("")
    if len(md_files) == 0:
        lines.append("(wiki is empty — nothing to lint yet)")
    return "\n".join(lines).rstrip()


# ── memory_ingest ────────────────────────────────────────────────────────────

# Manual trigger for the two-step wiki ingest over the CURRENT session.
# Normally the session_watcher fires this automatically after 30 minutes
# of idle; this tool lets the user say "remember this now" mid-stream.

INGEST_NAME = "memory_ingest"
INGEST_DESC = (
    "Manually run the two-step wiki ingest on the current conversation. "
    "Use only when the user explicitly says 'remember this' or 'consolidate "
    "memory now' — otherwise the background watcher handles it automatically "
    "after the session goes idle. Requires the current session_id."
)

INGEST_SPEC: dict[str, Any] = {
    "name": INGEST_NAME,
    "description": INGEST_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "ID of the session to ingest (current conversation).",
            },
        },
        "required": ["session_id"],
    },
}


def memory_ingest(session_id: str | None = None, **_: Any) -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "Error: memory_ingest requires `session_id`."
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.memory.ingest import ingest_session
        from openprogram.memory.llm_bridge import build_default_llm
    except Exception as e:
        return f"Error: ingest pipeline unavailable ({e})."
    llm = build_default_llm()
    if llm is None:
        return "Error: no LLM configured for ingest (set provider keys)."
    try:
        messages = default_db().get_branch(sid)
    except Exception as e:
        return f"Error: cannot load session {sid!r}: {e}."
    if not messages:
        return f"Session {sid!r} has no messages."
    result = ingest_session(sid, messages, llm=llm)
    if not result.get("ok"):
        return f"Ingest failed: {result.get('error')}"
    return (
        f"Ingest complete: wrote {result.get('n_files')} pages, "
        f"flagged {result.get('n_reviews')} review items."
    )


# ── Compatibility export for tools/__init__.py ───────────────────────────────

__all__ = [
    "NOTE_NAME", "NOTE_SPEC", "note",
    "RECALL_NAME", "RECALL_SPEC", "recall",
    "REFLECT_NAME", "REFLECT_SPEC", "reflect",
    "GET_NAME", "GET_SPEC", "memory_get",
    "WIKI_GET_NAME", "WIKI_GET_SPEC", "wiki_get",  # back-compat alias
    "BROWSE_NAME", "BROWSE_SPEC", "memory_browse",
    "LINT_NAME", "LINT_SPEC", "memory_lint",
    "INGEST_NAME", "INGEST_SPEC", "memory_ingest",
]
