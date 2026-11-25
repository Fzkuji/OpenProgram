"""hparam_sweep on a tiny experiment repository: every trial really runs in the sweep's
worktree; Optuna is either absent (the local search) or replaced by a small fake."""

import json
import shlex
import subprocess
import sys
import types

import pytest

from auto_research import checks
from auto_research.tasks.sweep import space as sp
from auto_research.tasks.sweep import sweep as wf

TRAIN = """import argparse, json, math, os
p = argparse.ArgumentParser()
p.add_argument("--lr", type=float, default=0.1)
p.add_argument("--warm-up", dest="warmup", type=int, default=0)
a = p.parse_args()
cfg = json.load(open("config.json"))
extra = json.load(open(os.environ["AR_PARAMS"])) if os.environ.get("AR_PARAMS") else {}
if cfg["optim"]["batch_size"] == 128:
    raise SystemExit("out of memory")
loss = (math.log10(a.lr) + 2) ** 2 + cfg["optim"]["batch_size"] / 100 + extra.get("noise", 0)
print(f"val_loss: {loss:.6f}")
"""
CONFIG = {"optim": {"batch_size": 64}, "epochs": 3}


def spec(edit="train.py, config.json", budget="1 min"):
    return f"""# Experiment loop
Approved: yes
Edit: {edit}
Command: {shlex.quote(sys.executable)} train.py
Metric: val_loss
Direction: lower
Budget: {budget}
Stop: 30 runs
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "train.py").write_text(TRAIN)
    (repo / "config.json").write_text(json.dumps(CONFIG))
    (repo / "workflow" / "experiment.md").write_text(spec())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo


@pytest.fixture
def no_optuna(monkeypatch):
    monkeypatch.setitem(sys.modules, "optuna", None)          # import optuna raises ImportError


def test_sweep_records_every_trial_on_its_branch(paper, no_optuna):
    kept = git(paper, "rev-parse", "--short=7", "HEAD")
    (paper / "workflow" / "results.tsv").write_text(f"commit\tmetric\tstatus\tseconds\tdescription\n"
                                                    f"{kept}\t1.5\tkeep\t1\tbaseline\n")
    out = json.loads(wf.hparam_sweep(str(paper), "lr=1e-4..1e-1 log; optim.batch_size=16,64,128; noise=0..0.01",
                                     trials="8", budget="120"))
    assert out["trials"] == 8 and out["sampler"] == "Latin hypercube + local search"
    header, rows = wf.rc.results(paper)
    assert header[-2:] == ["branch", "parent"]
    sweep = [r for r in rows if r["branch"] == out["branch"]]
    assert len(sweep) == 8 and all(r["parent"] == kept for r in sweep)
    assert out["branch"].startswith("sweep/")
    # crashes are recorded, keeps improve on the reference and on each other, as the experiment gate checks
    crashed = [r for r in sweep if "optim.batch_size=128" in r["description"]]
    assert all(r["status"] == "crash" and r["metric"] == "" for r in crashed)
    assert out["crashed"] == len(crashed)
    problems = checks.branches(paper, rows, lower=True)
    assert all("none is merged" in p for p in problems)
    kept_values = [float(r["metric"]) for r in sweep if r["status"] == "keep"]
    assert kept_values == sorted(kept_values, reverse=True) and all(v < 1.5 for v in kept_values)
    record = json.loads((wf.Path(out["json"])).read_text())
    assert record["routes"] == {"lr": ["cli --lr"], "optim.batch_size": ["config config.json", "env AR_PARAMS"],
                                "noise": ["env AR_PARAMS"]} or record["routes"]["lr"] == ["cli --lr", "env AR_PARAMS"]
    best = record["best"]
    # the trial's commit holds its config, and its metric is what the program computes from its parameters
    config = json.loads(git(paper, "show", f"{best['commit']}:config.json"))
    assert config["optim"]["batch_size"] == best["params"]["optim.batch_size"] and config["epochs"] == 3
    import math
    expected = (math.log10(best["params"]["lr"]) + 2) ** 2 + best["params"]["optim.batch_size"] / 100 \
        + best["params"]["noise"]
    assert abs(best["metric"] - expected) < 1e-5
    assert {x["name"] for x in record["importance"]} == {"lr", "optim.batch_size", "noise"}
    assert out["status"] == ("PASS_WITH_NOTES" if crashed else "PASS")
    report = (wf.Path(out["report"])).read_text()
    assert "# 超参数搜索" in report and "## 参数重要性" in report and "claim_stats" in report
    assert not list((paper.parent).glob("sweep-*"))
    assert len(git(paper, "worktree", "list").splitlines()) == 1


def test_parameters_reach_the_program_or_raise(paper, no_optuna):
    (paper / "train.py").write_text("\n".join("extra = {}" if x.startswith("extra =") else x
                                              for x in TRAIN.splitlines()))
    git(paper, "commit", "-qam", "no AR_PARAMS")
    with pytest.raises(ValueError, match="momentum cannot reach the program"):
        wf.hparam_sweep(str(paper), "momentum=0..1", trials="1")
    routes, configs, script = wf.delivery(paper, git(paper, "rev-parse", "HEAD"), wf.rc.spec(paper),
                                          sp.parse_space("warmup=0..5; epochs=1..9"))
    assert script == "train.py" and routes["warmup"][0] == "cli --warm-up" and routes["epochs"][0] == "config config.json"
    assert set(configs) == {"config.json"}


def test_budget_stops_the_sweep(paper, no_optuna):
    slow = TRAIN.replace("import argparse", "import time; time.sleep(1.2)\nimport argparse")
    (paper / "train.py").write_text(slow)
    git(paper, "commit", "-qam", "slow")
    out = json.loads(wf.hparam_sweep(str(paper), "lr=0.001..0.1 log", trials="5", budget="2"))
    assert out["trials"] < 5 and out["status"] == "PASS_WITH_NOTES"
    assert "总预算" in (wf.Path(out["report"])).read_text()


def test_inputs(paper):
    for bad, msg in [("", "space is empty"), ("lr", "not name=range"), ("lr=1..0", "smaller to a larger"),
                     ("lr=0..1 log", "above 0"), ("lr=0.1", "at least two choices"), ("lr=1,2;lr=2,3", "twice"),
                     ("lr=a..b", "two numbers"), ("1lr=1,2", "not a parameter name")]:
        with pytest.raises(ValueError, match=msg):
            sp.parse_space(bad)
    with pytest.raises(ValueError, match="trials"):
        wf.hparam_sweep(str(paper), "lr=0.1,0.2", trials="0")
    with pytest.raises(ValueError, match="budget"):
        wf.hparam_sweep(str(paper), "lr=0.1,0.2", budget="soon")
    s = sp.parse_space("lr=1e-4..1e-1 log; epochs=5..20; act=relu,gelu; flag=true,false")
    assert [p["type"] for p in s] == ["float", "int", "choice", "choice"] and s[3]["choices"] == [True, False]


def test_local_search_explores_then_refines():
    space = sp.parse_space("x=0.0..1.0; k=1..100 log; c=a,b")
    search = sp.LocalSearch(space, lower=True, trials=10)
    seen = []
    for _ in range(10):
        params, handle = search.ask()
        assert 0 <= params["x"] <= 1 and 1 <= params["k"] <= 100 and isinstance(params["k"], int)
        seen.append(sp.describe(params))
        search.tell(handle, abs(params["x"] - 0.3) + (params["c"] == "b"))
    assert len(set(seen)) == 10
    first = [p for p, _ in search.done[:search.initial]]
    assert sorted(int(p["x"] * search.initial) for p in first) == list(range(search.initial))   # one per stratum
    assert {p["c"] for p in first} == {"a", "b"}
    trials = [{"params": p, "metric": m} for p, m in search.done]
    ranked = sp.importance(space, trials, lower=True)
    assert {x["name"] for x in ranked} == {"x", "k", "c"} and all(0 <= (x["eta2"] or 0) <= 1 for x in ranked)
    assert sp.at_edge(space[0], 0.95) == "high" and sp.at_edge(space[1], 1) == "low" and sp.at_edge(space[2], "a") == ""


def fake_optuna():
    """Just enough of optuna's ask-and-tell API to drive OptunaTPE."""
    told = []

    class Trial:
        def __init__(self, dists):
            self.params = {k: (d.choices[0] if hasattr(d, "choices") else d.low) for k, d in dists.items()}

    class Study:
        def ask(self, dists):
            return Trial(dists)

        def tell(self, trial, value=None, state=None):
            told.append((trial.params, value, state))

    def dist(*args, log=False):
        return types.SimpleNamespace(low=args[0], high=args[1], log=log)

    mod = types.SimpleNamespace(
        logging=types.SimpleNamespace(WARNING=30, set_verbosity=lambda level: None),
        samplers=types.SimpleNamespace(TPESampler=lambda seed: ("tpe", seed)),
        create_study=lambda direction, sampler: Study(),
        distributions=types.SimpleNamespace(FloatDistribution=dist, IntDistribution=dist,
                                            CategoricalDistribution=lambda c: types.SimpleNamespace(choices=c)),
        trial=types.SimpleNamespace(TrialState=types.SimpleNamespace(FAIL="FAIL")))
    return mod, told


def test_optuna_is_used_when_installed(monkeypatch):
    mod, told = fake_optuna()
    monkeypatch.setitem(sys.modules, "optuna", mod)
    search = sp.sampler(sp.parse_space("lr=0.001..0.1 log; c=a,b"), lower=True, trials=3)
    assert search.name == "Optuna TPE"
    params, trial = search.ask()
    assert params == {"lr": 0.001, "c": "a"}
    search.tell(trial, 0.5)
    search.tell(trial, None)
    assert [t[1:] for t in told] == [(0.5, None), (None, "FAIL")]


def test_script_and_argparse_detection(paper):
    assert wf.command_script(paper, "python train.py --x 1") == ("train.py", True)
    assert wf.command_script(paper, "python train.py && echo done") == ("train.py", False)
    assert wf.argparse_options(TRAIN) == {"lr": "--lr", "warmup": "--warm-up"}
    assert wf.argparse_options("def (") == {}
