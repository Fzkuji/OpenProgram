"""The campaign step, start and stop, with a fake scheduler and a fake task runner.
No real scheduled task is ever created: every test replaces the scheduler."""

import json
import subprocess
import sys
import types
from datetime import datetime

import pytest

from auto_research.tasks.campaign import workflow as cw
from auto_research.tasks.campaign import campaign_state, campaign_step, start_campaign, stop_campaign

SPEC = """# Campaign
Approved: {approved}
Goal: Find out whether usage forgetting transfers.
Tasks: {tasks}
Agent work: Continue the experiment loop per workflow/experiment.md.
Every: 0 */6 * * *
Budget: {budget}
Until: {until}
"""


class FakeScheduler:
    def __init__(self):
        self.tasks, self.created, self.deleted, self.updates = {}, [], [], []

    def create_task(self, *, title, task_type, prompt=None, command=None, cron=None, cwd=None, notes="", **kw):
        task = {"id": f"t{len(self.created) + 1}", "title": title, "type": task_type, "prompt": prompt,
                "cron": cron, "cwd": cwd, "notes": notes, "enabled": True}
        self.tasks[task["id"]] = task
        self.created.append(task)
        return task

    def update_task(self, task_id, patch, **kw):
        self.updates.append((task_id, patch))
        self.tasks[task_id].update(patch)
        return self.tasks[task_id]

    def delete_task(self, task_id, **kw):
        self.deleted.append(task_id)
        return self.tasks.pop(task_id, None) is not None

    def list_tasks(self):
        return list(self.tasks.values())

    def get_task(self, task_id):
        return self.tasks.get(task_id)


@pytest.fixture
def sched(monkeypatch):
    fake = FakeScheduler()
    module = types.ModuleType("openprogram.runtime.scheduler.service")
    for name in ("create_task", "update_task", "delete_task", "list_tasks", "get_task"):
        setattr(module, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "openprogram.runtime.scheduler.service", module)
    monkeypatch.setattr(cw, "scheduler", lambda: module)
    return fake


@pytest.fixture
def paper(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "workflow").mkdir()
    return tmp_path


def spec(paper, approved="yes", tasks="watch, research_map", budget="10", until=""):
    (paper / "workflow" / "campaign.md").write_text(
        SPEC.format(approved=approved, tasks=tasks, budget=budget, until=until), encoding="utf-8")


def spend(paper, name, cost):
    """A saved run of some workflow that cost this much, as ledger.spend reads it."""
    out = paper / "drafts" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps({"workflow": name, "finished": datetime.now().isoformat(),
                                              "usage": {"calls": 1, "cost": cost}}), encoding="utf-8")


@pytest.fixture
def runner(monkeypatch):
    calls = []

    def run(paper, name, cost=0.0):
        calls.append(name)
        if name == "research_map":
            raise RuntimeError("cartographer reply malformed")
        spend(paper, f"{name}-{len(calls)}", runner.cost)
        return {"task": name, "status": "PASS"}

    runner = types.SimpleNamespace(calls=calls, cost=0.5)
    monkeypatch.setattr(cw, "run_task", run)
    return runner


def test_not_approved_is_refused(paper, sched, runner):
    spec(paper, approved="no")
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "REFUSED" and "not approved" in out["stop_reason"]
    assert runner.calls == []
    assert start_campaign(str(paper))["status"] == "REFUSED" and sched.created == []


def test_missing_spec_is_created_from_the_template_and_refused(paper, sched, runner):
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "REFUSED"
    assert "Approved: no" in (paper / "workflow" / "campaign.md").read_text(encoding="utf-8")


def test_unknown_task_and_self_are_refused(paper, sched, runner):
    spec(paper, tasks="watch, campaign, nonsense")
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "REFUSED"
    assert "campaign itself" in out["stop_reason"] and "nonsense" in out["stop_reason"]


