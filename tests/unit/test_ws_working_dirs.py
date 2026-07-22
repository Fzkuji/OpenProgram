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
    """隔离 SessionDB + 静音 server 依赖，返回 (db, broadcast 帧列表)。"""
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.webui.server._default_agent_id", lambda: "main")
    broadcasts: list[dict] = []
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast",
        lambda text: broadcasts.append(json.loads(text)),
    )
    return db, broadcasts


def test_set_working_dirs_saves_and_broadcasts(env, tmp_path: Path):
    db, broadcasts = env
    db.create_session("s1", "main")
    extra = tmp_path / "extra"
    extra.mkdir()

    ws = FakeWS()
    asyncio.run(ws_session.handle_set_working_dirs(ws, {
        "session_id": "s1", "dirs": [str(extra)],
    }))

    # 落库：load 回读到 expanduser 后的绝对路径。
    from openprogram.agent.session_config import load_session_run_config
    assert load_session_run_config("s1").additional_working_dirs == [str(extra)]
    # 无 error 帧 + 广播 working_dirs 帧内容正确。
    assert not any(f.get("type") == "error" for f in ws.sent)
    assert broadcasts == [{
        "type": "working_dirs",
        "data": {"session_id": "s1", "dirs": [str(extra)]},
    }]


def test_set_working_dirs_rejects_non_directory(env, tmp_path: Path):
    db, broadcasts = env
    db.create_session("s1", "main")
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "missing"  # 不存在

    ws = FakeWS()
    asyncio.run(ws_session.handle_set_working_dirs(ws, {
        "session_id": "s1", "dirs": [str(good), str(bad)],
    }))

    # 整帧拒绝：error 帧带原因、不广播、不部分写入。
    assert ws.sent and ws.sent[0]["type"] == "error"
    assert str(bad) in ws.sent[0]["data"]["message"]
    assert broadcasts == []
    from openprogram.agent.session_config import load_session_run_config
    assert load_session_run_config("s1").additional_working_dirs == []


def test_session_loaded_returns_additional_working_dirs(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    db, _ = env
    db.create_session("s1", "main")
    extra = tmp_path / "extra"
    extra.mkdir()
    db.update_session("s1", additional_working_dirs=[str(extra)])

    # handle_load_session 走 server 的会话缓存 + 运行态探针，最小化打桩。
    from openprogram.webui import server as _s
    with _s._sessions_lock:
        _s._sessions["s1"] = {"id": "s1"}
    monkeypatch.setattr(_s, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)

    ws = FakeWS()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {"session_id": "s1"}))
    finally:
        with _s._sessions_lock:
            _s._sessions.pop("s1", None)

    loaded = [f for f in ws.sent if f.get("type") == "session_loaded"]
    assert loaded
    settings = loaded[0]["data"]["settings"]
    assert settings["additional_working_dirs"] == [str(extra)]
