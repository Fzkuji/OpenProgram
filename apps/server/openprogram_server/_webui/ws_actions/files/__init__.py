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

from . import shared as _shared
from .shared import _ACTIVE_OPERATION_IDS
from .shared import _ACTIVE_OPERATION_IDS_LOCK
from .shared import _BINARY_SNIFF_BYTES
from .shared import _IDENTITY_DIGEST_MAX_BYTES
from .shared import _MUTATION_LOCKS
from .shared import _MUTATION_LOCKS_GUARD
from .shared import _READ_DIGEST_MAX_BYTES
from .shared import _READ_MAX_BYTES
from .shared import _SEARCH_IGNORED_DIRS
from .shared import _WRITE_MAX_BYTES
from .shared import _canonical_mutation_payload
from .shared import _durable_file_action
from .shared import _file_digest
from .shared import _identity
from .shared import _identity_matches
from .shared import _mutation_lock
from .shared import _mutation_state_matches
from .shared import _mutation_states
from .shared import _normalise_file_result
from .shared import _normalise_mutation_result
from .shared import _owner_process_alive
from .shared import _process_alive
from .shared import _replayed_mutation_result
from .shared import _request_id
from .shared import _workspace_mutation_lock
from .shared import _open
from .query import _QUERY_CURSORS
from .query import _QUERY_CURSOR_TOKENS
from .query import _QUERY_LOCK
from .query import _QUERY_SNAPSHOTS
from .query import _QueryLimitError
from .query import _QuerySnapshot
from .query import _evict_snapshot
from .query import _new_cursor
from .query import _query_error
from .query import _query_page
from .query import _resolve
from .query import _search_query
from .query import _snapshot_usage
from .query import _tree_query
from .query import _QUERY_MAX_CURSORS
from .query import _QUERY_MAX_SNAPSHOTS
from .query import _QUERY_MAX_SNAPSHOT_ITEMS
from .query import _QUERY_MAX_TOTAL_BYTES
from .query import _QUERY_MAX_TOTAL_ITEMS
from .query import _QUERY_SNAPSHOT_TTL
from .mutations import _copy_entry
from .mutations import _create_entry
from .mutations import _delete_entry
from .mutations import _read_file
from .mutations import _rename_entry
from .mutations import _reveal_entry
from .mutations import _write_file
from .ws import handle_project_file_info
from .ws import handle_project_folder_size
from .ws import ACTIONS
from .ws import handle_project_file_copy
from .ws import handle_project_file_create
from .ws import handle_project_file_delete
from .ws import handle_project_file_operation_status
from .ws import handle_project_file_read
from .ws import handle_project_file_rename
from .ws import handle_project_file_reveal
from .ws import handle_project_file_search
from .ws import handle_project_file_tree
from .ws import handle_project_file_write

__all__ = [
    "handle_project_file_info", "handle_project_folder_size",
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
