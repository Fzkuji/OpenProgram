"""apply_default_workdir: session workdir → runtime cwd (C part 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.internals._workdir import apply_default_workdir, session_workdir_for


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
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_db", lambda: s)
    s.create_session("sess1", "chat", title="t")
    s.append_message("sess1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "parent_id": None,
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


def test_apply_default_workdir_swallows_setter_exception(store):
    class Boom:
        def set_workdir(self, path: str) -> None:
            raise RuntimeError("nope")
    # Must not raise — chat turn shouldn't die because a provider's
    # set_workdir blew up.
    assert apply_default_workdir(Boom(), "sess1") is None
