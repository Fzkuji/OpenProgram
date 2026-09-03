from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent import dispatcher as D
from openprogram.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.self_update.maintenance import (
    enter_maintenance,
    leave_maintenance,
    maintenance_blocks,
)


def _ready(profile: Path, monkeypatch) -> None:
    from openprogram import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)
    store = SelfUpdateStore()
    store.create(
        UpdateRequest(
            update_id="su_maintenance",
            session_id="session-1",
            origin_turn_id="turn-1",
            origin_assistant_id="turn-1_reply",
            agent_id="main",
            repo="/tmp/OpenProgram",
            worktree_id="wt_candidate",
            base_sha="1" * 40,
            candidate_sha="2" * 40,
            changed_paths=("openprogram/feature.py",),
            pre_update_evidence=("git-status:clean",),
            goal="Add behavior",
            assertions=("Behavior works",),
        )
    )
    store.transition("su_maintenance", UpdatePhase.STAGING)
    store.transition("su_maintenance", UpdatePhase.READY)


def test_maintenance_is_idempotent_and_owner_scoped(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "profile"
    _ready(profile, monkeypatch)

    enter_maintenance("su_maintenance")
    enter_maintenance("su_maintenance")

    marker = profile / "self-updates" / "maintenance.json"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert maintenance_blocks("web") is True
    assert maintenance_blocks("self_update_verify") is False

    leave_maintenance("su_maintenance")
    assert marker.exists() is False
    assert maintenance_blocks("web") is False


def test_dispatcher_rejects_new_turn_without_persisting_it(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "profile"
    _ready(profile, monkeypatch)
    enter_maintenance("su_maintenance")
    monkeypatch.setattr(
        D,
        "_run_loop_blocking",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("model must not run")),
    )
    events: list[dict] = []
    request = D.TurnRequest(
        session_id="new-session",
        user_text="start work",
        agent_id="main",
        source="web",
    )

    result = D._process_turn_once(request, on_event=events.append)

    assert result.failed is True
    assert result.error_reason == "SELF_UPDATE_MAINTENANCE"
    assert events[-1]["data"]["reason_code"] == "SELF_UPDATE_MAINTENANCE"


def test_maintenance_symlink_fails_closed(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "profile"
    _ready(profile, monkeypatch)
    marker = profile / "self-updates" / "maintenance.json"
    marker.symlink_to(tmp_path / "missing")

    assert maintenance_blocks("web") is True
    with pytest.raises(RuntimeError, match="symbolic link"):
        enter_maintenance("su_maintenance")
