"""Nested LLM execution_stream.v1 — CallStreamState + transport.

See ``docs/reference/design/runtime/execution/agentic-llm-streaming.zh.html``.
"""
from __future__ import annotations

from .protocol import (
    SCHEMA_VERSION,
    BlockKind,
    StreamOp,
    attempt_status_values,
)
from .state import CallStreamState, StreamIdentity
from .transport import (
    ExecutionStreamTransport,
    bind_transport_for_turn,
    get_display_msg_id,
    get_stream_transport,
    reset_display_msg_id,
    reset_stream_transport,
    set_display_msg_id,
    set_stream_transport,
    validate_and_forward_envelope,
)

__all__ = [
    "SCHEMA_VERSION",
    "BlockKind",
    "StreamOp",
    "attempt_status_values",
    "CallStreamState",
    "StreamIdentity",
    "ExecutionStreamTransport",
    "bind_transport_for_turn",
    "get_display_msg_id",
    "get_stream_transport",
    "reset_display_msg_id",
    "reset_stream_transport",
    "set_display_msg_id",
    "set_stream_transport",
    "validate_and_forward_envelope",
]
