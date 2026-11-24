"""chat: an application window's own conversations, one per key."""
from types import SimpleNamespace

import pytest

from openprogram.programs._host.ui import catalog, state


@pytest.fixture
def window(tmp_path, monkeypatch):
    capabilities = {"chat"}
    monkeypatch.setattr(catalog, "home", lambda: tmp_path / "runtime")
    monkeypatch.setattr(state, "home", lambda: tmp_path / "runtime")
    monkeypatch.setattr(state, "get_instance", lambda key: {"app_id": "test.app", "project_path": str(tmp_path)})
    monkeypatch.setattr(catalog, "get", lambda app_id: {"capabilities": capabilities})
    from openprogram.state.store.project import project_store
    monkeypatch.setattr(project_store, "resolve_project",
                        lambda path: SimpleNamespace(id="project-1", path=path))
    return capabilities


def test_each_key_keeps_one_conversation(window, tmp_path):
    first = state.conversation("window-1", "task:abc", str(tmp_path))
    assert first["session_id"].startswith("local_")
    assert first == {"session_id": first["session_id"], "project_id": "project-1", "folder": str(tmp_path)}
    assert state.conversation("window-1", "task:abc", "")["session_id"] == first["session_id"]
    assert state.conversation("window-1", "project:abc", "")["session_id"] != first["session_id"]
    assert state.conversation("window-2", "task:abc", "")["session_id"] != first["session_id"]


@pytest.mark.parametrize("key", ["", "../x", "-x", "a b", "x" * 161, None])
def test_a_key_must_be_a_short_name(window, key):
    with pytest.raises(ValueError, match="key"):
        state.conversation("window-1", key, "")


def test_the_folder_must_exist(window, tmp_path):
    with pytest.raises(ValueError, match="folder"):
        state.conversation("window-1", "task", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="folder"):
        state.conversation("window-1", "task", "relative/path")


def test_only_a_window_that_declares_chat_gets_one(window):
    window.clear()
    with pytest.raises(ValueError, match="chat"):
        state.conversation("window-1", "task", "")
