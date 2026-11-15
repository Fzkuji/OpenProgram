"""Activity's roster facts: one status set, subtree cost, outgoing sends.

All fake: Jobs in memory, the ledger is a stub. See
docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.jobs import agent_status
from openprogram.runtime.jobs.types import Job, JobStatus


def _job(job_id: str, **kw) -> Job:
    base = dict(id=job_id, parent_session_id="p1", prompt="task", agent_id="main",
                status=JobStatus.RUNNING, caller_session_id="p1")
    base.update(kw)
    return Job(**base)


@pytest.mark.parametrize("fields, wait_kind, stalled, expected", [
    ({}, None, False, "running"),
    ({}, "approval", False, "needs approval"),
    ({}, "ask", False, "needs input"),
    ({}, None, True, "stalled"),
    ({"status": JobStatus.COMPLETED}, None, False, "idle"),
    ({"status": JobStatus.CANCELLED, "reason_code": "cancel.user"}, None, False, "idle"),
    ({"status": JobStatus.CANCELLED, "reason_code": "budget.tree_cost_exhausted"}, None, False, "failed"),
    ({"status": JobStatus.ERRORED}, None, False, "failed"),
])
def test_one_status_set(fields, wait_kind, stalled, expected):
    status = agent_status.agent_status(_job("j1", **fields), wait_kind=wait_kind, stalled=stalled)
    assert status == expected and status in agent_status.STATUSES


class _Ledger:
    def __init__(self, costs, unknown=frozenset()):
        self.costs, self.unknown = costs, unknown

    def job_resource_usage(self, job_id):
        return {"cost_values": self.costs.get(job_id, []), "total_tokens": 100 if job_id in self.costs else 0,
                "unknown_cost_events": 1 if job_id in self.unknown else 0}


def test_cost_is_the_subtree(monkeypatch):
    jobs = [_job("j1"), _job("j2", parent_job_id="j1"), _job("j3", parent_job_id="j2")]
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs",
                        lambda sid: [j for j in jobs if j.parent_session_id == sid])
    usage = agent_status.subtree_usage(jobs[0], ledger=_Ledger({"j1": ["0.01"], "j2": ["0.02"], "j3": ["0.03"]}))
    assert usage == {"tokens": 300, "cost_usd": "0.060000"}
    unknown = agent_status.subtree_usage(jobs[0], ledger=_Ledger({"j1": ["0.01"]}, unknown={"j3"}))
    assert unknown["cost_usd"] is None


def test_outgoing_rows_are_unanswered_sends_to_other_conversations(monkeypatch):
    monkeypatch.setattr(agent_status, "_open_wait_kind", lambda _id: None)
    monkeypatch.setattr(agent_status, "_was_stalled", lambda _job: False)
    sent = _job("d1", parent_session_id="butler", message_origin="agent_delivery", label="Butler",
                status=JobStatus.QUEUED, created_at=2.0)
    working = _job("d2", parent_session_id="other", message_origin="agent_delivery", label="Scout",
                   created_at=3.0)
    rows = agent_status.outgoing_sends("p1", [
        working, sent,
        _job("d3", parent_session_id="butler", message_origin="agent_delivery", status=JobStatus.COMPLETED),
        _job("d4", parent_session_id="p1", message_origin="agent_delivery"),  # same conversation
        _job("h1", parent_session_id="other"),  # a helper, not a message
        _job("d5", parent_session_id="butler", caller_session_id="x", message_origin="agent_delivery"),
    ])
    assert [(r["job_id"], r["to"], r["status"], r["sender"]) for r in rows] == [
        ("d1", "Butler", "waiting", "p1"), ("d2", "Scout", "running", "p1")]


def test_roster_keys_every_job_and_reads_a_settled_helper_once(monkeypatch):
    monkeypatch.setattr(agent_status, "_open_wait_kind", lambda _id: None)
    monkeypatch.setattr(agent_status, "_was_stalled", lambda _job: False)
    monkeypatch.setattr(agent_status, "_settled", type(agent_status._settled)())
    monkeypatch.setattr("openprogram.runtime.jobs.agent_result._tool_calls", lambda job: 12 if job.id == "live" else 3)
    reads = []

    def usage(job, **_):
        reads.append(job.id)
        return {"tokens": 18400, "cost_usd": "0.020000"}

    monkeypatch.setattr(agent_status, "subtree_usage", usage)
    jobs = [
        _job("live"), _job("queued", status=JobStatus.QUEUED),
        _job("done", status=JobStatus.COMPLETED, completed_at=5.0),
        # Ended itself, but a helper under it still runs: its cost can grow.
        _job("parent", status=JobStatus.COMPLETED, completed_at=6.0), _job("child", parent_job_id="parent"),
        _job("sent", parent_session_id="butler", message_origin="agent_delivery", label="Butler", status=JobStatus.QUEUED),
    ]
    first = agent_status.roster("p1", jobs)
    assert first["agents"]["live"] == {"agent_status": "running", "queued": False,
                                       "usage": {"tokens": 18400, "cost_usd": "0.020000", "tool_calls": 12}}
    assert first["agents"]["queued"]["queued"] is True and first["agents"]["done"]["agent_status"] == "idle"
    assert [row["to"] for row in first["outgoing"]] == ["Butler"]
    reads.clear()
    agent_status.roster("p1", jobs)
    assert "done" not in reads and {"live", "parent", "child"} <= set(reads)
