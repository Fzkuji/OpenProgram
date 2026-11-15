"""Context commits as stored records: the item and commit types and their files.

A context commit is an immutable record of what the model saw at one turn,
one JSON file per commit in the session directory (``context/commits/<id>.json``).
Generating a commit applies the runtime's context rules and lives in
``openprogram.runtime.context.commit``; this package defines the record and
reads and writes it.
"""
from .types import CLEARED_PLACEHOLDER, CURRENT_RULES_VERSION, ContextCommit, ContextItem
from .store import (
    init_schema,
    list_commits,
    load_commit,
    load_commit_for_head,
    load_latest_commit,
    save_commit,
)

__all__ = [
    "CLEARED_PLACEHOLDER",
    "CURRENT_RULES_VERSION",
    "ContextCommit",
    "ContextItem",
    "init_schema",
    "list_commits",
    "load_commit",
    "load_commit_for_head",
    "load_latest_commit",
    "save_commit",
]
