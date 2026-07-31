"""Turn-end shadow-git wiring (dispatcher/finalize.commit_turn_to_shadow_git).

Covers the contract the file-review UI depends on: after a turn that
touched files, the project's shadow repo holds a commit for it and the
assistant node carries the before/after sha pair.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openprogram.agent.dispatcher.finalize import commit_turn_to_shadow_git
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> SessionStore:
    s = SessionStore(root_path=tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session_store._default_store", s, raising=False,
    )
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", s,
        raising=False,
    )
    # `openprogram.store.default_store` is normally a lazy __getattr__
    # re-export, but other suites setattr a real attribute onto the
    # module, which permanently shadows the lazy hook. Pin it here so
    # this file passes standalone AND after those suites have run.
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: s, raising=False,
    )
    return s


def _seed(store: SessionStore, session_id: str, msg_id: str) -> None:
    store.create_session(session_id, agent_id="main", title="t")
    store.append_message(session_id, {
        "id": "u1", "role": "user", "content": "go", "timestamp": 1.0,
    })
    store.append_message(session_id, {
        "id": msg_id, "role": "assistant", "content": "ok",
        "predecessor": "u1", "timestamp": 2.0,
    })


def test_commit_stamps_node_and_produces_diff(store, tmp_path):
    session_id, msg_id = "s_fin", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "app.py"
    target.write_text("first\n")

    # A turn: checkpoint before the edit, then edit.
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("first\nsecond\n")

    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root), \
         patch("openprogram.store.project.project_commit._project_for",
               return_value=SimpleNamespace(path=str(project))):
        sha = commit_turn_to_shadow_git(session_id, msg_id, "make an edit")

    assert sha

    _git, idx = store._open(session_id)
    meta = (idx.nodes_by_id[msg_id].metadata or {}).get("shadow_git")
    assert meta is not None
    assert meta["repo"] == str(project)
    assert meta["after"] == sha

    # The recorded shas yield a real diff of this turn.
    from openprogram.store.shadow_git.store import ShadowGitStore
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root):
        diff = ShadowGitStore(str(project)).diff(
            meta["before"] or "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            meta["after"],
        )
    assert "+second" in diff


def test_stamp_survives_reload(store, tmp_path):
    """The stamp is written to the node's history file, not just memory."""
    session_id, msg_id = "s_fin_persist", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "a.py"
    target.write_text("x\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("y\n")

    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root), \
         patch("openprogram.store.project.project_commit._project_for",
               return_value=SimpleNamespace(path=str(project))):
        assert commit_turn_to_shadow_git(session_id, msg_id, "edit")

    fresh = SessionStore(root_path=store.root_path)
    _git, idx = fresh._open(session_id)
    assert (idx.nodes_by_id[msg_id].metadata or {}).get("shadow_git")


def test_no_project_is_noop(store, tmp_path):
    """Ad-hoc sessions (no bound project) skip shadow git entirely."""
    session_id, msg_id = "s_fin_noproj", "u1_reply"
    _seed(store, session_id, msg_id)
    with patch("openprogram.store.project.project_commit._project_for",
               return_value=None):
        assert commit_turn_to_shadow_git(session_id, msg_id, "x") is None


def test_turn_touching_no_files_is_noop(store, tmp_path):
    session_id, msg_id = "s_fin_nofiles", "u1_reply"
    _seed(store, session_id, msg_id)
    project = tmp_path / "proj"
    project.mkdir()
    with patch("openprogram.store.project.project_commit._project_for",
               return_value=SimpleNamespace(path=str(project))):
        assert commit_turn_to_shadow_git(session_id, msg_id, "x") is None


def test_failure_is_swallowed(store):
    """A shadow-git blowup must never propagate into the turn."""
    with patch("openprogram.store.project.project_commit._project_for",
               side_effect=RuntimeError("boom")):
        assert commit_turn_to_shadow_git("s", "m", "x") is None
