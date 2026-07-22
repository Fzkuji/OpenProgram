"""apply_default_workdir: session workdir → runtime cwd (C part 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.internals._workdir import (
    apply_default_workdir,
    project_workdir_for,
    session_workdir_for,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.last_workdir: str | None = None
        self.calls = 0

    def set_workdir(self, path: str) -> None:
        self.last_workdir = path
        self.calls += 1


class _NoWorkdirRuntime:
    """Runtime without set_workdir — apply must no-op without raising."""
    pass


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    from openprogram.store import project_store
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_db", lambda: s)
    # 与真实项目注册表隔离：默认无绑定、无默认项目，让"回落会话
    # workdir"的老用例仍然测得到那条链。
    monkeypatch.setattr(project_store, "project_for_session", lambda sid: None)
    monkeypatch.setattr(project_store, "get_default_project", lambda: None)
    s.create_session("sess1", "chat", title="t")
    s.append_message("sess1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.commit_turn("sess1", "init")
    return s


def test_session_workdir_for_returns_path(store):
    wd = session_workdir_for("sess1")
    assert wd is not None
    assert wd.exists()
    assert wd.name == "workdir"


def test_session_workdir_for_unknown_session(store):
    assert session_workdir_for("nope") is None


def test_apply_default_workdir_sets_runtime_cwd(store):
    rt = _FakeRuntime()
    applied = apply_default_workdir(rt, "sess1")
    assert applied is not None
    assert rt.last_workdir == str(applied)
    assert rt.calls == 1


def test_apply_default_workdir_runtime_without_setter(store):
    """Runtime lacking set_workdir must no-op rather than crash."""
    rt = _NoWorkdirRuntime()
    assert apply_default_workdir(rt, "sess1") is None


def test_apply_default_workdir_none_runtime(store):
    assert apply_default_workdir(None, "sess1") is None


def test_apply_default_workdir_unknown_session(store):
    rt = _FakeRuntime()
    assert apply_default_workdir(rt, "nope") is None
    assert rt.calls == 0


class _FakeProject:
    def __init__(self, path, is_default=False):
        self.path = path
        self.is_default = is_default


def _bind_project(monkeypatch, proj):
    from openprogram.store import project_store
    monkeypatch.setattr(project_store, "project_for_session",
                        lambda sid: proj)


def test_project_workdir_for_prefers_project_path(store, tmp_path, monkeypatch):
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    _bind_project(monkeypatch, _FakeProject(str(proj_dir)))
    assert project_workdir_for("sess1") == proj_dir
    rt = _FakeRuntime()
    applied = apply_default_workdir(rt, "sess1")
    assert applied == proj_dir
    assert rt.last_workdir == str(proj_dir)


def test_project_workdir_for_default_project_path_used(store, tmp_path, monkeypatch):
    """默认项目的路径（用户主目录）同样生效——绝不能漏成进程 cwd。"""
    _bind_project(monkeypatch, _FakeProject(str(tmp_path), is_default=True))
    assert project_workdir_for("sess1") == tmp_path
    rt = _FakeRuntime()
    assert apply_default_workdir(rt, "sess1") == tmp_path


def test_project_workdir_for_unbound_uses_default_project(store, tmp_path, monkeypatch):
    """未绑定项目的会话回落到默认项目路径（chip 显示的就是它）。"""
    from openprogram.store import project_store
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(project_store, "get_default_project",
                        lambda: _FakeProject(str(home), is_default=True))
    assert project_workdir_for("sess1") == home


def test_project_workdir_for_missing_dir_falls_back(store, tmp_path, monkeypatch):
    """A bound project whose path vanished must not become the cwd."""
    _bind_project(monkeypatch, _FakeProject(str(tmp_path / "gone")))
    assert project_workdir_for("sess1") is None


def test_apply_default_workdir_swallows_setter_exception(store):
    class Boom:
        def set_workdir(self, path: str) -> None:
            raise RuntimeError("nope")
    # Must not raise — chat turn shouldn't die because a provider's
    # set_workdir blew up.
    assert apply_default_workdir(Boom(), "sess1") is None
