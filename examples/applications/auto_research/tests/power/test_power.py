"""power_analysis on fixture projects, and the power computation against G*Power's
published sample sizes. No model call is made."""

import json
import subprocess

import pytest

from auto_research.tasks.power import power_analysis
from auto_research.tasks.power import stats
from auto_research.tasks.power import workflow as wf

CLAIMS = """# Claims
Approved: yes

C1: The tuned schedule lowers validation loss from 0.0137 to 0.0102.
RQ: RQ1
"""
EVIDENCE = "id,claim,statement,source,script\nE1,C1,Loss,workflow/results.tsv,train.py\n"
SPEC = "# Experiment loop\nApproved: yes\nCommand: python train.py\nMetric: val_loss\nDirection: lower\n"
HEAD = "commit\tmetric\tstatus\tseconds\tdescription\n"
UNSEEDED = ("a1\t0.0137\tkeep\t3\tbaseline lr 0.01\n"
            "a2\t0.0102\tkeep\t3\ttuned lr 0.05\n"
            "a3\t0.0183\tdiscard\t3\tlr 0.5\n")
PILOT = ("b1\t0.01443\tdiscard\t3\tbaseline lr 0.01 seed 1\n"
         "b2\t0.01482\tdiscard\t3\tbaseline lr 0.01 seed 2\n"
         "b3\t0.01229\tdiscard\t3\tbaseline lr 0.01 seed 3\n"
         "c1\t0.01043\tdiscard\t3\ttuned lr 0.05 seed 1\n"
         "c2\t0.00970\tdiscard\t3\ttuned lr 0.05 seed 2\n"
         "c3\t0.00976\tdiscard\t3\ttuned lr 0.05 seed 3\n")


def make(tmp_path, results, claims=CLAIMS, evidence=EVIDENCE, spec=SPEC):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, text in (("claims.md", claims), ("evidence.csv", evidence), ("results.tsv", HEAD + results),
                       ("experiment.md", spec)):
        (repo / "workflow" / name).write_text(text)
    return repo


def run(repo, **kw):
    return json.loads(power_analysis(str(repo), out_dir=str(repo / "out"), **kw))


def test_sample_sizes_match_gpower():
    # G*Power 3.1, two-tailed, alpha 0.05, power 0.8: paired dz 0.5 / 0.8 / 1.0 / 2.0 and two groups d 0.5 / 0.8
    assert [stats.seeds_needed(d, True, sd_diff=1) for d in (0.5, 0.8, 1.0, 2.0)] == [34, 15, 10, 5]
    assert [stats.seeds_needed(d, False, sd_a=1, sd_b=1) for d in (0.5, 0.8)] == [64, 26]
    assert stats.seeds_needed(0.5, True, sd_diff=1, target=0.9) == 44
    assert stats.seeds_needed(0.5, True, sd_diff=1, alpha=0.01) == 51
    assert stats.t_critical(0.05, 10) == pytest.approx(2.228139, abs=1e-5)
    assert stats.power(2.5, 10, 0.05) == pytest.approx(0.6165, abs=2e-3)      # scipy nct
    assert stats.power(0.0, 10, 0.05) == pytest.approx(0.05, abs=1e-4)        # the null rejects at alpha
    assert stats.seeds_needed(1e-6, True, sd_diff=1) is None                  # infeasible
    assert stats.seeds_needed(0.5, True, sd_diff=0) == 2                      # no noise


def test_claim_numbers():
    assert wf.claimed_numbers("lowers loss from 0.0137 to 0.0102") == (0.0137, 0.0102)
    assert wf.claimed_numbers("accuracy 71.2% -> 74.5%") == (71.2, 74.5)
    assert wf.claimed_numbers("ours 0.9 vs 0.8 for the baseline") == (0.8, 0.9)
    assert wf.claimed_numbers("ours is better") is None
    assert wf.parse_effect("5%") == {"value": 0.05, "relative": True, "text": "5%"}
    with pytest.raises(ValueError):
        wf.parse_effect("much")


