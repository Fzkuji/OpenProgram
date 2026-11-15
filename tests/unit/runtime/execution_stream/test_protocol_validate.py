"""Parent-side envelope validation for subprocess bridge."""
from __future__ import annotations

from openprogram.runtime.agent.runtime.execution_stream.protocol import (
    SCHEMA_VERSION,
)
from openprogram.runtime.agent.runtime.execution_stream.transport import (
    validate_and_forward_envelope,
)


def test_validate_forwards_matching_identity():
    forwarded = []
    env = {
        "type": "chat_response",
        "data": {
            "type": "execution_stream",
            "schema_version": SCHEMA_VERSION,
            "session_id": "s1",
            "execution_id": "e1",
            "node_id": "n1",
            "generation": 1,
            "op": "block_delta",
            "base_revision": 0,
            "revision": 1,
            "delta": "hi",
            "block_id": "b1",
            "attempt_id": "a1",
        },
    }
    ok = validate_and_forward_envelope(
        env,
        session_id="s1",
        execution_id="e1",
        generation=1,
        on_event=forwarded.append,
    )
    assert ok is True
    assert len(forwarded) == 1


def test_validate_rejects_session_mismatch():
    forwarded = []
    env = {
        "type": "chat_response",
        "data": {
            "type": "execution_stream",
            "schema_version": SCHEMA_VERSION,
            "session_id": "other",
            "execution_id": "e1",
            "node_id": "n1",
            "generation": 1,
            "op": "snapshot",
            "revision": 1,
            "snapshot": {},
        },
    }
    ok = validate_and_forward_envelope(
        env,
        session_id="s1",
        execution_id="e1",
        on_event=forwarded.append,
    )
    assert ok is False
    assert forwarded == []


def test_validate_ignores_non_stream_envelopes():
    forwarded = []
    ok = validate_and_forward_envelope(
        {"type": "chat_response", "data": {"type": "tree_update"}},
        session_id="s1",
        execution_id="e1",
        on_event=forwarded.append,
    )
    assert ok is False
