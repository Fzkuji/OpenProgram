"""Public project-file action facade.

The implementation is split by responsibility.  This module only publishes
the stable action names and explicit shared primitives used by existing
server routes and tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
from builtins import open

from . import files_shared as _shared
from .files_shared import (
    _ACTIVE_OPERATION_IDS, _ACTIVE_OPERATION_IDS_LOCK, _BINARY_SNIFF_BYTES,
    _IDENTITY_DIGEST_MAX_BYTES, _MUTATION_LOCKS, _MUTATION_LOCKS_GUARD,
    _READ_DIGEST_MAX_BYTES, _READ_MAX_BYTES, _SEARCH_IGNORED_DIRS,
    _WRITE_MAX_BYTES, _canonical_mutation_payload, _durable_file_action,
    _file_digest, _identity, _identity_matches, _mutation_lock,
    _mutation_state_matches, _mutation_states, _normalise_file_result,
    _normalise_mutation_result, _owner_process_alive, _process_alive,
    _replayed_mutation_result, _request_id, _workspace_mutation_lock,
    _open,
)
from .files_query import (
    _QUERY_CURSORS, _QUERY_CURSOR_TOKENS, _QUERY_LOCK, _QUERY_SNAPSHOTS,
    _QueryLimitError, _QuerySnapshot, _evict_snapshot, _new_cursor,
    _query_error, _query_page, _resolve, _search_query, _snapshot_usage,
    _tree_query, _QUERY_MAX_CURSORS, _QUERY_MAX_SNAPSHOTS,
    _QUERY_MAX_SNAPSHOT_ITEMS, _QUERY_MAX_TOTAL_BYTES, _QUERY_MAX_TOTAL_ITEMS,
    _QUERY_SNAPSHOT_TTL,
)
from .files_mutations import (
    _copy_entry, _create_entry, _delete_entry, _read_file, _rename_entry,
    _reveal_entry, _write_file,
)
from .files_ws import (
    ACTIONS, handle_project_file_copy, handle_project_file_create,
    handle_project_file_delete, handle_project_file_operation_status,
    handle_project_file_read, handle_project_file_rename,
    handle_project_file_reveal, handle_project_file_search,
    handle_project_file_tree, handle_project_file_write,
)

__all__ = [
    "ACTIONS", "handle_project_file_copy", "handle_project_file_create",
    "handle_project_file_delete", "handle_project_file_operation_status",
    "handle_project_file_read", "handle_project_file_rename",
    "handle_project_file_reveal", "handle_project_file_search",
    "handle_project_file_tree", "handle_project_file_write",
]


_CONFIG_NAMES = {
    "_QUERY_MAX_CURSORS", "_QUERY_MAX_SNAPSHOTS", "_QUERY_MAX_SNAPSHOT_ITEMS",
    "_QUERY_MAX_TOTAL_BYTES", "_QUERY_MAX_TOTAL_ITEMS", "_QUERY_SNAPSHOT_TTL",
    "_READ_MAX_BYTES", "_READ_DIGEST_MAX_BYTES", "_WRITE_MAX_BYTES",
    "_IDENTITY_DIGEST_MAX_BYTES", "_BINARY_SNIFF_BYTES",
}


class _PublicConfigModule(types.ModuleType):
    """Keep legacy test/config assignments directed at the lower layer."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in _CONFIG_NAMES:
            setattr(_shared, name, value)
        elif name == "open":
            _shared._FILE_OPENER = value


sys.modules[__name__].__class__ = _PublicConfigModule
