"""Chat WS actions: chat / retry_function /
set_conversation_channel.

The ``chat`` action is the sole turn entry point from the web UI. The
retry / channel-bind actions are ws-only.

The implementation is split by responsibility into the modules of this
package. This module re-exports their names and the ``ACTIONS`` table so
existing imports keep working; patch a name in the module that reads it.
"""
from __future__ import annotations

from .context_usage import _record_context_usage
from .context_usage import _record_canonical_context_stats
from .session_agent import _db_agent_id
from .attachments import _safe_attach_name
from .attachments import _write_private_json
from .attachments import MAX_ATTACH_MB
from .attachments import MAX_ATTACH_BYTES
from .attachments import MAX_TURN_ATTACH_BYTES
from .attachments import PREVIEW_CAP
from .attachments import _decoded_kind
from .attachments import _pdf_count_and_preview
from .attachments import _count_and_preview
from .attachments import _attachment_name
from .attachments import _persist_attachments
from .attachments import _persist_attachments_async
from .attachments import _attachments_for_dispatch
from .message_input import _with_recording
from .message_input import _title_from_text
from .message_input import _pasted_text_elements
from .turn import handle_chat
from .retry import _retry_call_node
from .retry import _call_predecessor
from .retry import handle_retry_function
from .conversation_channel import handle_set_conversation_channel
from .commands import handle_compact
from .commands import handle_operation_cancel
from .commands import handle_sandbox
from .rewind import handle_rewind_list
from .rewind import handle_rewind
from .rewind import handle_rewind_marks
from .rewind import handle_rewind_redo
from .rewind import handle_rewind_undo


ACTIONS = {
    "chat": handle_chat,
    "retry_function": handle_retry_function,
    "set_conversation_channel": handle_set_conversation_channel,
    "compact": handle_compact,
    "operation.cancel": handle_operation_cancel,
    "sandbox": handle_sandbox,
    "rewind_list": handle_rewind_list,
    "rewind": handle_rewind,
    "rewind_marks": handle_rewind_marks,
    "rewind_undo": handle_rewind_undo,
    "rewind_redo": handle_rewind_redo,
}

__all__ = [
    "_record_context_usage",
    "_record_canonical_context_stats",
    "_db_agent_id",
    "_safe_attach_name",
    "_write_private_json",
    "MAX_ATTACH_MB",
    "MAX_ATTACH_BYTES",
    "MAX_TURN_ATTACH_BYTES",
    "PREVIEW_CAP",
    "_decoded_kind",
    "_pdf_count_and_preview",
    "_count_and_preview",
    "_attachment_name",
    "_persist_attachments",
    "_persist_attachments_async",
    "_attachments_for_dispatch",
    "_with_recording",
    "_title_from_text",
    "_pasted_text_elements",
    "handle_chat",
    "_retry_call_node",
    "_call_predecessor",
    "handle_retry_function",
    "handle_set_conversation_channel",
    "handle_compact",
    "handle_sandbox",
    "handle_rewind_list",
    "handle_rewind",
    "handle_rewind_marks",
    "handle_rewind_undo",
    "handle_rewind_redo",
    "ACTIONS",
]