def test_step_isolates_task_errors_and_logs(paper, sched, runner):
    spec(paper)
    (paper / "workflow" / "world.json").write_text(json.dumps(
        {"proposals": [{"task": "consensus", "why": "x"}, {"task": "watch", "why": "y"}]}), encoding="utf-8")
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "RAN"
    assert [(r["task"], r["status"]) for r in out["ran"]] == [("watch", "PASS"), ("research_map", "ERROR")]
    assert "malformed" in out["ran"][1]["error"]
    assert out["spent"] == 0.5 and out["budget"] == 10.0 and out["stop_reason"] == ""
    assert "experiment loop" in out["next"]
    log = (paper / "workflow" / "campaign_log.md").read_text(encoding="utf-8")
    line = [l for l in log.splitlines() if l.startswith("- ") and " step " in l][0]
    assert "watch PASS, research_map ERROR" in line and "$0.50 of $10.00" in line
    assert "proposals open: 2 (consensus, watch)" in line
    json.loads(campaign_step(str(paper)))
    assert campaign_state(str(paper))["steps"] == 2


def test_spend_before_the_campaign_does_not_count(paper, sched, runner):
    spend(paper, "old", 50.0)
    spec(paper)
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "RAN" and out["spent"] == 0.5


def test_budget_stop_disables_the_scheduled_task(paper, sched, runner):
    spec(paper, budget="1")
    assert start_campaign(str(paper))["created"] is True
    runner.cost = 0.6
    assert json.loads(campaign_step(str(paper)))["status"] == "RAN"
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "STOPPED" and out["stop_reason"].startswith("budget reached")
    assert sched.updates == [("t1", {"enabled": False})]
    calls = len(runner.calls)
    again = json.loads(campaign_step(str(paper)))
    assert again["status"] == "STOPPED" and len(runner.calls) == calls
    state = campaign_state(str(paper))
    assert state["state"] == "stopped" and state["spent"] == 1.2 and "STOPPED" in state["last"][-1]


def test_until_date_stop(paper, sched, runner, monkeypatch):
    spec(paper, until="2026-01-31")
    start_campaign(str(paper))
    monkeypatch.setattr(cw, "now", lambda: datetime(2026, 2, 1, 9, 0))
    out = json.loads(campaign_step(str(paper)))
    assert out["status"] == "STOPPED" and "Until" in out["stop_reason"]
    assert out["ran"] == [] and runner.calls == []
    assert sched.tasks["t1"]["enabled"] is False


def test_until_day_itself_still_runs(paper, sched, runner, monkeypatch):
    spec(paper, until="2026-01-31")
    monkeypatch.setattr(cw, "now", lambda: datetime(2026, 1, 31, 23, 0))
    assert json.loads(campaign_step(str(paper)))["status"] == "RAN"


def test_start_stop_round_trip_and_idempotent_start(paper, sched, runner):
    spec(paper)
    first = start_campaign(str(paper))
    assert first == {"status": "RUNNING", "task_id": "t1", "created": True}
    task = sched.tasks["t1"]
    assert task["title"] == f"Auto Research campaign: {paper.name}" and task["type"] == "recurring"
    assert task["cron"] == "0 */6 * * *" and task["cwd"] == str(paper.resolve())
    for text in ("-a task=campaign", f"paper_dir={paper.resolve()}", "campaign/", "Never push",
                 "Never approve", "campaign_log.md", "experiment.md", "STOPPED"):
        assert text in task["prompt"]
    state = json.loads((paper / "workflow" / "campaign.json").read_text(encoding="utf-8"))
    assert state["task_id"] == "t1" and state["spend_at_start"] == 0 and state["started_at"]

    assert start_campaign(str(paper)) == {"status": "RUNNING", "task_id": "t1", "created": False}
    assert len(sched.created) == 1 and campaign_state(str(paper))["state"] == "running"

    assert stop_campaign(str(paper)) == {"status": "STOPPED", "deleted": True}
    assert sched.tasks == {} and campaign_state(str(paper))["state"] == "stopped"
    assert json.loads(campaign_step(str(paper)))["status"] == "STOPPED"

    again = start_campaign(str(paper))
    assert again["created"] is True and list(sched.tasks) == [again["task_id"]]


def test_no_scheduler_refuses_start(paper, runner, monkeypatch):
    monkeypatch.setattr(cw, "scheduler", lambda: None)
    spec(paper)
    assert start_campaign(str(paper)) == {"status": "REFUSED", "problems": ["this host has no scheduler"]}


def test_not_a_repository(tmp_path, sched):
    with pytest.raises(ValueError):
        campaign_step(str(tmp_path))
