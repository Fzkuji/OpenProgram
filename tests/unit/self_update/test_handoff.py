from __future__ import annotations

from pathlib import Path

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update.control.handoff import release_prepared_update


def _request() -> UpdateRequest:
    return UpdateRequest(
        update_id="su_handoff",
        session_id="session-1",
        origin_turn_id="turn-1",
        origin_assistant_id="turn-1_reply",
        agent_id="main",
        repo=str(Path("/tmp/OpenProgram").resolve()),
        worktree_id="wt_candidate",
        base_sha="1" * 40,
        candidate_sha="2" * 40,
        changed_paths=("openprogram/feature.py",),
        pre_update_evidence=("git-status:clean",),
        goal="Add the requested behavior",
        assertions=("The behavior is observable",),
    )


def test_release_requires_the_origin_assistant_message(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())

    assert release_prepared_update("session-1", "other", store=store) is None
    assert store.load_active().state.phase is UpdatePhase.PREPARING

    released = release_prepared_update(
        "session-1", "turn-1_reply", store=store
    )

    assert released is not None
    assert released.phase is UpdatePhase.BUILDING
    assert released.detail == {
        "turn_released": True,
        "session_id": "session-1",
        "assistant_id": "turn-1_reply",
    }
    assert release_prepared_update(
        "session-1", "turn-1_reply", store=store
    ) is None


def test_release_without_an_update_does_not_create_state_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing"

    assert release_prepared_update("session-1", "turn-1_reply", store=SelfUpdateStore(root)) is None
    assert root.exists() is False
