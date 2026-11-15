"""Session lifecycle WS actions: delete / clear / load / search / list / follow_up_answer / chapter pins.

The implementation is split by responsibility into the modules of this
package. This module re-exports their names and the ``ACTIONS`` table so
existing imports keep working; patch a name in the module that reads it.
"""
from __future__ import annotations

from .close_view import handle_close_view
from .tool_output import TOOL_OUTPUT_INLINE_MAX_BYTES
from .transcript import _annotate_spawn_origin
from .transcript import _spawned_branch
from .transcript import _ordered_fn_run_siblings
from .transcript import splice_compaction_event_rows
from .transcript import _is_top_function_run
from .lifecycle import handle_delete_session
from .lifecycle import _rename_tasks
from .lifecycle import handle_rename_session
from .lifecycle import handle_predict_next_prompt
from .lifecycle import handle_update_session_flags
from .lifecycle import handle_clear_sessions
from .load import _session_io
from .load import handle_load_session
from .search import handle_search_session
from .tool_output import handle_get_full_tool_output
from .run_state import handle_get_run_state
from .run_state import handle_follow_up_answer
from .permission_rules import handle_list_permission_rules
from .permission_rules import handle_add_permission_rule
from .permission_rules import handle_remove_permission_rule
from .computer_use import handle_computer_use_apps
from .run_config import handle_set_working_dirs
from .run_config import handle_set_sandbox
from .search import handle_search_messages
from .listing import build_sessions_list
from .listing import broadcast_sessions_list
from .listing import handle_list_sessions
from .lifecycle import handle_mark_session_read
from .lifecycle import handle_set_window_hidden
from .chapter import handle_set_message_chapter


ACTIONS = {
    "close_view": handle_close_view,
    "delete_session": handle_delete_session,
    "mark_session_read": handle_mark_session_read,
    "set_window_hidden": handle_set_window_hidden,
    "rename_session": handle_rename_session,
    "predict_next_prompt": handle_predict_next_prompt,
    "update_session_flags": handle_update_session_flags,
    "clear_sessions": handle_clear_sessions,
    "load_session": handle_load_session,
    "get_full_tool_output": handle_get_full_tool_output,
    "get_run_state": handle_get_run_state,
    "follow_up_answer": handle_follow_up_answer,
    "set_working_dirs": handle_set_working_dirs,
    "set_sandbox": handle_set_sandbox,
    "search_messages": handle_search_messages,
    "search_session": handle_search_session,
    "list_sessions": handle_list_sessions,
    "list_permission_rules": handle_list_permission_rules,
    "add_permission_rule": handle_add_permission_rule,
    "remove_permission_rule": handle_remove_permission_rule,
    "computer_use_apps": handle_computer_use_apps,
    "set_message_chapter": handle_set_message_chapter,
}

__all__ = [
    "TOOL_OUTPUT_INLINE_MAX_BYTES",
    "_annotate_spawn_origin",
    "_spawned_branch",
    "_ordered_fn_run_siblings",
    "splice_compaction_event_rows",
    "_is_top_function_run",
    "handle_delete_session",
    "_rename_tasks",
    "handle_rename_session",
    "handle_predict_next_prompt",
    "handle_update_session_flags",
    "handle_clear_sessions",
    "_session_io",
    "handle_load_session",
    "handle_search_session",
    "handle_get_full_tool_output",
    "handle_get_run_state",
    "handle_follow_up_answer",
    "handle_list_permission_rules",
    "handle_add_permission_rule",
    "handle_remove_permission_rule",
    "handle_computer_use_apps",
    "handle_set_working_dirs",
    "handle_set_sandbox",
    "handle_search_messages",
    "build_sessions_list",
    "broadcast_sessions_list",
    "handle_list_sessions",
    "handle_mark_session_read",
    "handle_set_window_hidden",
    "handle_set_message_chapter",
    "ACTIONS",
    "handle_close_view",
]
