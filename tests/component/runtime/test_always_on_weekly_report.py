"""The weekly health report reads real stores and changes nothing."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time
import uuid

from scripts.testing.always_on_weekly_report import build_report


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _world(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session import session_store

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    session_store.shared._default_store = None
    agent_id = "weekly" + uuid.uuid4().hex[:8]
    manager.create(agent_id, name="Weekly")
    session_id = "weekly-" + uuid.uuid4().hex[:8]
    default_db().create_session(session_id, agent_id)
    return state, agent_id, session_id


def test_report_finds_what_needs_attention_and_writes_nothing(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on import store

    state, agent_id, session_id = _world(tmp_path, monkeypatch)
    now = time.time()
    store.update_state(session_id, joined_at=now)
    store.append_wake_log(session_id, {"at": now - 60, "reason": "schedule", "outcome": "failed"})
    store.append_wake_log(session_id, {"at": now - 30, "reason": "schedule", "outcome": "nothing_new",
                                       "cost": {"input_tokens": 900, "output_tokens": 5, "usd": 0.25}})
    store.append_wake_log(session_id, {"at": now - 30 * 86400, "outcome": "failed"})
    report = store.add_report(session_id, title="Stuck", body="x",
                              deliveries=[{"channel": "telegram", "status": "sending"}])
    store.update_report(session_id, report["id"], at=now - 2 * 3600)
    default_db().append_run_rows(session_id, [
        {"t": "executions", "r": {"execution_id": "e1", "status": "paused", "created_at": now - 50}},
        {"t": "execution_agent_turn_inputs", "r": {
            "execution_id": "e1", "payload_json": '{"request": {"speaker_id": "runtime/always_on"}}'}},
        {"t": "executions", "r": {"execution_id": "chat", "status": "paused", "created_at": now - 50}},
        {"t": "executions", "r": {"execution_id": "e2", "status": "running", "created_at": now - 50}},
        {"t": "execution_events", "r": {"sequence": 7, "execution_id": "e2",
                                        "kind": "execution.restart.requested", "payload_json": "{}"}},
    ])
    before = _fingerprint(state)

    text = build_report(state, now)

    assert _fingerprint(state) == before
    assert f"{agent_id}: failed 1, nothing_new 1" in text
    assert "$0.2500 over 1 check-ins" in text and "largest prompt 900 tokens" in text
    assert "Unsettled restart intents: 1" in text
    assert "scheduled runs paused with no intent: 1" in text
    assert "Deliveries sending for over an hour: 1" in text
    assert text.splitlines()[-1].startswith("NEEDS ATTENTION")


def test_report_reads_tasks_handoffs_memory_and_run_lengths(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on import store
    from openprogram.runtime.scheduler import task_runs
    from openprogram.state.paths import get_memory_dir

    state, _agent_id, session_id = _world(tmp_path, monkeypatch)
    now = time.time()
    store.update_state(session_id, joined_at=now)
    task_runs.record("task-1", task_runs.FAILED, "broke")
    task_runs.record("task-1", task_runs.QUIET)
    store.save_handoffs({"late": {"phase": "waiting", "at": now - 2 * 86400},
                         "finished": {"phase": "done", "at": now - 2 * 86400},
                         "recent": {"phase": "running", "at": now - 60}})
    get_memory_dir().mkdir(parents=True, exist_ok=True)
    (get_memory_dir() / "notes.md").write_text("x" * 321, encoding="utf-8")
    default_db().append_run_rows(session_id, [
        {"t": "executions", "r": {"execution_id": "s", "status": "completed", "created_at": now - 100,
                                  "terminal_at": now - 10}},
        {"t": "execution_agent_turn_inputs", "r": {
            "execution_id": "s", "payload_json": '{"request": {"scheduled_task": {"id": "task-1"}}}'}},
        {"t": "executions", "r": {"execution_id": "c", "status": "completed", "created_at": now - 45,
                                  "terminal_at": now - 5}},
        {"t": "execution_agent_turn_inputs", "r": {
            "execution_id": "c", "payload_json": '{"request": {"speaker_id": "runtime/always_on"}}'}},
    ])

    text = build_report(state, now)

    assert "- failed 1, quiet 1" in text
    assert "Unfinished for over 24 hours: 1" in text
    assert "Memory size: 321 bytes" in text
    assert "Scheduled run: 90 s" in text and "Check-in: 40 s" in text


def test_a_settled_intent_and_a_quiet_week_are_healthy(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on import store

    state, _agent_id, session_id = _world(tmp_path, monkeypatch)
    store.update_state(session_id, joined_at=time.time())
    default_db().append_run_rows(session_id, [
        {"t": "executions", "r": {"execution_id": "e1", "status": "completed", "created_at": 1.0}},
        {"t": "execution_events", "r": {"sequence": 7, "execution_id": "e1",
                                        "kind": "execution.restart.requested", "payload_json": "{}"}},
        {"t": "execution_events", "r": {"sequence": 8, "execution_id": "e1", "kind": "execution.restart.settled",
                                        "payload_json": '{"request_sequence": 7}'}},
    ])
    text = build_report(state, time.time())
    assert "Unsettled restart intents: 0" in text
    assert text.splitlines()[-1] == "HEALTHY"
