"""Memory tools — agent-facing entry points to the persistent vault.

Seven tools:

  WRITE:
    memory_note     record a single observation into today's journal log

  READ:
    memory_browse   unified catalog (wiki folder tree + recent days)
    memory_get      fetch a wiki page (by filename) or journal day (YYYY-MM-DD)
    memory_recall   keyword FTS over the whole memory store
    memory_reflect  multi-page LLM synthesis

  ADMIN:
    memory_ingest   manual consolidation of the current session
    memory_lint     wiki structural health report
"""
from __future__ import annotations

import re
from typing import Any

from openprogram.memory import journal, store, wiki
from openprogram.memory.builtin.recall import recall_for_prompt
from openprogram.memory.provider import sanitize_context


# ── memory_note ──────────────────────────────────────────────────────────────

NOTE_NAME = "memory_note"
NOTE_DESC = (
    "Record a fact, preference, decision, or lesson in long-term memory. "
    "Use when you learn something likely to matter in future conversations. "
    "Appended to today's journal file; the next session-end / sleep "
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
    journal.append_text(text, type=kind, tags=tag_list, confidence=conf)
    return f"Noted: ({kind}) {text}"


# ── memory_browse ────────────────────────────────────────────────────────────

BROWSE_NAME = "memory_browse"
BROWSE_DESC = (
    "Return the unified memory catalog: wiki folder tree (topic axis) + "
    "recent journal days (time axis). Read this first; then "
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
    files = sorted(store.journal_dir().glob("*.md"))[-14:]
    if not files:
        parts.append("(no journal notes yet)")
    else:
        for f in reversed(files):
            date = f.stem
            try:
                entries = journal.read_day(date)
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
        "`memory_get \"<YYYY-MM-DD>\"` reads a journal day."
    )
    return "\n".join(parts)


# ── memory_get ───────────────────────────────────────────────────────────────

GET_NAME = "memory_get"
GET_DESC = (
    "Fetch a memory page. Accepts a wiki page filename (e.g. "
    "'Claude Max Proxy', case-insensitive) or an ISO date "
    "('YYYY-MM-DD') for a journal day."
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
        path = store.journal_for(target)
        if not path.exists():
            return f"No journal file for {target}."
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
    from openprogram.memory.wiki.ingest import ingest_session_by_id
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
    from openprogram.memory.wiki import ops as wiki_ops
    return wiki_ops.lint()


# ── memory_rename ────────────────────────────────────────────────────────────

RENAME_NAME = "memory_rename"
RENAME_DESC = (
    "Rename a wiki page (filename stem). Moves the file/folder AND "
    "rewrites every `[[old]]` → `[[new]]` across the vault. Updates "
    "the link index. Use this — never `mv` directly."
)

RENAME_SPEC: dict[str, Any] = {
    "name": RENAME_NAME, "description": RENAME_DESC,
    "parameters": {
        "type": "object",
        "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
        "required": ["old", "new"],
    },
}


def memory_rename(old: str | None = None, new: str | None = None, **_: Any) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new:
        return "Error: memory_rename requires both `old` and `new`."
    from openprogram.memory.wiki import ops as wiki_ops
    r = wiki_ops.rename(old, new)
    if not r.get("ok"):
        return f"Rename failed: {r.get('error')}"
    return f"Renamed [[{old}]] → [[{new}]]; {r.get('rewrites', 0)} pages updated."


# ── memory_relink ────────────────────────────────────────────────────────────

RELINK_NAME = "memory_relink"
RELINK_DESC = (
    "Cascade-rewrite `[[old]]` → `[[new]]` across the vault WITHOUT "
    "moving any file. Use when a page was renamed externally and "
    "wikilinks to it are broken."
)

RELINK_SPEC: dict[str, Any] = {
    "name": RELINK_NAME, "description": RELINK_DESC,
    "parameters": {
        "type": "object",
        "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
        "required": ["old", "new"],
    },
}


def memory_relink(old: str | None = None, new: str | None = None, **_: Any) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new:
        return "Error: memory_relink requires both `old` and `new`."
    from openprogram.memory.wiki import ops as wiki_ops
    r = wiki_ops.relink(old, new)
    return f"Relinked [[{old}]] → [[{new}]] in {r.get('rewrites', 0)} pages."


# ── memory_delete ────────────────────────────────────────────────────────────

DELETE_NAME = "memory_delete"
DELETE_DESC = (
    "Delete a wiki page (leaf or empty topic folder). Strips every "
    "`[[name]]` reference into plain text. Refuses to delete a topic "
    "that still has subtopic children."
)

DELETE_SPEC: dict[str, Any] = {
    "name": DELETE_NAME, "description": DELETE_DESC,
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
}


def memory_delete(name: str | None = None, **_: Any) -> str:
    name = (name or "").strip()
    if not name:
        return "Error: memory_delete requires `name`."
    from openprogram.memory.wiki import ops as wiki_ops
    r = wiki_ops.delete_page(name)
    if not r.get("ok"):
        return f"Delete failed: {r.get('error')}"
    return (
        f"Deleted {r.get('deleted')}; stripped {r.get('refs_stripped', 0)} references."
    )


# ── memory_review ────────────────────────────────────────────────────────────

REVIEW_NAME = "memory_review"
REVIEW_DESC = (
    "Manage the review queue. No args: list pending items "
    "(contradictions / duplicates / missing pages / suggestions). "
    "With `resolve_id` + `action`: mark an item resolved."
)

REVIEW_SPEC: dict[str, Any] = {
    "name": REVIEW_NAME, "description": REVIEW_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "resolve_id": {"type": "integer"},
            "action": {"type": "string"},
            "note": {"type": "string"},
        },
    },
}


