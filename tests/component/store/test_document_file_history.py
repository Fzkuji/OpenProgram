"""Public trusted write/read behavior for staged binary documents."""

import asyncio
import json
from pathlib import Path

import pytest

from openprogram import sandbox
from openprogram.agent.dispatcher.finalize import persist_turn_file_summary
from openprogram.programs.tools.files.read import read
from openprogram.programs.tools.files.write import write
from openprogram.store import SessionNodeWriter, SessionStore, _current_turn_id, _store
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.webui.ws_actions import turn_files as turn_files_ws


def _text(result) -> str:
    return result.content[0].text if hasattr(result, "content") else str(result)


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture(autouse=True)
def _restore_sandbox_policy():
    saved = sandbox.policy_snapshot()
    yield
    sandbox.install_policy_snapshot(saved)


@pytest.mark.parametrize("suffix", [".docx", ".pptx"])
def test_registered_binary_history_survives_reopen_and_public_revert_reapply(
    tmp_path: Path, monkeypatch, suffix: str,
) -> None:
    """Exercise the public tool boundary and durable history boundary together."""
    sandbox.install_policy_snapshot({"enabled": False, "policy": None})
    root = tmp_path / "sessions"
    session_id, turn_id = "binary-history", "assistant-binary"
    session_store = SessionStore(root_path=root)
    session_store.create_session(session_id, agent_id="main", title="binary")
    session_store.append_message(session_id, {
        "id": "u1", "role": "user", "content": "publish", "timestamp": 1.0,
    })
    session_store.append_message(session_id, {
        "id": turn_id, "role": "assistant", "content": "done",
        "predecessor": "u1", "timestamp": 2.0,
    })
    monkeypatch.setattr("openprogram.store.default_store", lambda: session_store, raising=False)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: session_store, raising=False)
    shim = SessionNodeWriter(session_store, session_id)
    store_token = _store.set(shim)
    turn_token = _current_turn_id.set(turn_id)
    target = tmp_path / f"Proposal{suffix}"
    staged = tmp_path / f"Proposal.staged{suffix}"
    before = b"old\x00document"
    after = b"new\x00document\xffwith-layout-bytes"
    target.write_bytes(before)
    staged.write_bytes(after)
    reopened = None
    try:
        read_result = asyncio.run(read.execute("read-before-publish", {"file_path": str(target)}, None, None))
        assert "binary" in _text(read_result)
        published = asyncio.run(write.execute("publish-binary", {
            "file_path": str(target), "source_path": str(staged),
        }, None, None))
        assert "Wrote" in _text(published)
        assert target.read_bytes() == after
        summary = persist_turn_file_summary(session_id, turn_id)
        assert summary is not None
        assert summary["files"][0]["binary"] is True
        session_store.close()
        reopened = SessionStore(root_path=root)
        monkeypatch.setattr("openprogram.store.default_store", lambda: reopened, raising=False)
        monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: reopened, raising=False)
        assert reopened._open(session_id, create_if_missing=True) is not None
        ws = _WS()
        asyncio.run(turn_files_ws.handle_review_scope(ws, {
            "session_id": session_id, "scope": "turn", "assistant_msg_id": turn_id,
        }))
        data = ws.sent[0]["data"]
        assert data["status"] == "ready", data
        assert data["files"][0]["path"] == str(target)
        journal = CheckpointStore(reopened._session_dir(session_id))
        reverted = journal.apply_history_operation(turn_id, "revert", idempotency_key="binary-revert")
        assert reverted["status"] == "committed"
        assert target.read_bytes() == before
        reapplied = journal.apply_history_operation(turn_id, "reapply", idempotency_key="binary-reapply")
        assert reapplied["status"] == "committed"
        assert target.read_bytes() == after
        target.write_bytes(b"external change")
        conflict = journal.apply_history_operation(turn_id, "revert", idempotency_key="after-external-edit")
        assert conflict["status"] != "committed"
        assert target.read_bytes() == b"external change"
    finally:
        if reopened is not None:
            reopened.close()
        session_store.close()
        from openprogram.store.snapshot.read_tracking import forget_session
        forget_session(session_id)
        _current_turn_id.reset(turn_token)
        _store.reset(store_token)


def test_registered_write_publishes_staged_binary_and_read_reports_metadata(tmp_path: Path, monkeypatch) -> None:
    from openprogram import sandbox
    sandbox.install_policy_snapshot({"enabled": False, "policy": None})
    target = tmp_path / "proposal.docx"
    source = tmp_path / "proposal.staged.docx"
    payload = b"PK\x03\x04\x00\x01\x80\xff\x00binary-document"
    source.write_bytes(payload)

    result = asyncio.run(write.execute("write-binary", {
        "file_path": str(target), "source_path": str(source),
    }, None, None))
    assert "Wrote" in _text(result)
    assert target.read_bytes() == payload

    observed = asyncio.run(read.execute("read-binary", {"file_path": str(target)}, None, None))
    assert "binary" in _text(observed)
    assert str(len(payload)) in _text(observed)


def test_registered_write_rejects_invalid_source_without_touching_target(tmp_path: Path, monkeypatch) -> None:
    from openprogram import sandbox
    sandbox.install_policy_snapshot({"enabled": False, "policy": None})
    target = tmp_path / "proposal.pptx"
    target.write_bytes(b"original")
    source = tmp_path / "missing.pptx"
    result = asyncio.run(write.execute("write-invalid", {
        "file_path": str(target), "source_path": str(source),
    }, None, None))
    assert "Error" in _text(result)
    assert target.read_bytes() == b"original"


def test_registered_write_rejects_ambiguous_content_and_source(tmp_path: Path, monkeypatch) -> None:
    sandbox.install_policy_snapshot({"enabled": False, "policy": None})
    target = tmp_path / "target.txt"
    source = tmp_path / "source.txt"
    source.write_bytes(b"source")
    result = asyncio.run(write.execute("ambiguous", {
        "file_path": str(target), "content": "text", "source_path": str(source),
    }, None, None))
    assert "exactly one" in _text(result)
    assert not target.exists()

