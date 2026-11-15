"""Public turn-file action facade."""
from __future__ import annotations

import sys
import types

from . import shared as _shared
from .shared import _MAX_DIFF_BYTES
from .shared import _MAX_DIFF_LINE_BYTES
from .shared import _MAX_DIFF_LINES
from .shared import _MAX_DIFF_PAGE_BYTES
from .shared import _MAX_REVIEW_CURSORS
from .shared import _MAX_REVIEW_SNAPSHOT_BYTES
from .shared import _MAX_REVIEW_SNAPSHOT_ITEMS
from .shared import _MAX_REVIEW_SNAPSHOT_TOMBSTONES
from .shared import _MAX_REVIEW_SNAPSHOTS
from .shared import _MAX_REVIEW_TEXT_BYTES
from .shared import _MAX_SCOPE_FILES
from .shared import _REVIEW_CURSORS
from .shared import _REVIEW_SNAPSHOT_EPOCHS
from .shared import _REVIEW_SNAPSHOT_NONCE
from .shared import _REVIEW_SNAPSHOT_TTL
from .shared import _REVIEW_SNAPSHOTS
from .shared import _SCOPE_PAGE_SIZE
from .shared import _project_root
from .scope import _branch_scope
from .scope import _get_review_snapshot
from .scope import _history_eligibility
from .scope import _page_scope
from .scope import _review_filter_files
from .scope import _scope_payload
from .scope import _turn_scope
from .scope import _turn_summary
from .diff_shared import _net_stats
from .diff_shared import _state_bytes
from .diff import _bind_diff_page
from .diff import _branch_file_diff
from .diff import _resolve_diff_cursor
from .diff import _review_turn_file_diff
from .diff import _workspace_file_diff
from .diff import _workspace_scope
from .history import ACTIONS
from .history import handle_reapply_turn
from .history import handle_review_file_diff
from .history import handle_review_revert_hunk
from .history import handle_review_scope
from .history import handle_revert_turn
from .history import handle_turn_history_state
from .history import handle_turn_operation_status
from .history import _stable_file_result

__all__ = [
    "ACTIONS", "handle_reapply_turn", "handle_review_file_diff", "handle_review_revert_hunk",
    "handle_review_scope", "handle_revert_turn", "handle_turn_history_state",
    "handle_turn_operation_status", "_stable_file_result",
    # Settable limits; assignments here are forwarded to ``shared``.
    "_MAX_DIFF_BYTES", "_MAX_DIFF_LINE_BYTES", "_MAX_DIFF_LINES", "_MAX_DIFF_PAGE_BYTES",
    "_MAX_REVIEW_CURSORS", "_MAX_REVIEW_SNAPSHOT_BYTES", "_MAX_REVIEW_SNAPSHOT_ITEMS",
    "_MAX_REVIEW_SNAPSHOT_TOMBSTONES", "_MAX_REVIEW_SNAPSHOTS", "_MAX_REVIEW_TEXT_BYTES",
    "_MAX_SCOPE_FILES", "_REVIEW_SNAPSHOT_NONCE", "_REVIEW_SNAPSHOT_TTL", "_SCOPE_PAGE_SIZE",
    "_project_root",
    # Shared review state and primitives read by server routes and tests.
    "_REVIEW_CURSORS", "_REVIEW_SNAPSHOT_EPOCHS", "_REVIEW_SNAPSHOTS",
    "_branch_scope", "_get_review_snapshot", "_history_eligibility", "_page_scope",
    "_review_filter_files", "_scope_payload", "_turn_scope", "_turn_summary",
    "_net_stats", "_state_bytes", "_bind_diff_page", "_branch_file_diff",
    "_resolve_diff_cursor", "_review_turn_file_diff", "_workspace_file_diff",
    "_workspace_scope",
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
