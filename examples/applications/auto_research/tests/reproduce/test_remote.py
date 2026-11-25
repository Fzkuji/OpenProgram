"""remote_run over ssh and Slurm with subprocess.run replaced: nothing leaves the machine."""

import json
import subprocess

import pytest

from auto_research.tasks.reproduce import remote as rm
from auto_research.tasks.reproduce import remote_run

from .repo import commit

REAL = subprocess.run


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


@pytest.fixture
def remote(monkeypatch):
    sent, replies = [], []

    def fake(args, *a, **kw):
        if args[0] in ("ssh", "scp"):
            sent.append(list(args))
            reply = replies.pop(0)
            if args[0] == "scp" and reply.returncode == 0:
                open(args[-1], "w", encoding="utf-8").write(reply.stdout)
            return reply
        assert args[0] == "git", args
        return REAL(args, *a, **kw)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)
    return sent, replies


def test_ssh_runs_the_committed_code_and_reads_the_metric(paper, remote):
    sent, replies = remote
    sha = commit(paper, "run")
    replies.append(Done(0, "epoch 1\nval_loss: 0.31\n"))
    out = json.loads(remote_run(str(paper), "gpu-box"))
    assert out["status"] == "PASS" and out["metric"] == 0.31 and out["commit"] == sha
    assert sent == [["ssh", "-o", "BatchMode=yes", "gpu-box",
                     f"cd ~/work/paper && git fetch --quiet && git checkout --quiet {sha} && python train.py"]]
    assert "val_loss: 0.31" in open(out["log"], encoding="utf-8").read()
    assert "ssh 直接运行" in open(out["report"], encoding="utf-8").read()


def test_ssh_failure_and_missing_metric_fail(paper, remote):
    _, replies = remote
    commit(paper, "run")
    replies.append(Done(1, "", "CUDA out of memory"))
    out = json.loads(remote_run(str(paper), "gpu-box", command="python train.py --big"))
    assert out["status"] == "FAIL" and "CUDA out of memory" in out["note"]
    replies.append(Done(0, "done\n"))
    out = json.loads(remote_run(str(paper), "gpu-box"))
    assert out["status"] == "FAIL" and out["metric"] is None and "val_loss" in out["note"]


def test_slurm_submits_polls_and_fetches_the_log(paper, remote):
    sent, replies = remote
    sha = commit(paper, "run")
    replies += [Done(0, "4242\n"), Done(0, "RUNNING\n"), Done(0, "PENDING\n"), Done(0, ""),
                Done(0, "COMPLETED|0:0\n"), Done(0, "loading\nval_loss: 0.29\n")]
    out = json.loads(remote_run(str(paper), "me@cluster", scheduler="slurm"))
    assert out["status"] == "PASS" and out["metric"] == 0.29 and out["job"] == "4242" and out["state"] == "COMPLETED"
    submit = sent[0][-1]
    assert submit.startswith(f"cd ~/work/paper && git fetch --quiet && git checkout --quiet {sha} && ")
    assert "sbatch --parsable --output=.openprogram_runs/slurm-%j.out --wrap='python train.py'" in submit
    assert [s[-1] for s in sent[1:4]] == ["squeue -h -j 4242 -o %T"] * 3
    assert sent[4][-1].startswith("sacct -n -X -P -j 4242")
    assert sent[5][:4] == ["scp", "-q", "-o", "BatchMode=yes"]
    assert sent[5][4] == "me@cluster:work/paper/.openprogram_runs/slurm-4242.out"


def test_slurm_job_that_fails(paper, remote):
    _, replies = remote
    commit(paper, "run")
    replies += [Done(0, "77;cluster\n"), Done(0, ""), Done(0, "FAILED|1:0\n"), Done(0, "Traceback\n")]
    out = json.loads(remote_run(str(paper), "me@cluster", scheduler="slurm"))
    assert out["status"] == "FAIL" and out["state"] == "FAILED" and out["job"] == "77"


def test_arguments_are_checked(paper, remote):
    commit(paper, "run")
    with pytest.raises(ValueError, match="scheduler"):
        remote_run(str(paper), "gpu-box", scheduler="pbs")
    with pytest.raises(ValueError, match="ssh destination"):
        remote_run(str(paper), "-oProxyCommand=x")
    (paper / "workflow" / "experiment.md").write_text("Command: python train.py\nMetric: val_loss\n")
    with pytest.raises(ValueError, match="Remote"):
        remote_run(str(paper), "gpu-box")
