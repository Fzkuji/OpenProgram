"""Memory tools — agent-facing entry points to the persistent vault.

Seven tools:

  WRITE:
    memory_note     record a single observation into today's short-term log

  READ:
    memory_browse   unified catalog (wiki folder tree + recent days)
    memory_get      fetch a wiki page (by filename) or short-term day (YYYY-MM-DD)
    memory_recall   keyword FTS over the whole memory store
    memory_reflect  multi-page LLM synthesis

  ADMIN:
    memory_ingest   manual consolidation of the current session
    memory_lint     wiki structural health report
"""
from __future__ import annotations

import re
from typing import Any

from openprogram.memory import short_term, store, wiki
from openprogram.memory.builtin.recall import recall_for_prompt
from openprogram.memory.provider import sanitize_context


# ── memory_note ──────────────────────────────────────────────────────────────

NOTE_NAME = "memory_note"
NOTE_DESC = (
    "Record a fact, preference, decision, or lesson in long-term memory. "
    "Use when you learn something likely to matter in future conversations. "
    "Appended to today's short-term file; the next session-end / sleep "
    "folds it into the wiki."
)

NOTE_SPEC: dict[str, Any] = {
    "name": NOTE_NAME, "description": NOTE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "One factual sentence, <200 chars."},
            "type": {
                "type": "string",
                "enum": ["user-pref", "env", "project", "procedure", "fact", "observation"],
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
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


# ── memory_browse ────────────────────────────────────────────────────────────

BROWSE_NAME = "memory_browse"
BROWSE_DESC = (
    "Return the unified memory catalog: wiki folder tree (topic axis) + "
    "recent short-term days (time axis). Read this first; then "
    "`memory_get <name>` on the pages or days that look relevant."
)

BROWSE_SPEC: dict[str, Any] = {
    "name": BROWSE_NAME, "description": BROWSE_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_browse(**_: Any) -> str:
    parts: list[str] = ["=== Wiki (folder tree) ===", ""]
    tree = wiki.tree(max_depth=6).strip()
    parts.append(tree or "(empty — use `memory_note` or `memory_ingest`.)")
    parts.append("")
    parts.append("=== Short-term (recent days) ===")
    parts.append("")
    files = sorted(store.short_term_dir().glob("*.md"))[-14:]
    if not files:
        parts.append("(no short-term notes yet)")
    else:
        for f in reversed(files):
            date = f.stem
            try:
                entries = short_term.read_day(date)
            except Exception:
                entries = []
            preview = ""
            if entries:
                first = entries[0].text.strip().replace("\n", " ")
                if len(first) > 80:
                    first = first[:77] + "..."
                preview = f" — {first}"
            parts.append(f"- {date} ({len(entries)} entries){preview}")
    parts.append("")
    parts.append(
        "`memory_get \"<page filename>\"` reads a wiki page; "
        "`memory_get \"<YYYY-MM-DD>\"` reads a short-term day."
    )
    return "\n".join(parts)


# ── memory_get ───────────────────────────────────────────────────────────────

GET_NAME = "memory_get"
GET_DESC = (
    "Fetch a memory page. Accepts a wiki page filename (e.g. "
    "'Claude Max Proxy', case-insensitive) or an ISO date "
    "('YYYY-MM-DD') for a short-term day."
)

GET_SPEC: dict[str, Any] = {
    "name": GET_NAME, "description": GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Wiki page name or YYYY-MM-DD."},
        },
        "required": ["target"],
    },
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def memory_get(target: str | None = None, **_: Any) -> str:
    target = (target or "").strip()
    if not target:
        return "Error: memory_get requires `target`."
    if _DATE_RE.match(target):
        path = store.short_term_for(target)
        if not path.exists():
            return f"No short-term file for {target}."
        return path.read_text(encoding="utf-8")
    content = wiki.read(target)
    if content is None:
        return f"No memory matches {target!r}. Try `memory_browse` first."
    return content


# ── memory_recall ────────────────────────────────────────────────────────────

RECALL_NAME = "memory_recall"
RECALL_DESC = (
    "Keyword FTS over the whole memory store. Returns ranked snippets. "
    "Use as a fallback when you don't know which wiki page to read."
)

RECALL_SPEC: dict[str, Any] = {
    "name": RECALL_NAME, "description": RECALL_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "wiki_k": {"type": "integer"},
            "short_k": {"type": "integer"},
            "short_days": {"type": "integer"},
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
    return sanitize_context(text) if text else f"No memories matched {query!r}."


# ── memory_reflect ───────────────────────────────────────────────────────────

REFLECT_NAME = "memory_reflect"
REFLECT_DESC = (
    "Collect cross-cutting recall snippets and ask the model to synthesise. "
    "More expensive than memory_recall — use only when raw snippets aren't enough."
)

REFLECT_SPEC: dict[str, Any] = {
    "name": REFLECT_NAME, "description": REFLECT_DESC,
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}


def reflect(query: str | None = None, **_: Any) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: memory_reflect requires `query`."
    raw = recall_for_prompt(query, wiki_k=10, short_k=10, short_days=90)
    if not raw:
        return f"No memories to reflect on for {query!r}."
    return (
        f"Reflection sources for {query!r}:\n\n{sanitize_context(raw)}\n\n"
        "(Synthesize a coherent answer. Note conflicts. Cite [[wikilinks]].)"
    )


# ── memory_ingest ────────────────────────────────────────────────────────────

INGEST_NAME = "memory_ingest"
INGEST_DESC = (
    "Manually consolidate the current conversation into the wiki via the "
    "two-step agentic ingest pipeline. Use only when the user explicitly "
    "says 'remember this' — session_watcher fires it automatically after "
    "30 minutes of idle."
)

INGEST_SPEC: dict[str, Any] = {
    "name": INGEST_NAME, "description": INGEST_DESC,
    "parameters": {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    },
}


def memory_ingest(session_id: str | None = None, **_: Any) -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "Error: memory_ingest requires `session_id`."
    from openprogram.memory.ingest import ingest_session_by_id
    result = ingest_session_by_id(sid)
    if not result.get("ok"):
        return f"Ingest failed: {result.get('error')}"
    report = result.get("report") or "Ingest complete (no report)."
    commit = result.get("commit") or {}
    if commit.get("committed"):
        report += f"\n\n[git commit {commit.get('hash')}]"
    return report


# ── memory_lint ──────────────────────────────────────────────────────────────

LINT_NAME = "memory_lint"
LINT_DESC = (
    "Wiki health check. Reports missing/unknown `type:`, folder-stem "
    "mismatches, broken `[[wikilinks]]`, orphans, refactor candidates."
)

LINT_SPEC: dict[str, Any] = {
    "name": LINT_NAME, "description": LINT_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_lint(**_: Any) -> str:
    from openprogram.memory import wiki_ops
    return wiki_ops.lint()


# ── memory_backlinks ─────────────────────────────────────────────────────────

BACKLINKS_NAME = "memory_backlinks"
BACKLINKS_DESC = (
    "List every wiki page that has a `[[wikilink]]` to the given page. "
    "Obsidian's backlinks panel in tool form — useful for 'what mentions X?'"
)

BACKLINKS_SPEC: dict[str, Any] = {
    "name": BACKLINKS_NAME, "description": BACKLINKS_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Wiki page filename stem."},
        },
        "required": ["name"],
    },
}


def memory_backlinks(name: str | None = None, **_: Any) -> str:
    name = (name or "").strip()
    if not name:
        return "Error: memory_backlinks requires `name`."
    from openprogram.memory import wiki_ops
    hits = wiki_ops.backlinks(name)
    if not hits:
        return f"No pages link to [[{name}]]."
    lines = [f"# Backlinks to [[{name}]] ({len(hits)} pages)", ""]
    for h in hits:
        lines.append(f"## `{h['page']}`")
        lines.append(h['snippet'])
        lines.append("")
    return "\n".join(lines).rstrip()


# Back-compat alias
WIKI_GET_NAME = GET_NAME
WIKI_GET_SPEC = GET_SPEC
wiki_get = memory_get


__all__ = [
    "NOTE_NAME", "NOTE_SPEC", "note",
    "RECALL_NAME", "RECALL_SPEC", "recall",
    "REFLECT_NAME", "REFLECT_SPEC", "reflect",
    "GET_NAME", "GET_SPEC", "memory_get",
    "WIKI_GET_NAME", "WIKI_GET_SPEC", "wiki_get",
    "BROWSE_NAME", "BROWSE_SPEC", "memory_browse",
    "LINT_NAME", "LINT_SPEC", "memory_lint",
    "INGEST_NAME", "INGEST_SPEC", "memory_ingest",
    "BACKLINKS_NAME", "BACKLINKS_SPEC", "memory_backlinks",
]
