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


# ── wiki_get ─────────────────────────────────────────────────────────────────

WIKI_GET_NAME = "wiki_get"
WIKI_GET_DESC = (
    "Fetch a complete wiki page by slug or alias. Returns the full markdown "
    "including frontmatter (claims, sources, confidence). Use this after "
    "memory_recall surfaces a relevant page and you need the full context, "
    "or when you know the slug from prior interactions."
)

WIKI_GET_SPEC: dict[str, Any] = {
    "name": WIKI_GET_NAME,
    "description": WIKI_GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Page slug or alias (e.g. 'openprogram', 'op').",
            },
            "kind": {
                "type": "string",
                "enum": ["entities", "concepts", "procedures", "user"],
                "description": "Optional kind to disambiguate.",
            },
        },
        "required": ["slug"],
    },
}


def wiki_get(
    slug: str | None = None,
    kind: str | None = None,
    **_: Any,
) -> str:
    slug = (slug or "").strip()
    if not slug:
        return "Error: wiki_get requires `slug`."
    page = wiki.get(kind, slug) if kind else None
    if page is None:
        page = wiki.find(slug)
    if page is None:
        return f"No wiki page matches {slug!r}."
    from openprogram.memory.schema import render_wiki_page
    return render_wiki_page(page)


# ── Compatibility export for tools/__init__.py ───────────────────────────────

# Keep the original ``NAME`` / ``DESCRIPTION`` / ``SPEC`` / ``execute``
# triple — older registry code still expects a single tool. We treat the
# four tools as a tool *bundle* exported from ``__init__.py``; this file
# itself just defines them.

__all__ = [
    "NOTE_NAME", "NOTE_SPEC", "note",
    "RECALL_NAME", "RECALL_SPEC", "recall",
    "REFLECT_NAME", "REFLECT_SPEC", "reflect",
    "WIKI_GET_NAME", "WIKI_GET_SPEC", "wiki_get",
]
