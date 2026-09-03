from __future__ import annotations

import multiprocessing
from pathlib import Path


def _create_update(root: str, update_id: str, start, results) -> None:
    from openprogram.self_update import (
        ActiveUpdateError,
        SelfUpdateStore,
        UpdateRequest,
    )

    request = UpdateRequest(
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
    start.wait(timeout=10)
    try:
        SelfUpdateStore(Path(root)).create(request)
    except ActiveUpdateError:
        results.put("active")
    else:
        results.put("created")


def test_processes_cannot_create_two_active_updates(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_create_update, args=(str(tmp_path), update_id, start, results)
        )
        for update_id in ("su_a", "su_b")
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        observed = sorted(results.get(timeout=15) for _ in processes)
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        results.close()

    assert observed == ["active", "created"]
