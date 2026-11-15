"""Moving a conversation into another project, turns and all.

The repository and its file-recovery tree move to the project's ``chats/``
folder, the meta and the registry name the new project, the move is
recorded as a ``project/move`` node, and the project it left stays on the
meta so its memory is not written the same turns again. A running
conversation is refused.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openprogram.runtime.context.session_db import SessionDB
from openprogram.interfaces.server.ws_actions import project as ws_project


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    db = SessionDB(state / "projects")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    return db


def _project(tmp_path: Path, name: str):
    from openprogram.state.store.project import project_store as P
    (tmp_path / name).mkdir(exist_ok=True)
    return P.resolve_project(tmp_path / name, name=name)


def _turn(db, session_id: str, node_id: str = "u1") -> None:
    db.append_message(session_id, {
        "id": node_id, "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })


def _reply(ws: FakeWS, kind: str) -> dict:
    frames = [f for f in ws.sent if f.get("type") == kind]
    assert frames, f"no {kind} frame in {ws.sent}"
    return frames[0]["data"]


def test_a_conversation_with_turns_moves_with_its_recovery_tree(env, tmp_path):
    from openprogram.state.store.project import project_store as P
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    env.create_session("s1", "main", project_id=a.id, title="paper")
    _turn(env, "s1")
    before = env._session_dir("s1")
    assert before == env.root_path / a.id / "chats" / "s1"
    recovery = before.parent / ".file-recovery" / "s1" / "t1"
    recovery.mkdir(parents=True)
    (recovery / "x.txt").write_text("undo", encoding="utf-8")

    result = env.move_session("s1", b.id)

    assert result == {"from_project_id": a.id, "project_id": b.id, "moved": True}
    after = env.root_path / b.id / "chats" / "s1"
    assert not before.exists() and (after / "main.jsonl").is_file()
    assert env._session_dir("s1") == after
    assert (after.parent / ".file-recovery" / "s1" / "t1" / "x.txt").read_text(encoding="utf-8") == "undo"
    assert P.project_for_session("s1").id == b.id
    assert "s1" not in (P.get_project(a.id).session_ids or [])
    meta = env.get_session("s1")
    assert meta["title"] == "paper" and meta["project_id"] == b.id
    assert meta["project_history"] == [a.id]
    # The turn came along.
    assert [m["id"] for m in env.get_messages("s1")] == ["u1"]


def test_moving_home_and_back_keeps_every_project_it_left(env, tmp_path):
    from openprogram.state.store.project import project_store as P
    a = _project(tmp_path, "a")
    home = P.get_default_project()
    env.create_session("s1", "main", project_id=a.id)
    _turn(env, "s1")

    env.move_session("s1", home.id)
    assert env._session_dir("s1") == env.root_path / "default" / "chats" / "s1"
    assert P.project_for_session("s1").is_default
    env.move_session("s1", a.id)

    assert env._session_dir("s1") == env.root_path / a.id / "chats" / "s1"
    assert env.get_session("s1")["project_history"] == [a.id, home.id]


def test_moving_into_the_same_project_changes_nothing(env, tmp_path):
    a = _project(tmp_path, "a")
    env.create_session("s1", "main", project_id=a.id)
    before = env._session_dir("s1")

    assert env.move_session("s1", a.id)["moved"] is False
    assert env._session_dir("s1") == before
    assert "project_history" not in (env.get_session("s1") or {})


def test_unknown_project_and_unknown_session_are_refused(env, tmp_path):
    a = _project(tmp_path, "a")
    env.create_session("s1", "main", project_id=a.id)
    with pytest.raises(ValueError):
        env.move_session("s1", "nope")
    with pytest.raises(ValueError):
        env.move_session("ghost", a.id)


def test_ws_move_records_a_node_and_refuses_a_running_conversation(env, tmp_path, monkeypatch):
    from openprogram.state.store.project import project_store as P
    from openprogram.state.store.project.relocate_node import MOVE_NODE_NAME
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    env.create_session("s1", "main", project_id=a.id)
    _turn(env, "s1")

    from openprogram.interfaces.server import server as srv
    from openprogram.state.store.session.provenance import iter_nodes_since
    monkeypatch.setattr(srv, "_broadcast", lambda frame: None)
    monkeypatch.setattr("openprogram.runtime.conversation.run_control.is_turn_running", lambda sid: True)
    ws = FakeWS()
    asyncio.run(ws_project.handle_move_session_to_project(ws, {"session_id": "s1", "project_id": b.id}))
    data = _reply(ws, "session_moved")
    assert data["ok"] is False and data["error"] == ws_project.RUNNING_ERROR
    assert P.project_for_session("s1").id == a.id

    monkeypatch.setattr("openprogram.runtime.conversation.run_control.is_turn_running", lambda sid: False)
    ws = FakeWS()
    asyncio.run(ws_project.handle_move_session_to_project(ws, {"session_id": "s1", "project_id": b.id}))
    data = _reply(ws, "session_moved")
    assert data["ok"] is True and data["from_project_id"] == a.id and data["node_id"]
    assert P.project_for_session("s1").id == b.id
    nodes = [n for n in iter_nodes_since(env, "s1") if n.name == MOVE_NODE_NAME]
    assert len(nodes) == 1
    assert nodes[0].output == "a → b"
    assert nodes[0].metadata["old_project_id"] == a.id and nodes[0].metadata["new_project_id"] == b.id
    assert nodes[0].caller == "ROOT" and nodes[0].predecessor is None
    # The conversation's head did not move: the user turn is still the tip.
    assert [m["id"] for m in env.get_branch("s1")] == ["u1"]
    listing = _reply(ws, "projects_list")
    assert "s1" in next(p for p in listing["projects"] if p["id"] == b.id)["session_ids"]

    ws = FakeWS()
    asyncio.run(ws_project.handle_move_session_to_project(ws, {"session_id": "s1", "project_id": "nope"}))
    assert _reply(ws, "session_moved")["error"] == "unknown project"
