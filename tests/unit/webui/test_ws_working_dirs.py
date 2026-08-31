"""ws action ``set_working_dirs`` + ``session_loaded`` 回带。

设计：docs/reference/design/runtime/additional-working-directories.md §3.2/§3.3。
锁三件事：
  1. 合法目录列表 → expanduser 后落库（save_session_run_config）+ 广播
     ``working_dirs`` 帧；
  2. 任一条目不是存在的目录 → 整帧拒绝（error 帧），不做部分写入；
  3. ``session_loaded.data.settings`` 回带 ``additional_working_dirs``，
     刷新/换端后前端能恢复列表。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest

from openprogram.agent.session_db import SessionDB
from openprogram.webui.ws_actions import session as ws_session


class FakeWS:
    """收集 send_text 的假 WebSocket。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离 SessionDB + 静音 server 依赖，返回 (db, broadcast 帧列表, sid)。

    两处状态隔离，缺一个都会在随机序下偶发挂：

    1. ``sid`` 每次唯一。run-config 读写走 ``default_db()``、
       ``server._sessions`` 是进程级全局字典，硬编码 "s1" 会和别的
       模块留在这两处的同名残留互相踩。
    2. ``broadcasts`` 只收本用例关心的帧。别的用例起的后台线程随时
       可能在本用例执行期间广播其它类型的帧，掺进这个 list 就把
       ``broadcasts == []`` / ``== [...]`` 的断言打挂。
    """
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.webui.server._default_agent_id", lambda: "main")

    async def _direct_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ws_session.asyncio, "to_thread", _direct_to_thread)
    broadcasts: list[dict] = []

    def _collect(text: str) -> None:
        frame = json.loads(text)
        if frame.get("type") == "working_dirs":
            broadcasts.append(frame)

    monkeypatch.setattr("openprogram.webui.server._broadcast", _collect)
    return db, broadcasts, f"wd-{uuid.uuid4().hex[:12]}"


def test_set_working_dirs_saves_and_broadcasts(env, tmp_path: Path):
    db, broadcasts, sid = env
    db.create_session(sid, "main")
    extra = tmp_path / "extra"
    extra.mkdir()

    ws = FakeWS()
    asyncio.run(ws_session.handle_set_working_dirs(ws, {
        "session_id": sid, "dirs": [str(extra)],
    }))

    # 落库：load 回读到 expanduser 后的绝对路径。
    from openprogram.agent.session_config import load_session_run_config
    assert load_session_run_config(sid).additional_working_dirs == [str(extra)]
    # 无 error 帧 + 广播 working_dirs 帧内容正确。
    assert not any(f.get("type") == "error" for f in ws.sent)
    assert broadcasts == [{
        "type": "working_dirs",
        "data": {"session_id": sid, "dirs": [str(extra)]},
    }]


def test_set_working_dirs_rejects_non_directory(env, tmp_path: Path):
    db, broadcasts, sid = env
    db.create_session(sid, "main")
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "missing"  # 不存在

    ws = FakeWS()
    asyncio.run(ws_session.handle_set_working_dirs(ws, {
        "session_id": sid, "dirs": [str(good), str(bad)],
    }))

    # 整帧拒绝：error 帧带原因、不广播、不部分写入。
    assert ws.sent and ws.sent[0]["type"] == "error"
    assert str(bad) in ws.sent[0]["data"]["message"]
    assert broadcasts == []
    from openprogram.agent.session_config import load_session_run_config
    assert load_session_run_config(sid).additional_working_dirs == []


def test_session_loaded_returns_additional_working_dirs(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    db, _, sid = env
    db.create_session(sid, "main")
    extra = tmp_path / "extra"
    extra.mkdir()
    db.update_session(sid, additional_working_dirs=[str(extra)])

    # handle_load_session 走 server 的会话缓存 + 运行态探针，最小化打桩。
    from openprogram.webui import server as _s
    with _s._sessions_lock:
        _s._sessions[sid] = {"id": sid}
    monkeypatch.setattr(_s, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)

    ws = FakeWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
    finally:
        with _s._sessions_lock:
            _s._sessions.pop(sid, None)

    loaded = [f for f in ws.sent if f.get("type") == "session_loaded"]
    assert loaded
    settings = loaded[0]["data"]["settings"]
    assert settings["additional_working_dirs"] == [str(extra)]


def test_session_loaded_precedes_async_context_stats(
    env, monkeypatch: pytest.MonkeyPatch,
):
    db, _, sid = env
    db.create_session(sid, "main")

    from openprogram.webui import server as _s

    with _s._sessions_lock:
        _s._sessions[sid] = {"id": sid}
    monkeypatch.setattr(_s, "_get_provider_info", lambda session_id=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda session_id: False)
    context_frames: list[dict] = []
    monkeypatch.setattr(
        _s,
        "_broadcast_chat_response",
        lambda _sid, _msg_id, frame: context_frames.append(frame),
    )

    ws = FakeWS()

    async def _after_session_loaded(func, /, *args, **kwargs):
        assert any(frame.get("type") == "session_loaded" for frame in ws.sent)
        return func(*args, **kwargs)

    monkeypatch.setattr(ws_session.asyncio, "to_thread", _after_session_loaded)
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
    finally:
        with _s._sessions_lock:
            _s._sessions.pop(sid, None)

    loaded = next(frame for frame in ws.sent if frame["type"] == "session_loaded")
    assert loaded["data"]["context_stats"] is None
    assert context_frames and context_frames[-1]["type"] == "context_stats"


def test_session_loaded_replays_running_execution_id(env, monkeypatch):
    db, _, sid = env
    db.create_session(sid, "main")
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.store import SessionNodeWriter
    writer = SessionNodeWriter(db, sid)
    writer.append(Call(
        id="hidden-control",
        role=ROLE_CODE,
        name="hidden_probe",
        metadata={
            "status": "running",
            "expose": "hidden",
            "execution_control": True,
        },
    ))
    writer.append(Call(
        id="hidden-child",
        role=ROLE_CODE,
        name="nested_hidden_work",
        input={"secret": "never-show"},
        caller="hidden-control",
        metadata={"status": "running"},
    ))
    from openprogram.webui import server as _s
    execution_id = "exec-hidden-direct"
    with _s._sessions_lock:
        _s._sessions[sid] = {"id": sid}
    with _s._running_tasks_lock:
        _s._running_tasks[sid] = {
            "msg_id": "m1",
            "func_name": "hidden_probe",
            "execution_id": execution_id,
            "started_at": time.time(),
            "last_event_at": time.time(),
            "stream_events": [],
        }
    monkeypatch.setattr(_s, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: True)

    ws = FakeWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
    finally:
        with _s._sessions_lock:
            _s._sessions.pop(sid, None)
        with _s._running_tasks_lock:
            _s._running_tasks.pop(sid, None)

    replay = next(frame for frame in ws.sent if frame["type"] == "running_task")
    assert replay["data"]["execution_id"] == execution_id
    loaded = next(frame for frame in ws.sent if frame["type"] == "session_loaded")
    assert "never-show" not in str(loaded["data"]["messages"])
    assert "never-show" not in str(loaded["data"]["graph"])


def test_session_loaded_keeps_workflow_llm_output_inside_runtime_card(
    env, monkeypatch: pytest.MonkeyPatch,
):
    """Internal workflow LLM nodes belong to context_tree, not Chat bubbles."""
    db, _, sid = env
    db.create_session(sid, "main")
    db.append_message(sid, {
        "id": "u1", "role": "user", "content": "run workflow",
        "predecessor": "ROOT",
    })
    db.append_message(sid, {
        "id": "a1", "role": "assistant", "content": "starting",
        "predecessor": "u1",
    })
    from openprogram.context.nodes import Call, ROLE_CODE, ROLE_LLM
    from openprogram.store import SessionNodeWriter
    writer = SessionNodeWriter(db, sid)
    writer.append(Call(
        id="run1", role=ROLE_CODE, name="agentic_workflow",
        output={"status": "completed"}, predecessor="a1",
        metadata={"status": "completed"},
    ))
    writer.append(Call(
        id="inner-llm", role=ROLE_LLM,
        output="workflow internal final output", caller="run1",
        metadata={"status": "completed"},
    ))

    from openprogram.webui import server as _s
    with _s._sessions_lock:
        _s._sessions[sid] = {"id": sid}
    monkeypatch.setattr(_s, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)

    ws = FakeWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
    finally:
        with _s._sessions_lock:
            _s._sessions.pop(sid, None)

    loaded = next(f for f in ws.sent if f.get("type") == "session_loaded")
    messages = loaded["data"]["messages"]
    assert [m["id"] for m in messages] == ["u1", "a1", "run1"]
    runtime = messages[-1]
    assert runtime["display"] == "runtime"
    assert runtime["function"] == "agentic_workflow"
    assert runtime["context_tree"]
    assert "inner-llm" not in {m["id"] for m in messages}


def test_session_loaded_emits_function_run_sibling_navigation(
    env, monkeypatch: pytest.MonkeyPatch,
):
    db, _, sid = env
    db.create_session(sid, "main")
    db.append_message(sid, {
        "id": "u1", "role": "user", "content": "run",
        "predecessor": "ROOT", "timestamp": 1,
    })
    db.append_message(sid, {
        "id": "a1", "role": "assistant", "content": "starting",
        "predecessor": "u1", "timestamp": 2,
    })
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.store import SessionNodeWriter
    writer = SessionNodeWriter(db, sid)
    for node_id in ("run-early", "run-late"):
        writer.append(Call(
            id=node_id,
            role=ROLE_CODE,
            name="word_count",
            output={"status": "completed"},
            predecessor="a1",
            metadata={"status": "completed"},
        ))
    db.set_head(sid, "run-late")

    from openprogram.webui import persistence
    from openprogram.webui import server as _s
    original_aggregate = persistence.aggregate_tool_messages

    def aggregate_with_created_at(messages):
        aggregated = original_aggregate(messages)
        created_at = {"run-early": 10, "run-late": 20}
        for message in aggregated:
            if message.get("id") in created_at:
                message["created_at"] = created_at[message["id"]]
        return aggregated

    monkeypatch.setattr(
        persistence, "aggregate_tool_messages", aggregate_with_created_at,
    )
    monkeypatch.setattr(_s, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(_s, "refresh_context_stats", lambda _sid: None)
    with _s._sessions_lock:
        _s._sessions[sid] = {"id": sid}

    ws = FakeWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
        loaded = next(frame for frame in ws.sent if frame["type"] == "session_loaded")
        late = next(message for message in loaded["data"]["messages"]
                    if message.get("id") == "run-late")
        assert late["sibling_index"] == 2
        assert late["sibling_total"] == 2
        assert late["prev_sibling_id"] == "run-early"
        assert late["next_sibling_id"] is None

        db.set_head(sid, "run-early")
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": sid}))
        loaded = [frame for frame in ws.sent if frame["type"] == "session_loaded"][-1]
        early = next(message for message in loaded["data"]["messages"]
                     if message.get("id") == "run-early")
        assert early["sibling_index"] == 1
        assert early["sibling_total"] == 2
        assert early["prev_sibling_id"] is None
        assert early["next_sibling_id"] == "run-late"
    finally:
        with _s._sessions_lock:
            _s._sessions.pop(sid, None)
