"""Background-job completion notices (agent/job/notices.py).

  * only a background job that completed or failed is queued, once
  * reading the result with job_output drops the notice for good
  * an idle watch that hands back the reply a job ended on drops its notice
  * a busy conversation keeps the notice; an idle one gets one turn for
    every job that finished meanwhile
  * background agents in the same session do not count as busy
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.jobs import notices
from openprogram.runtime.jobs.store import save_job
from openprogram.runtime.jobs.types import Job, JobStatus


@pytest.fixture
def session(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    s.create_session("p1", "main", title="caller")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.commit_turn("p1", "init")
    monkeypatch.setattr(notices, "_due", set())
    monkeypatch.setattr(notices, "_seeded", True)
    return s


def _job(job_id="j1", **kw) -> Job:
    base = dict(id=job_id, parent_session_id="p1", prompt="look into X", agent_id="main",
                status=JobStatus.COMPLETED, wait=False, head_id="h1",
                result_text="X is fine", label="scout")
    base.update(kw)
    job = Job(**base)
    save_job("p1", job)
    return job


def _capture_dispatch(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    runner = SimpleNamespace(_dispatch_followup=lambda *jobs: calls.append(jobs))
    monkeypatch.setattr("openprogram.runtime.jobs.get_runner", lambda: runner)
    return calls


def _pending(session_id="p1") -> list[str]:
    path = notices._path(session_id)
    return [e["job_id"] for e in notices._load(path)[0]] if path.exists() else []


def test_queues_only_finished_background_jobs(session):
    assert notices.queue(_job("w", wait=True)) is False
    assert notices.queue(_job("c", status=JobStatus.CANCELLED)) is False
    assert notices.queue(_job("ok")) is True
    assert notices.queue(_job("bad", status=JobStatus.ERRORED, error="boom")) is True
    assert notices.queue(_job("ok")) is False  # a second projection of the same end
    assert _pending() == ["ok", "bad"]
    assert notices._due == {"p1"}


def test_job_output_read_drops_the_notice(session, monkeypatch):
    job = _job()
    notices.queue(job)
    notices.consume(job)
    assert _pending() == []
    assert notices.queue(job) is False
    calls = _capture_dispatch(monkeypatch)
    notices.sweep()
    assert calls == []


def test_reading_the_reply_a_job_ended_on_drops_its_notice(session):
    notices.queue(_job("a", head_id="h1"))
    notices.queue(_job("b", head_id="h2"))
    notices.consume_reply("p1", "elsewhere", "h1")  # another conversation's node
    assert _pending() == ["a", "b"]
    notices.consume_reply("p1", "p1", "h1")
    assert _pending() == ["b"]
    assert notices.queue(_job("a", head_id="h1")) is False


def test_busy_session_waits_then_one_turn_for_all(session, monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    busy = {"on": True}
    monkeypatch.setattr(notices, "_busy", lambda sid, jobs: busy["on"])
    notices.queue(_job("a"))
    notices.queue(_job("b"))
    notices.sweep()
    assert calls == [] and _pending() == ["a", "b"]
    busy["on"] = False
    notices.sweep()
    assert [[j.id for j in jobs] for jobs in calls] == [["a", "b"]]
    assert _pending() == [] and notices._due == set()
    # Delivered jobs never notify again.
    assert notices.queue(_job("a")) is False


def test_background_agents_do_not_make_the_session_busy(session, monkeypatch):
    running = _job("still", status=JobStatus.RUNNING)
    done = _job("done")
    records = [SimpleNamespace(execution_id=running.id)]
    store = SimpleNamespace(list_nonterminal=lambda session_id=None: records)
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    assert notices._busy("p1", [done]) is False
    records.append(SimpleNamespace(execution_id="chat-turn"))
    assert notices._busy("p1", [done]) is True


def test_dispatcher_still_running_keeps_the_notice(session, monkeypatch):
    _job("boss", status=JobStatus.RUNNING)
    child = _job("child", parent_job_id="boss")
    store = SimpleNamespace(list_nonterminal=lambda session_id=None: [])
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    assert notices._busy("p1", [child]) is True


def test_notice_text_marks_itself_and_carries_replies():
    # A background spawn (not waited on): its card carries the report.
    spawn = Job(id="s1", parent_session_id="p1", prompt="research", agent_id="main",
                status=JobStatus.COMPLETED, attach_pointer_id="ap1", label="scout", wait=False)
    reply = Job(id="r1", parent_session_id="p2", prompt="ask", agent_id="main",
                status=JobStatus.COMPLETED, head_id="hb", result_text="B says yes")
    text = notices.notice_text([spawn, reply])
    assert "not a message from the user" in text
    assert "Agent \"scout\" finished (job s1)." in text
    # No card says it renders the report itself: the result travels here.
    assert "attached above" not in text
    assert "Reply from branch p2:hb:" in text and "B says yes" in text
    assert "send_message(to=\"p2:hb\")" in text


def test_notice_items_give_each_job_its_row():
    # A background spawn (not waited on): its card carries the report.
    spawn = Job(id="s1", parent_session_id="p1", prompt="research", agent_id="main",
                status=JobStatus.COMPLETED, attach_pointer_id="ap1", label="scout", wait=False)
    reply = Job(id="r1", parent_session_id="p2", prompt="ask", agent_id="main",
                status=JobStatus.ERRORED, head_id="hb", error="boom", label="frontend")
    text = notices.notice_text([spawn, reply])
    items = notices.notice_items([spawn, reply])
    assert [(i["kind"], i["name"], i["failed"]) for i in items] == [
        ("report", "scout", False), ("reply", "frontend", True)]
    assert items[1]["address"] == "p2:hb"
    # The chat finds each job's part of the text by its first line.
    assert all(i["line"] in text.split("\n") for i in items)


def test_a_notice_turn_cut_off_by_a_restart_counts_as_answered(session, monkeypatch):
    """A reply held on a message passed on (``hold``) waits for the passer's
    notice turn to end. A restart cuts that turn off before it records its
    end; the next Worker counts it answered, so the held reply goes on."""
    path = notices._path("p1")
    with notices._lock:
        notices._write(path, [], ["c1"], ["older"])
    monkeypatch.setattr(notices, "_seeded", False)
    notices._seed()
    assert notices._answered(path) == ["older", "c1"]
    assert notices._load(path) == ([], ["c1"])
