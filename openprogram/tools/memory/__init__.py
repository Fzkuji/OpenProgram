"""Memory tools — bundle of four agent tools.

The persistent memory subsystem (``openprogram.memory``) is what the
agent actually talks to. This package wraps it in tool entrypoints
that match the rest of ``openprogram.tools`` (``TOOL`` dict per name,
``ALL`` map for batch registration).

Tools exposed:

  memory_note     — record a fact in short-term
  memory_recall   — search wiki + recent short-term, raw snippets
  memory_reflect  — collect cross-cutting recall for LLM synthesis
  wiki_get        — fetch a complete wiki page by slug
"""
from .memory import (
    NOTE_NAME, NOTE_SPEC, note,
    RECALL_NAME, RECALL_SPEC, recall,
    REFLECT_NAME, REFLECT_SPEC, reflect,
    WIKI_GET_NAME, WIKI_GET_SPEC, wiki_get,
)


def _wrap(spec, fn, *, max_chars=20_000):
    return {"spec": spec, "execute": fn, "max_result_size_chars": max_chars}


MEMORY_NOTE = _wrap(NOTE_SPEC, note)
MEMORY_RECALL = _wrap(RECALL_SPEC, recall)
MEMORY_REFLECT = _wrap(REFLECT_SPEC, reflect)
WIKI_GET = _wrap(WIKI_GET_SPEC, wiki_get, max_chars=30_000)

# Mapping keyed by tool name for direct registration in
# ``openprogram.tools.__init__.ALL_TOOLS``.
ALL: dict[str, dict] = {
    NOTE_NAME: MEMORY_NOTE,
    RECALL_NAME: MEMORY_RECALL,
    REFLECT_NAME: MEMORY_REFLECT,
    WIKI_GET_NAME: WIKI_GET,
}

__all__ = [
    "NOTE_NAME", "RECALL_NAME", "REFLECT_NAME", "WIKI_GET_NAME",
    "MEMORY_NOTE", "MEMORY_RECALL", "MEMORY_REFLECT", "WIKI_GET",
    "ALL",
]
