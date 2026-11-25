"""claim_stats on a fixture project, the statistics against known values, and the
popper mode with a scripted falsifier."""

import json
import math
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.methods import claim_stats
from auto_research.tasks.methods import claims as cs
from auto_research.tasks.methods import significance as sig

CLAIMS = """# Claims
Approved: yes

C1: Ours beats the baseline on accuracy.
RQ: RQ1
C2: Ours beats the baseline in the loop.
RQ: RQ2
C3: The ablation shows the gate matters.
RQ: RQ3
"""
EVIDENCE = """id,claim,statement,source,script,method,value
E1,C1,Main table,results/main.csv,,,
E2,C2,Loop runs,workflow/results.tsv,,,
E3,C3,Ablation with gate,workflow/claims.md,,ours,0.9
E4,C3,Ablation without gate,workflow/claims.md,,no gate,0.8
"""
MAIN = "method,seed,accuracy\nours,1,0.80\nours,2,0.82\nours,3,0.81\nbase,1,0.70\nbase,2,0.71\nbase,3,0.72\n"
PLAN = "claim,experiment,kind,evidence,status\nC1,main table,main,E1,done\nC2,loop,main,E2,done\nC3,gate,ablation,E3,done\n"
RESULTS = ("commit\tmetric\tstatus\tseconds\tdescription\n"
           "a1\t0.75\tkeep\t10\tours seed 1\n"
           "a2\t0.70\tdiscard\t10\tours seed 2\n"
           "a3\t0.72\tdiscard\t10\tbaseline seed 1\n"
           "a4\t0.71\tdiscard\t10\tbaseline seed 2\n")
SPEC = "# Experiment loop\nCommand: python train.py\nMetric: accuracy\nDirection: higher\n"


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "results").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, text in (("claims.md", CLAIMS), ("evidence.csv", EVIDENCE), ("experiment_plan.csv", PLAN),
                       ("results.tsv", RESULTS), ("experiment.md", SPEC)):
        (repo / "workflow" / name).write_text(text)
    (repo / "results" / "main.csv").write_text(MAIN)
    return repo


def test_t_distribution_against_closed_forms():
    for t in (0.5, 1.0, 3.0, 12.706):          # df 1 is Cauchy, df 2 has a closed form
        assert sig.t_two_sided(t, 1) == pytest.approx(1 - 2 / math.pi * math.atan(t), abs=1e-10)
        assert sig.t_two_sided(t, 2) == pytest.approx(1 - t / math.sqrt(t * t + 2), abs=1e-10)
    assert sig.t_two_sided(2.228, 10) == pytest.approx(0.05, abs=1e-4)      # table values
    assert sig.t_two_sided(2.262, 9) == pytest.approx(0.05, abs=1e-4)
    assert sig.t_greater(2.262, 9) == pytest.approx(0.025, abs=1e-4)
    assert sig.t_greater(-2.262, 9) == pytest.approx(0.975, abs=1e-4)


