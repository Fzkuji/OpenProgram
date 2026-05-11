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
    GET_NAME, GET_SPEC, memory_get,
    BROWSE_NAME, BROWSE_SPEC, memory_browse,
    LINT_NAME, LINT_SPEC, memory_lint,
    INGEST_NAME, INGEST_SPEC, memory_ingest,
    BACKLINKS_NAME, BACKLINKS_SPEC, memory_backlinks,
)


def _wrap(spec, fn, *, max_chars=20_000):
    return {"spec": spec, "execute": fn, "max_result_size_chars": max_chars}


MEMORY_NOTE = _wrap(NOTE_SPEC, note)
MEMORY_RECALL = _wrap(RECALL_SPEC, recall)
MEMORY_REFLECT = _wrap(REFLECT_SPEC, reflect)
MEMORY_GET = _wrap(GET_SPEC, memory_get, max_chars=30_000)
MEMORY_BROWSE = _wrap(BROWSE_SPEC, memory_browse, max_chars=30_000)
MEMORY_LINT = _wrap(LINT_SPEC, memory_lint, max_chars=15_000)
MEMORY_INGEST = _wrap(INGEST_SPEC, memory_ingest, max_chars=4_000)
MEMORY_BACKLINKS = _wrap(BACKLINKS_SPEC, memory_backlinks, max_chars=20_000)

# Mapping keyed by tool name for direct registration in
# ``openprogram.tools.__init__.ALL_TOOLS``.
ALL: dict[str, dict] = {
    NOTE_NAME: MEMORY_NOTE,
    RECALL_NAME: MEMORY_RECALL,
    REFLECT_NAME: MEMORY_REFLECT,
    GET_NAME: MEMORY_GET,
    BROWSE_NAME: MEMORY_BROWSE,
    LINT_NAME: MEMORY_LINT,
    INGEST_NAME: MEMORY_INGEST,
    BACKLINKS_NAME: MEMORY_BACKLINKS,
}

__all__ = [
    "NOTE_NAME", "RECALL_NAME", "REFLECT_NAME", "GET_NAME",
    "BROWSE_NAME", "LINT_NAME", "INGEST_NAME",
    "MEMORY_NOTE", "MEMORY_RECALL", "MEMORY_REFLECT", "MEMORY_GET",
    "MEMORY_BROWSE", "MEMORY_LINT", "MEMORY_INGEST", "MEMORY_BACKLINKS",
    "BACKLINKS_NAME",
    "ALL",
]
