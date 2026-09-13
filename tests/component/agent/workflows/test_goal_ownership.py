from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from openprogram.agent.session_db import SessionDB


def test_ownership_is_shared_across_processes_and_released(tmp_path):
    from openprogram.programs.workflow.goal.ownership import goal_owner

    db = SessionDB(tmp_path / "sessions")
    script = (
        "import sys; from openprogram.agent.session_db import SessionDB; "
        "from openprogram.programs.workflow.goal.ownership import goal_owner\n"
        "with goal_owner(SessionDB(sys.argv[1]), 's') as owned: print(owned)\n"
    )
    def probe():
        return subprocess.run(
            [sys.executable, "-c", script, str(db.root_path)],
            text=True, capture_output=True, timeout=10, check=True,
        ).stdout.strip()
    with goal_owner(db, "s"):
        assert probe() == "False"
    assert probe() == "True"


def test_live_goal_owner_survives_sibling_startup(tmp_path, monkeypatch):
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui._exec_dag import reconcile_interrupted_runs

    db = SessionDB(tmp_path / "sessions")
    db.create_session("s", "main")
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    goal_pkg.save_goal("s", {"text": "task", "status": "running", "version": 0})
    db.update_session("s", status="running")
    ownership = importlib.import_module("openprogram.programs.workflow.goal.ownership")
    with ownership.goal_owner(db, "s") as acquired:
        assert acquired
        assert reconcile_interrupted_runs() == 0
        assert goal_pkg.load_goal("s")["status"] == "running"
        assert db.get_session("s")["status"] == "running"
        with ownership.goal_owner(SessionDB(tmp_path / "sessions"), "s") as second:
            assert not second
    assert reconcile_interrupted_runs() == 2
    assert goal_pkg.load_goal("s")["status"] == "paused_recoverable"


@pytest.mark.timeout(15)
def test_abrupt_owner_exit_releases_lock(tmp_path):
    from openprogram.programs.workflow.goal.ownership import goal_owner

    db = SessionDB(tmp_path / "sessions")
    script = (
        "import sys; from openprogram.agent.session_db import SessionDB; "
        "from openprogram.programs.workflow.goal.ownership import goal_owner\n"
        "with goal_owner(SessionDB(sys.argv[1]), 's') as owned:\n"
        " print(owned, flush=True)\n"
        " sys.stdin.read()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(db.root_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "True"
        with goal_owner(db, "s") as acquired:
            assert not acquired
        process.kill()
        process.wait(timeout=5)
        with goal_owner(db, "s") as acquired:
            assert acquired
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_public_goal_rejects_a_second_owner_before_model_work(tmp_path, monkeypatch):
    import openprogram.programs.workflow.goal as goal_pkg
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    ownership = importlib.import_module("openprogram.programs.workflow.goal.ownership")
    db = SessionDB(tmp_path / "sessions")
    db.create_session("s", "main")
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr("openprogram.agentic_programming.function.current_session_id", lambda: "s")
    with ownership.goal_owner(db, "s"):
        with pytest.raises(ValueError, match="already executing"):
            module.goal("do not start a duplicate")