def test_paired_welch_bootstrap_and_e_values():
    w = sig.welch([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    assert w["t"] == pytest.approx(-3 / math.sqrt(2.5)) and w["df"] == pytest.approx(6.25 / 1.0625)
    p = sig.paired([0.80, 0.82, 0.81], [0.70, 0.71, 0.72])
    assert p["t"] == pytest.approx(0.1 / (0.01 / math.sqrt(3))) and p["df"] == 2
    assert p["p"] == pytest.approx(1 - p["t"] / math.sqrt(p["t"] ** 2 + 2))
    lo, hi = sig.bootstrap_ci([1.0, 2.0, 3.0], [0.0, 1.0, 2.0], True)
    assert lo == hi == pytest.approx(1.0)                                # every difference is 1
    lo, hi = sig.bootstrap_ci([0.03, -0.01], [0, 0], True)
    assert lo == pytest.approx(-0.01) and hi == pytest.approx(0.03)
    assert sig.bootstrap_ci([1, 2, 3, 4], [0, 1.5, 2, 3.2], True) == sig.bootstrap_ci([1, 2, 3, 4], [0, 1.5, 2, 3.2], True)
    assert sig.e_value(0.01) == pytest.approx(5) and sig.e_value(0.25) == pytest.approx(1)
    assert sig.combine([5, 4]) == 20 and sig.combine([]) == 1


def test_claims_are_tested_and_a_main_claim_crossing_zero_fails(paper):
    out = json.loads(claim_stats(str(paper)))
    assert out["status"] == "FAIL" and "falsification" not in out
    c = out["claims"]
    assert c["C1"]["verdict"] == "pass" and c["C1"]["flags"] == []
    assert c["C2"]["verdict"] == "fail" and "ci_crosses_zero" in c["C2"]["flags"]
    assert c["C3"]["main"] is False and c["C3"]["verdict"] == "notes" and c["C3"]["flags"] == ["single_seed"]
    stats = json.load(open(out["stats"], encoding="utf-8"))
    one = stats["C1"]["tables"][0]["comparisons"][0]
    assert stats["C1"]["tables"][0]["source"] == "results/main.csv"
    assert one["a"] == "ours" and one["b"] == "base" and one["test"] == "paired t"
    assert one["diff"] == pytest.approx(0.1) and one["ci"][0] > 0
    loop = stats["C2"]["tables"][0]
    assert set(loop["groups"]) == {"ours", "baseline"} and loop["comparisons"][0]["test"] == "paired t"
    report = open(out["report"], encoding="utf-8").read()
    assert "论断的统计检验" in report and "置信区间跨过 0" in report and "只有一个种子" in report
    assert not (paper / "workflow" / "falsification.md").exists()


def test_lower_is_better_and_a_single_kept_run(paper):
    (paper / "workflow" / "experiment.md").write_text(SPEC.replace("higher", "lower"))
    (paper / "workflow" / "results.tsv").write_text(
        "commit\tmetric\tstatus\tseconds\tdescription\nb1\t0.4\tkeep\t5\tfirst\nb2\t0.3\tkeep\t5\tsecond\n")
    out = json.loads(claim_stats(str(paper), alpha="0.1"))
    assert out["claims"]["C2"]["flags"] == ["single_seed"] and out["claims"]["C2"]["verdict"] == "fail"
    stats = json.load(open(out["stats"], encoding="utf-8"))
    assert list(stats["C2"]["tables"][0]["groups"]) == ["second"]                # the best kept run
    assert stats["C1"]["tables"][0]["comparisons"][0]["a"] == "base"             # lower accuracy wins now


def test_arguments_are_checked(paper):
    with pytest.raises(ValueError, match="mode"):
        claim_stats(str(paper), mode="bayes")
    with pytest.raises(ValueError, match="alpha"):
        claim_stats(str(paper), alpha="1.5")


def proposals(better="ours", worse="base"):
    return {"tests": [{"run": "换一个数据集", "refutes": "accuracy 不再高于基线", "command": "python train.py --data b",
                       "better": better, "worse": worse},
                      {"run": "调强基线", "refutes": "基线追平", "command": "python train.py --tune-base",
                       "better": better, "worse": worse}]}


def test_popper_proposes_tests_and_combines_e_values(paper):
    with script_roles({cs.falsifier: [proposals(), proposals("ours", "baseline"), proposals("ours", "no gate")]}) as calls:
        out = json.loads(claim_stats(str(paper), mode="popper"))
    request = calls[cs.falsifier][0]
    assert "could refute the claim" in request.text and "C1: Ours beats the baseline" in request.text
    assert len(calls[cs.falsifier]) == 3
    text = (paper / "workflow" / "falsification.md").read_text()
    assert "### falsify:C1:2" in text and "Refuted if: 基线追平" in text
    assert out["claims"]["C1"]["popper"] == "pending" and out["claims"]["C1"]["e"] == 1

    rows = [f"f{i}\t{v}\tdiscard\t5\tfalsify:C1:1 {m} seed {s}"
            for i, (m, s, v) in enumerate([("ours", 1, 0.80), ("ours", 2, 0.81), ("ours", 3, 0.79), ("ours", 4, 0.80),
                                           ("ours", 5, 0.805), ("base", 1, 0.70), ("base", 2, 0.70),
                                           ("base", 3, 0.70), ("base", 4, 0.70), ("base", 5, 0.70)])]
    rows.append("g1\t0.7\tdiscard\t5\tfalsify:C2:1 ours seed 1")
    with open(paper / "workflow" / "results.tsv", "a") as f:
        f.write("\n".join(rows) + "\n")
    edited = text.replace("Command: python train.py --data b", "Command: python train.py --data c")
    (paper / "workflow" / "falsification.md").write_text(edited)
    with script_roles({cs.falsifier: []}):                     # every claim already has its tests
        out = json.loads(claim_stats(str(paper), mode="popper"))
    assert "--data c" in (paper / "workflow" / "falsification.md").read_text()
    assert out["claims"]["C1"]["popper"] == "supported" and out["claims"]["C1"]["e"] >= 20
    stats = json.load(open(out["stats"], encoding="utf-8"))
    first, second = stats["C1"]["popper"]["tests"]
    assert first["status"] == "done" and first["e"] == pytest.approx(1 / (2 * math.sqrt(first["p"])))
    assert second["status"] == "pending"
    assert stats["C2"]["popper"]["tests"][0]["status"] == "incomplete"
    assert out["claims"]["C2"]["popper"] == "pending"
    assert stats["C2"]["tables"][0]["groups"] == {"ours": {"1": 0.75, "2": 0.7}, "baseline": {"1": 0.72, "2": 0.71}}
    report = open(out["report"], encoding="utf-8").read()
    assert "1/(2√p)" in report and "证伪检验后成立" in report


def test_the_falsifier_reply_is_checked():
    with pytest.raises(ValueError, match="with itself"):
        cs.check_tests(proposals("ours", "Ours"), "C1")
    got = cs.check_tests({"tests": [{"run": "r", "refutes": "x", "command": "c", "better": "", "worse": ""}]}, "C1",
                         {"a": "ours", "b": "base"})
    assert got[0]["better"] == "ours" and got[0]["tag"] == "falsify:C1:1"