def test_single_runs_ask_for_pilot_seeds(tmp_path):
    out = run(make(tmp_path, UNSEEDED + "s1\t0.0091\tkeep\t3\tseed 1 (uncommitted)\n"))
    assert out["status"] == "PASS_WITH_NOTES"
    [c] = out["comparisons"]                      # the claim's numbers pick the pair out of four groups
    assert (c["a"], c["b"], c["state"], c["needed"]) == ("tuned lr 0.05", "baseline lr 0.01", "pilot", None)
    assert "先试跑" in open(out["report"]).read()
    assert json.load(open(out["edits"]))["edits"] == []


def test_pilot_gives_seeds_and_proposes_the_line(tmp_path):
    repo = make(tmp_path, UNSEEDED + PILOT)
    out = run(repo)
    [c] = out["comparisons"]
    assert (c["have"], c["needed"], c["state"]) == (3, 4, "underpowered")
    record = json.load(open(out["power"]))
    plan = record["claims"]["C1"]["comparisons"][0]
    assert plan["paired"] and plan["effect_from"] == "claim" and plan["effect"] == pytest.approx(0.0035)
    [edit] = json.load(open(out["edits"]))["edits"]
    assert edit["quote"] == "Direction: lower" and edit["replacement"] == "Direction: lower\nSeeds: 4"
    assert edit["file"] == "workflow/experiment.md"
    assert "Seeds" not in (repo / "workflow" / "experiment.md").read_text()      # proposed, not applied
    # a relative effect needs far more seeds; an existing larger Seeds line needs no edit
    assert run(repo, effect="5%")["seeds"] > 20
    (repo / "workflow" / "experiment.md").write_text(SPEC + "Seeds: 5\n")
    out = run(repo)
    assert out["status"] == "PASS_WITH_NOTES" and json.load(open(out["edits"]))["edits"] == []
    (repo / "workflow" / "experiment.md").write_text(SPEC + "Seeds: 2\n")
    [edit] = json.load(open(run(repo)["edits"]))["edits"]
    assert (edit["quote"], edit["replacement"]) == ("Seeds: 2", "Seeds: 4")


def test_enough_seeds_pass(tmp_path):
    more = PILOT + ("b4\t0.01380\tdiscard\t3\tbaseline lr 0.01 seed 4\n"
                    "c4\t0.00990\tdiscard\t3\ttuned lr 0.05 seed 4\n"
                    "b5\t0.01400\tdiscard\t3\tbaseline lr 0.01 seed 5\n"
                    "c5\t0.01000\tdiscard\t3\ttuned lr 0.05 seed 5\n")
    out = run(make(tmp_path, more))
    assert out["status"] == "PASS"
    assert out["comparisons"][0]["state"] == "enough"


def test_evidence_compare_and_unpaired_groups(tmp_path):
    evidence = ("id,claim,statement,source,script,method,value,seed,compare\n"
                "E1,C1,a,workflow/claims.md,,ours,0.90,1,ours > base\n"
                "E2,C1,a,workflow/claims.md,,ours,0.92,2,\n"
                "E3,C1,a,workflow/claims.md,,base,0.80,3,\n"
                "E4,C1,a,workflow/claims.md,,base,0.83,4,\n"
                "E5,C1,a,workflow/claims.md,,other,0.99,5,\n")
    claims = "# Claims\nApproved: yes\n\nC1: Ours beats the baseline.\nRQ: RQ1\n"
    spec = SPEC.replace("lower", "higher")
    out = run(make(tmp_path, "", claims=claims, evidence=evidence, spec=spec))
    [c] = out["comparisons"]
    assert (c["a"], c["b"]) == ("ours", "base")
    record = json.load(open(out["power"]))
    plan = record["claims"]["C1"]["comparisons"][0]
    assert not plan["paired"] and plan["effect_from"] == "pilot" and c["needed"] >= 2


def test_no_comparison_fails(tmp_path):
    assert run(make(tmp_path, "a1\t0.5\tkeep\t3\tonly one\n"))["status"] == "FAIL"
    assert run(make(tmp_path / "x", "", claims="# Claims\n"))["status"] == "FAIL"


def test_inputs_are_checked(tmp_path):
    repo = make(tmp_path, PILOT)
    for kw in ({"alpha": "1.5"}, {"power": "x"}, {"effect": "0"}):
        with pytest.raises(ValueError):
            power_analysis(str(repo), **kw)
    with pytest.raises(ValueError):
        power_analysis(str(tmp_path / "missing"))
