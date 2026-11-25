"""ablation_target on a tiny experiment repository: the metric really runs in each
worktree; the splitter and ablator are scripted."""

import json
import shlex
import subprocess
import sys

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research import checks
from auto_research.tasks.discovery import ablation as wf

TRAIN = """def augment(x):
    return [v * 2 for v in x]


def regularize(w):
    return w * 0.5


def main():
    data = augment([1, 2, 3])
    loss = 10 - sum(data) * 0.1 - regularize(4)
    print(f"val_loss: {loss:.4f}")


main()
"""


def spec():
    return f"""# Experiment loop
Approved: yes
Edit: train.py
Command: {shlex.quote(sys.executable)} train.py
Metric: val_loss
Direction: lower
Budget: 1 min
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
    (repo / "workflow" / "experiment.md").write_text(spec())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo


def edit(quote, replacement, baseline="identity"):
    return {"quote": quote, "replacement": replacement, "baseline": baseline, "why": "关掉它"}


AUGMENT = edit("    return [v * 2 for v in x]\n", "    return list(x)\n")
REGULARIZE = edit("    return w * 0.5\n", "    return 0\n", "constant")


def test_named_components_ablated_against_the_best_kept_run(paper):
    kept = git(paper, "rev-parse", "--short=7", "HEAD")
    results = ("commit\tmetric\tstatus\tseconds\tdescription\n"
               f"{kept}\t6.8\tkeep\t1\tbaseline\n"
               "abc1234\t7.5\tdiscard\t1\ttried more\n")
    (paper / "workflow" / "results.tsv").write_text(results)
    replies = {wf.ablator: [AUGMENT, edit("    return [v * 2 for v in x]\n", "    return 0\n"), REGULARIZE]}
    with script_roles(replies) as calls:
        out = json.loads(wf.ablation_target(str(paper), components="augment, reg=train.py:regularize"))

    asks = calls[wf.ablator]
    assert len(asks) == 3 and "   6|     return w * 0.5" in asks[1].text
    assert "not inside lines 5-6" in asks[2].text                     # the retry is told why
    assert out["status"] == "PASS" and out["reference"] == 6.8
    comps = out["components"]
    assert [c["name"] for c in comps] == ["reg", "augment"]
    assert comps[0]["metric"] == pytest.approx(8.8) and comps[0]["contribution"] == pytest.approx(2.0)
    assert comps[1]["impact"] == pytest.approx(0.6) and comps[1]["branch"] == "ablate/augment"
    assert (comps[0]["start"], comps[0]["end"]) == (5, 6)
    assert "reg" in out["suggestion"] and "第 5-6 行" in out["suggestion"]

    lines = (paper / "workflow" / "results.tsv").read_text().splitlines()
    assert lines[0].split("\t") == ["commit", "metric", "status", "seconds", "description", "branch", "parent"]
    assert lines[1] == f"{kept}\t6.8\tkeep\t1\tbaseline\t\t"
    added = [dict(zip(lines[0].split("\t"), x.split("\t"))) for x in lines[3:]]
    assert [(r["branch"], r["status"], r["description"], r["parent"]) for r in added] == [
        ("ablate/augment", "discard", "ablation: augment", kept), ("ablate/reg", "discard", "ablation: reg", kept)]
    assert added[1]["metric"] == "8.8"
    assert git(paper, "show", f"{added[1]['commit']}:train.py").count("return 0") == 1
    assert git(paper, "log", "-1", "--format=%s", "ablate/reg") == "ablation: reg"
    assert git(paper, "worktree", "list").count("\n") == 0                     # only the main tree is left
    assert (paper / "train.py").read_text() == TRAIN
    assert checks.experiment(paper, None) == []
    report = open(out["report"], encoding="utf-8").read()
    assert "影响最大的是 **reg**" in report and "`train.py:5-6`" in report

    # A second run keeps the old branches and takes new names.
    with script_roles({wf.ablator: [AUGMENT]}):
        again = json.loads(wf.ablation_target(str(paper), components="augment"))
    assert again["components"][0]["branch"] == "ablate/augment-2"


def test_splitter_proposes_and_head_is_the_reference(paper):
    split = {"components": [
        {"name": "augment", "file": "train.py", "start": 1, "end": 2, "why": "数据增强"},
        {"name": "regularize", "file": "train.py", "start": 5, "end": 6, "why": "正则"},
        {"name": "ghost", "file": "train.py", "start": 40, "end": 60, "why": "不存在"},
    ]}
    with script_roles({wf.splitter: [split], wf.ablator: [AUGMENT, edit("    return w * 0.5\n", "    return w *\n"),
                                                         edit("    return w * 0.5\n", "    return (w\n")]}) as calls:
        out = json.loads(wf.ablation_target(str(paper), out_dir=str(paper / "o")))
    assert "  11|     loss = 10 - sum(data) * 0.1 - regularize(4)" in calls[wf.splitter][0].text
    assert out["reference"] == pytest.approx(6.8)
    comps = {c["name"]: c for c in out["components"]}
    assert set(comps) == {"augment", "regularize"}                    # ghost is outside the file
    assert comps["augment"]["impact"] == pytest.approx(0.6)
    assert comps["regularize"]["status"] == "invalid" and comps["regularize"]["branch"] == ""
    assert out["status"] == "PASS_WITH_NOTES"
    record = json.load(open(out["json"], encoding="utf-8"))
    assert "does not parse" in next(c for c in record["components"] if c["name"] == "regularize")["note"]
    rows = (paper / "workflow" / "results.tsv").read_text().splitlines()
    assert len(rows) == 2 and rows[1].split("\t")[-1] == ""           # no kept parent
    trail = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]][:4] == ["spec", "reference", "split", "check split"]


def test_component_parsing_and_budget():
    sources = {"train.py": TRAIN}
    assert wf.parse_components("x=train.py:1-2, main", sources) == [
        {"name": "x", "file": "train.py", "start": 1, "end": 2, "why": ""},
        {"name": "main", "file": "train.py", "start": 9, "end": 12, "why": ""}]
    for bad in ("x=train.py:3-99", "x=other.py:1-2", "nothing", "train.py:1-2", "a=train.py:1-2, a=train.py:3-4"):
        with pytest.raises(ValueError):
            wf.parse_components(bad, sources)
    assert wf.budget_seconds("5 min")[0] == 300 and wf.budget_seconds("90s")[0] == 90
    assert wf.budget_seconds("1.5 hours")[0] == 5400 and wf.budget_seconds("soon")[0] == 600
    assert wf.apply_edit(TRAIN, {"file": "train.py", "start": 1, "end": 2}, "v * 2", "v")[0] is not None
    assert "occurs 0 times" in wf.apply_edit(TRAIN, {"file": "train.py", "start": 1, "end": 2}, "zzz", "v")[1]
