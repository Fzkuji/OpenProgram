from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openprogram.self_update import ActiveUpdateError, SelfUpdateStore, UpdateRequest


def _request(update_id: str) -> UpdateRequest:
    return UpdateRequest(
        update_id=update_id,
        session_id="session-1",
        origin_turn_id="turn-1",
        origin_assistant_id="assistant-1",
        agent_id="default",
        repo="/tmp/openprogram",
        worktree_id="wt-1",
        base_sha="1" * 40,
        candidate_sha="2" * 40,
        changed_paths=("openprogram/example.py",),
        pre_update_evidence=("pytest:tests/unit/example.py",),
        goal="Add the requested behavior",
        assertions=("The public entry returns the new result",),
    )


def test_concurrent_create_has_one_winner(tmp_path: Path) -> None:
    def create(update_id: str) -> str:
        try:
            SelfUpdateStore(tmp_path).create(_request(update_id))
        except ActiveUpdateError:
            return "active"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("su_a", "su_b")))

    assert sorted(results) == ["active", "created"]
    active = SelfUpdateStore(tmp_path).load_active()
    assert active is not None
    assert active.request.update_id in {"su_a", "su_b"}
