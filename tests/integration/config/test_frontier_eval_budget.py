"""The launcher stops Harbor once the job's attempts reach the budget.

A stand-in ``harbor`` writes one finished trial per tick into the job
directory and records the signal it is stopped with; with ``retry`` it
deletes each trial's folder, as Harbor does before retrying it, after its
agent wrote the attempt's cost to the ledger.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scripts.benchmarks import frontier_eval as fe

FAKE_HARBOR = r"""
import json, shutil, signal, sys, time
from pathlib import Path
job, record = Path(sys.argv[1]), Path(sys.argv[2])
retry = sys.argv[3:] == ["retry"]
ledger = job.parent / f"{job.name}.attempts.jsonl"
def stop(signum, frame):
    record.write_text(signal.Signals(signum).name)
    sys.exit(130)
signal.signal(signal.SIGINT, stop)
for i in range(200):
    trial = job / f"t{i}"
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "result.json").write_text(json.dumps({
        "trial_name": f"t{i}", "task_name": "terminal-bench/x",
        "finished_at": "2026-11-12T01:00:00Z",
        "agent_result": {"cost_usd": 0.4}}))
    if retry:
        with ledger.open("a") as handle:
            handle.write(json.dumps({"trial": "t", "attempt": f"a{i}", "cost_usd": 0.4}) + "\n")
        shutil.rmtree(trial)
    time.sleep(0.05)
record.write_text("finished")
"""


def _fake(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_harbor.py"
    script.write_text(FAKE_HARBOR, encoding="utf-8")
    return [sys.executable, str(script)]


def test_harbor_is_interrupted_once_the_budget_is_spent(tmp_path):
    job, record = tmp_path / "jobs" / "j1", tmp_path / "record"
    status = fe.run_harbor(_fake(tmp_path) + [str(job), str(record)], env=dict(os.environ),
                           job_dir=job, budget_usd=1.0, poll_sec=0.1)
    assert status == 130
    assert record.read_text() == "SIGINT"
    trials = len(list(job.glob("*/result.json")))
    assert 3 <= trials < 200


def test_without_a_budget_harbor_runs_to_the_end(tmp_path):
    job, record = tmp_path / "jobs" / "j1", tmp_path / "record"
    script = _fake(tmp_path)
    Path(script[1]).write_text(FAKE_HARBOR.replace("range(200)", "range(5)"), encoding="utf-8")
    status = fe.run_harbor(script + [str(job), str(record)], env=dict(os.environ),
                           job_dir=job, budget_usd=None, poll_sec=0.1)
    assert status == 0 and record.read_text() == "finished"
    assert json.loads((job / "t4" / "result.json").read_text())["trial_name"] == "t4"


def test_attempts_harbor_deleted_to_retry_them_count_toward_the_budget(tmp_path):
    job, record = tmp_path / "jobs" / "j1", tmp_path / "record"
    status = fe.run_harbor(_fake(tmp_path) + [str(job), str(record), "retry"],
                           env=dict(os.environ), job_dir=job, budget_usd=1.0, poll_sec=0.1)
    assert status == 130 and record.read_text() == "SIGINT"
    assert not list(job.glob("*/result.json"))
    assert 3 <= len(fe.ledger_path(job).read_text().splitlines()) < 200
