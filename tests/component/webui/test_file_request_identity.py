"""Request correlation and durable idempotency for project-file WS actions."""
from __future__ import annotations

import asyncio
import json
import types
import uuid
from pathlib import Path

import pytest

from openprogram.store.project import project_store
from openprogram.webui import server
from openprogram.webui.ws_actions import files


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, value):
        self.sent.append(json.loads(value))


def run(handler, command):
    ws = FakeWS()
    asyncio.run(handler(ws, command))
    return ws.sent[0]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(
        project_store, "get_project",
        lambda project_id: types.SimpleNamespace(id="p1", path=str(root))
        if project_id == "p1" else None,
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    return root


def test_file_result_echoes_request_id_on_success_and_stale(project):
    request_id = str(uuid.uuid4())
    frame = run(files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
        "request_id": request_id,
    })
    assert frame["data"]["request_id"] == request_id
    stale = run(files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": "missing",
        "request_id": request_id,
    })
    assert stale["data"]["request_id"] == request_id
    assert stale["data"]["error_code"] == "STALE_SNAPSHOT"


def test_dispatch_requires_uuid_and_idempotency_key():
    from openprogram.webui.ws_errors import OperationError

    with pytest.raises(OperationError):
        server._validate_file_request({"request_id": "request-1"}, "project_file_read")
    with pytest.raises(OperationError):
        server._validate_file_request({"request_id": str(uuid.uuid4())}, "project_file_write")


def test_file_write_replay_collision_and_restart(project):
    request_id = str(uuid.uuid4())
    command = {
        "project_id": "p1", "path": "source.txt", "content": "after",
        "expected_mtime": (project / "source.txt").stat().st_mtime,
        "idempotency_key": "write-once",
        "request_id": request_id,
    }
    first = run(files.handle_project_file_write, command)["data"]
    assert first["ok"] is True
    mtime = (project / "source.txt").stat().st_mtime

    replay = run(files.handle_project_file_write, command)["data"]
    assert replay["operation_id"] == first["operation_id"]
    assert (project / "source.txt").stat().st_mtime == mtime

    collision = run(files.handle_project_file_write, {
        **command, "path": "other.txt",
    })["data"]
    assert collision["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # A fresh store handle observes the same durable completed record.
    from openprogram.store.file_operations import default_file_operation_store, fingerprint
    store = default_file_operation_store()
    row, owner = store.begin(
        "p1", "project_file_write", "write-once",
        fingerprint({"path": "source.txt", "content": "after",
                     "expected_mtime": command["expected_mtime"]}),
    )
    assert not owner and store.replay(row)["operation_id"] == first["operation_id"]
