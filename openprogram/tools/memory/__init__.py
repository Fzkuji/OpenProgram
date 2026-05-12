"""Memory tools — agent tool bundle for the wiki-backed memory subsystem.

Tools exposed:

  memory_note      — record a fact in journal
  memory_recall    — search wiki + recent journal, raw snippets
  memory_reflect   — collect cross-cutting recall for LLM synthesis
  memory_get       — fetch a complete wiki page by slug
  memory_browse    — render the wiki folder tree
  memory_lint      — structural health report
  memory_ingest    — two-step agentic conversation ingest
  memory_backlinks — inbound references to a page (Obsidian-style)
  memory_rename    — move a page + cascade-rewrite all wikilinks
  memory_relink    — cascade-rewrite wikilinks only (no file move)
  memory_delete    — remove a page + optionally prune dangling refs
  memory_review    — list or resolve REVIEW-queue items
  memory_status    — vault stats (page counts, pending reviews, etc.)
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
    RENAME_NAME, RENAME_SPEC, memory_rename,
    RELINK_NAME, RELINK_SPEC, memory_relink,
    DELETE_NAME, DELETE_SPEC, memory_delete,
    REVIEW_NAME, REVIEW_SPEC, memory_review,
    STATUS_NAME, STATUS_SPEC, memory_status,
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
MEMORY_RENAME = _wrap(RENAME_SPEC, memory_rename, max_chars=8_000)
MEMORY_RELINK = _wrap(RELINK_SPEC, memory_relink, max_chars=8_000)
MEMORY_DELETE = _wrap(DELETE_SPEC, memory_delete, max_chars=8_000)
MEMORY_REVIEW = _wrap(REVIEW_SPEC, memory_review, max_chars=20_000)
MEMORY_STATUS = _wrap(STATUS_SPEC, memory_status, max_chars=8_000)

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
    RENAME_NAME: MEMORY_RENAME,
    RELINK_NAME: MEMORY_RELINK,
    DELETE_NAME: MEMORY_DELETE,
    REVIEW_NAME: MEMORY_REVIEW,
    STATUS_NAME: MEMORY_STATUS,
}

__all__ = [
    "NOTE_NAME", "RECALL_NAME", "REFLECT_NAME", "GET_NAME",
    "BROWSE_NAME", "LINT_NAME", "INGEST_NAME", "BACKLINKS_NAME",
    "RENAME_NAME", "RELINK_NAME", "DELETE_NAME", "REVIEW_NAME", "STATUS_NAME",
    "MEMORY_NOTE", "MEMORY_RECALL", "MEMORY_REFLECT", "MEMORY_GET",
    "MEMORY_BROWSE", "MEMORY_LINT", "MEMORY_INGEST", "MEMORY_BACKLINKS",
    "MEMORY_RENAME", "MEMORY_RELINK", "MEMORY_DELETE", "MEMORY_REVIEW", "MEMORY_STATUS",
    "ALL",
]
