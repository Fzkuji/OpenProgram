"""Public turn-file action facade."""
from __future__ import annotations

import sys
import time
import types

from . import turn_files_shared as _shared
from .turn_files_shared import (
    _MAX_DIFF_BYTES, _MAX_DIFF_LINE_BYTES, _MAX_DIFF_LINES,
    _MAX_DIFF_PAGE_BYTES, _MAX_REVIEW_CURSORS, _MAX_REVIEW_SNAPSHOT_BYTES,
    _MAX_REVIEW_SNAPSHOT_ITEMS, _MAX_REVIEW_SNAPSHOT_TOMBSTONES,
    _MAX_REVIEW_SNAPSHOTS, _MAX_REVIEW_TEXT_BYTES, _MAX_SCOPE_FILES,
    _REVIEW_CATEGORIES, _REVIEW_CURSORS, _REVIEW_REGISTRY_LOCK,
    _REVIEW_SCOPES, _REVIEW_SNAPSHOT_EPOCHS, _REVIEW_SNAPSHOT_NONCE,
    _REVIEW_SNAPSHOT_TTL, _REVIEW_SNAPSHOTS, _REVIEW_SORTS, _SCOPE_PAGE_SIZE,
    _project_root, _setting, _valid_turn_id,
)
from .turn_files_scope import (
    _OutputLimitError, _ReviewContentBudget, _active_nodes, _branch_scope,
    _get_review_cursor, _get_review_snapshot, _history_eligibility,
    _manifest_mutations, _open_session, _page_scope, _relative,
    _review_category, _review_filter_files, _review_value_bytes,
    _scope_payload, _snapshot_instance_id, _tombstone_review_snapshot,
    _totals, _turn_scope, _turn_summary,
)
from .turn_files_diff import (
    _bind_diff_page, _branch_file_diff, _net_stats, _resolve_diff_cursor,
    _review_turn_file_diff, _same_state, _state_bytes, _workspace_file_diff,
    _workspace_scope,
)
from .turn_files_history import (
    ACTIONS, handle_reapply_turn, handle_review_file_diff,
    handle_review_scope, handle_revert_turn, handle_turn_history_state,
    handle_turn_operation_status, _stable_file_result,
)

__all__ = [
    "ACTIONS", "handle_reapply_turn", "handle_review_file_diff",
    "handle_review_scope", "handle_revert_turn", "handle_turn_history_state",
    "handle_turn_operation_status", "_stable_file_result",
]

_CONFIG_NAMES = {
    "_MAX_SCOPE_FILES", "_SCOPE_PAGE_SIZE", "_MAX_DIFF_BYTES",
    "_MAX_DIFF_PAGE_BYTES", "_MAX_DIFF_LINES", "_MAX_DIFF_LINE_BYTES",
    "_REVIEW_SNAPSHOT_TTL", "_MAX_REVIEW_SNAPSHOTS",
    "_MAX_REVIEW_SNAPSHOT_BYTES", "_MAX_REVIEW_SNAPSHOT_ITEMS",
    "_MAX_REVIEW_CURSORS", "_MAX_REVIEW_SNAPSHOT_TOMBSTONES",
    "_MAX_REVIEW_TEXT_BYTES", "_REVIEW_SNAPSHOT_NONCE",
}


class _PublicConfigModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in _CONFIG_NAMES:
            setattr(_shared, name, value)
        elif name == "_project_root":
            _shared._project_root = value


sys.modules[__name__].__class__ = _PublicConfigModule
