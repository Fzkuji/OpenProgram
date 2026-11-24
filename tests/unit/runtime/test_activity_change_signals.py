"""Activity's lists, the pending inbox and the Scheduler's tasks are read
again when their state changes, not on a timer: each store signals its topic
(``host/_platform/changes.py``) where it writes."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.host._platform import changes


@pytest.fixture
def heard():
    listeners = []

    def listen(*topics: str):
        listener = changes.listen(*topics)
        listeners.append(listener)
        return listener

    yield listen
    for listener in listeners:
        listener.close()


def test_topic_sorting_signals_each_batch_and_its_end(heard, monkeypatch, tmp_path):
    from openprogram.runtime.conversation import topics
    from openprogram.runtime.execution.projections.activity_runs import Ask

    asks = [Ask(message_id=f"m{n}", text=f"question {n}") for n in range(topics._BATCH + 1)]
    monkeypatch.setattr(topics, "_ask_model", lambda _book, _previous, batch: ["Deploy"] * len(batch))
    written = []
    assert topics.sort_asks(tmp_path, asks, lambda: written.append(1))
    assert len(written) == 2

    class Database:
        def read_index(self, _sid, read):
            return asks

        def _session_dir(self, _sid):
            return tmp_path

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: Database())
    listener = heard("runs")
    topics._running.add("c1")
    topics._sort_session("c1")
    assert listener.wait(0) == {"runs:c1"}
    assert "c1" not in topics._running


def test_a_job_write_signals_every_conversation_that_lists_it(heard, tmp_path, monkeypatch, request):
    from openprogram.runtime.jobs.store import save_job, update_job_status
    from openprogram.runtime.jobs.types import Job, JobStatus
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    request.addfinalizer(store.close)
    monkeypatch.setattr("openprogram.state.store.session.session_store.shared._default_store", store, raising=False)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    store.create_session("work", "main", title="work")

    listener = heard("runs")
    save_job("work", Job(id="j1", parent_session_id="work", prompt="go", agent_id="helper"), _mirror=False)
    assert listener.wait(0) == {"runs:work"}
    update_job_status("work", "j1", JobStatus.RUNNING)
    assert "runs:work" in listener.wait(0)


def test_a_helpers_usage_signals_its_roster_and_a_conversations_own_does_not(heard, tmp_path):
    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.ledger import UsageLedger

    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    listener = heard("roster")
    ledger.append(UsageEvent(session_id="c1", provider="p", model_id="m"))
    assert listener.wait(0) == set()
    ledger.append(UsageEvent(session_id="helper", parent_session_id="c1", job_id="j1", provider="p", model_id="m"))
    assert listener.wait(0) == {"roster:c1", "roster:helper"}


def test_reports_and_a_persons_message_signal_the_inbox(heard, monkeypatch, tmp_path):
    from openprogram.runtime.conversation.always_on import pending, store

    monkeypatch.setattr(store, "_reports_path", lambda _sid: tmp_path / "reports.jsonl")
    listener = heard("inbox")
    store._write_reports("c1", [{"id": "r1", "kind": "approval", "at": 1.0}])
    assert listener.wait(0) == {"inbox:c1"}

    pending.person_wrote("c1", {"role": "user", "display": "runtime"})
    assert listener.wait(0) == set()
    pending.person_wrote("c1", {"role": "user"})
    assert listener.wait(0) == {"inbox:c1"}


def test_a_scheduled_tasks_run_signals_work(heard, monkeypatch, tmp_path: Path):
    from openprogram.runtime.scheduler import task_runs

    monkeypatch.setenv("OPENPROGRAM_SCHEDULER_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(task_runs, "_path", lambda: str(tmp_path / "task-runs.json"))
    listener = heard("work")
    task_runs.record("t1", task_runs.QUIET)
    assert listener.wait(0) == {"work"}
