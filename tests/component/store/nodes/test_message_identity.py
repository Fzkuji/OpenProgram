"""Stored extension fields cannot replace message routing identity."""
import json
from contextlib import ExitStack, closing

import pytest

from openprogram.context.nodes import Call, ROLE_CODE, ROLE_LLM, ROLE_USER
from openprogram.store import SessionNodeWriter, SessionStore


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
@pytest.mark.parametrize("encoded", [False, True])
def test_message_extra_preserves_canonical_identity(tmp_path, role, encoded):
    root = tmp_path / "sessions"
    metadata = {"id": "other-node", "session_id": "other-session", "custom": {"kept": True}}
    with ExitStack() as stack:
        store = stack.enter_context(closing(SessionStore(root)))
        store.create_session("actual-session", "main")
        store.append_message("actual-session", {
            "id": "actual-node", "role": role, "content": "original",
            "extra": json.dumps(metadata) if encoded else metadata,
        })
        reopened = stack.enter_context(closing(SessionStore(root)))
        for reader in (store, reopened):
            for messages in (reader.get_messages("actual-session"), reader.get_branch("actual-session")):
                assert len(messages) == 1
                message = messages[0]
                assert message["id"] == "actual-node"
                assert message["session_id"] == "actual-session"
                assert message["role"] == role
                assert message["custom"] == {"kept": True}
            assert reader.get_nodes("actual-session")[0].metadata["id"] == "other-node"


@pytest.mark.parametrize("role", [ROLE_USER, ROLE_LLM, ROLE_CODE])
def test_node_metadata_preserves_message_identity(tmp_path, role):
    root = tmp_path / "sessions"
    with ExitStack() as stack:
        store = stack.enter_context(closing(SessionStore(root)))
        store.create_session("actual-session", "main")
        store.append_message("actual-session", {"id": "parent", "role": "user", "content": "parent"})
        SessionNodeWriter(store, "actual-session").append(Call(
            id="actual-node", role=role, output="original", caller="parent",
            predecessor="parent" if role != ROLE_CODE else None,
            metadata={"id": "other-node", "session_id": "other-session", "custom": "kept"},
        ))
        reopened = stack.enter_context(closing(SessionStore(root)))
        for reader in (store, reopened):
            messages = reader.get_messages("actual-session")
            descendants = reader.get_descendants("actual-session", "parent")
            for message in (messages[-1], descendants[0]):
                assert message["id"] == "actual-node"
                assert message["session_id"] == "actual-session"
                assert message["custom"] == "kept"