def memory_review(
    resolve_id: int | None = None,
    action: str | None = None,
    note: str | None = None,
    **_: Any,
) -> str:
    from openprogram.memory.wiki import ops as wiki_ops
    if resolve_id is not None:
        r = wiki_ops.review_resolve(int(resolve_id), action=(action or "ack"), note=(note or ""))
        return r.get("error") if not r.get("ok") else f"Marked #{resolve_id} resolved ({action or 'ack'})."
    items = wiki_ops.review_list(only_pending=True)
    if not items:
        return "Review queue is empty."
    lines = [f"# Review queue ({len(items)} pending)", ""]
    for it in items[:30]:
        lines.append(f"## #{it.get('id')} [{it.get('kind')}] {it.get('title','')}")
        if it.get("detail"):
            lines.append(it["detail"])
        lines.append(f"_source: {it.get('source_slug','')} | created: {it.get('created_at','')}_")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── memory_status ────────────────────────────────────────────────────────────

STATUS_NAME = "memory_status"
STATUS_DESC = (
    "Snapshot of the memory vault — page count by type, FTS rows, "
    "pending reviews, last reindex, vault root."
)

STATUS_SPEC: dict[str, Any] = {
    "name": STATUS_NAME, "description": STATUS_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_status(**_: Any) -> str:
    from openprogram.memory.wiki import ops as wiki_ops
    s = wiki_ops.stats()
    lines = [
        "# Memory status", "",
        f"Vault: `{s.get('vault_root')}`",
        f"Total pages: **{s.get('pages_total', 0)}**",
        f"FTS rows: wiki={s.get('fts_wiki_rows', 0)} journal={s.get('fts_short_rows', 0)}",
        f"Pending reviews: **{s.get('pending_reviews', 0)}**",
        f"Last reindex: {s.get('last_reindex') or '(never)'}",
        "", "## Pages by type",
    ]
    by_type = s.get("pages_by_type", {})
    if by_type:
        for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{t}`: {n}")
    else:
        lines.append("- (none)")
    return "\n".join(lines)


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
    from openprogram.memory.wiki import ops as wiki_ops
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
    "RENAME_NAME", "RENAME_SPEC", "memory_rename",
    "RELINK_NAME", "RELINK_SPEC", "memory_relink",
    "DELETE_NAME", "DELETE_SPEC", "memory_delete",
    "REVIEW_NAME", "REVIEW_SPEC", "memory_review",
    "STATUS_NAME", "STATUS_SPEC", "memory_status",
]
