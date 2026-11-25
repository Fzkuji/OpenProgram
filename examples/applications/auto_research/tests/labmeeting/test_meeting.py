"""lab_meeting with the planner, members, critic and PI assistant scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.labmeeting import meeting as wf
from auto_research.tasks.labmeeting import sources as src

AGENDA = "Is the batch-size result robust enough to claim, and what should we run next?"
TEX = r"""\documentclass{article}
\begin{document}
We use a batch size of 32 and reach a validation loss of 0.0102.
\end{document}
"""
RESULTS = ("commit\tmetric\tstatus\tseconds\tdescription\n"
           "aaa1111\t0.013727\tkeep\t3\tbaseline batch 64\n"
           "bbb2222\t0.010215\tkeep\t3\tbatch 32\n")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    wfd = repo / "workflow"
    wfd.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (wfd / "claims.md").write_text("# Claims\nApproved: yes\n\nC1: Batch 32 lowers the loss.\nRQ: RQ1\n")
    (wfd / "evidence.csv").write_text("id,claim,statement,source,script\nE1,C1,Loss 0.0102 at batch 32,"
                                      "workflow/results.tsv,train.py\n")
    (wfd / "results.tsv").write_text(RESULTS)
    (wfd / "experiment.md").write_text("# Experiment loop\nEdit: train.py, config.json, ../secret\n"
                                       "Command: python train.py\nMetric: val_loss\nDirection: lower\n")
    (repo / "config.json").write_text('{"batch_size": 64}\n')
    (wfd / "experiment_plan.csv").write_text("claim,experiment,kind,evidence,status\n"
                                             "C1,batch 32 vs 64 one seed,main,E1,done\n")
    return repo


TEAM = {"members": [
    {"role": "Optimization Researcher", "expertise": "SGD schedules.", "goal": "Judge the batch effect."},
    {"role": "Statistician", "expertise": "Seed variance.", "goal": "Decide how many seeds."},
    {"role": "ML Engineer", "expertise": "Training code.", "goal": "Check the runs are reproducible."}]}


def speech(*statements):
    return {"statements": list(statements)}


def fact(text, *cites, quote=""):
    return {"kind": "fact", "text": text, "cites": list(cites), "quote": quote}


def opinion(text):
    return {"kind": "opinion", "text": text, "cites": [], "quote": ""}


def proposal(text):
    return {"kind": "proposal", "text": text, "cites": [], "quote": ""}


MEMBERS = [
    speech(fact("Batch 32 reached 0.010215 against 0.013727.", "workflow/results.tsv:3", "aaa1111"),
           fact("The paper reports 0.0102.", "main.tex:3", quote="validation loss of 0.0102")),
    speech(fact("Only one seed per setting was run.", "X1"),
           opinion("A 26% gain from one seed is not robust.")),                     # 26 is not in any cited line
    speech(fact("The plan row is done with E1.", "workflow/experiment_plan.csv:9"),   # line out of range
           fact("Loss was 0.5 at batch 128.")),                                      # no citation, number made up
    speech(proposal("Run 5 seeds for batch 16, 32 and 64.")),
    speech(fact("E1 says loss 0.0102.", "E1", quote="Loss 0.0102 at batch 32")),
    speech(fact("Claim C1 is approved.", "workflow/claims.md:2", quote="not what the line says")),
]
CRITIC = [speech(fact("The baseline used batch 64.", "workflow/results.tsv:2"),
                 proposal("Report mean and std over 5 seeds before claiming.")),
          speech(opinion("The claim should wait for the seed study."))]


def summary(**over):
    data = {"answer": "Not yet: the batch result rests on one seed per setting.",
            "decisions": [{"text": "The current result is a single seed.", "cites": ["X1"]}],
            "disagreements": [{"topic": "Whether one seed is enough for a workshop paper",
                               "positions": [{"member": "Statistician", "position": "No."},
                                             {"member": "ML Engineer", "position": "Maybe, with a caveat."}]}],
            "actions": [
                {"action": "Run batch 16/32/64 with 5 seeds.", "owner": "Statistician", "task": "",
                 "plan": {"claim": "C1", "experiment": "batch 16, 32, 64 x 5 seeds, mean and std", "kind": "robustness"}},
                {"action": "Test the difference across seeds.", "owner": "Statistician", "task": "claim_stats",
                 "plan": {"claim": "", "experiment": "", "kind": ""}},
                {"action": "Repeat the existing run.", "owner": "PI", "task": "",
                 "plan": {"claim": "C1", "experiment": "batch 32 vs 64 one seed", "kind": "main"}},  # already planned
                {"action": "Sweep the batch size.", "owner": "ML Engineer", "task": "",
                 "plan": {"claim": "", "experiment": "", "kind": "main"}}]}                         # no experiment
    data.update(over)
    return data


def run(paper, members_replies=None, critic_replies=None, summ=None, **kw):
    script = {wf.planner: [TEAM], wf.member: members_replies or list(MEMBERS), wf.critic: critic_replies or list(CRITIC),
              wf.secretary: [summ or summary()]}
    with script_roles(script) as calls:
        out = json.loads(wf.lab_meeting(str(paper), AGENDA, **kw))
    return out, calls


def test_meeting_checks_citations_and_writes_the_transcript(paper):
    out, calls = run(paper)
    assert out["status"] == "PASS_WITH_NOTES"
    assert out["members"] == ["Optimization Researcher", "Statistician", "ML Engineer", "Scientific Critic"]
    assert len(calls[wf.member]) == 6 and len(calls[wf.critic]) == 2
    first = calls[wf.member][0].text
    assert "every fact you state comes with the file and line" in first
    assert "3| bbb2222\t0.010215" in first and AGENDA in first and "Optimization Researcher" in first
    assert "=== config.json\n1| {\"batch_size\": 64}" in first and "secret" not in first.split("=== config.json")[1]
    # the critic of round 1 hears the three members of round 1
    assert "Only one seed per setting was run." in calls[wf.critic][0].text
    record = json.loads((Path(out["report"]).parent / "meeting.json").read_text())
    said = [s for t in record["turns"] for s in t["statements"]]
    assert said[0]["flags"] == [] and said[0]["cites"] == ["workflow/results.tsv:3", "aaa1111"]
    assert said[1]["quote"] == "validation loss of 0.0102" and not said[1]["flags"]
    assert said[3]["text"] == f"A {src.REMOVED}% gain from one seed is not robust."
    assert any("points to no line" in f for f in said[4]["flags"])
    assert "a factual statement without a valid citation" in said[5]["flags"]
    assert "0.5" not in said[5]["text"]
    assert said[8]["text"] == "Run 5 seeds for batch 16, 32 and 64." and not said[8]["flags"]  # proposals keep numbers
    assert said[9]["quote"] == "Loss 0.0102 at batch 32"
    assert "the quote is not in the cited lines" in said[10]["flags"] and said[10]["quote"] == ""
    s = record["summary"]
    assert [a["task"] for a in s["actions"]] == ["", "claim_stats", "", ""]
    assert out["plan_rows"] == [{"claim": "C1", "experiment": "batch 16, 32, 64 x 5 seeds, mean and std",
                                 "kind": "robustness", "evidence": "", "status": "planned"}]
    assert any("already in experiment_plan.csv" in n for n in s["notes"])
    assert any("proposed a plan row without an experiment" in n for n in s["notes"])
    # the plan file is never written
    assert (paper / "workflow" / "experiment_plan.csv").read_text().count("\n") == 2
    meeting = Path(out["meeting"])
    assert meeting.parent == paper / "workflow" / "meetings" and meeting.name.endswith("-is-the-batch-size-result-robust.md")
    text = meeting.read_text()
    assert "## Summary" in text and "### Round 2" in text and "#### Scientific Critic" in text
    assert "C1,\"batch 16, 32, 64 x 5 seeds, mean and std\",robustness,,planned" in text
    report = (Path(out["report"])).read_text()
    assert "# 组会纪要" in report and "显著性检验（`claim_stats`）" in report and "发言核查" in report


def test_next_meeting_reads_the_last_summary_and_named_roles(paper):
    first, _ = run(paper)
    team = {"members": [{"role": "Statistician", "expertise": "Seeds.", "goal": "Settle the seeds."}]}
    script = {wf.planner: [team], wf.member: [speech(opinion("Agreed."))], wf.critic: [speech(opinion("Fine."))],
              wf.secretary: [summary(decisions=[], disagreements=[], actions=[
                  {"action": "Run the seeds.", "owner": "Statistician", "task": "claim_stats",
                   "plan": {"claim": "", "experiment": "", "kind": ""}}])]}
    with script_roles(script) as calls:
        out = json.loads(wf.lab_meeting(str(paper), AGENDA, rounds="1", members="Statistician, scientific critic"))
    assert out["members"] == ["Statistician", "Scientific Critic"]
    assert "Settle the seeds." in calls[wf.member][0].text
    text = calls[wf.member][0].text
    assert "Not yet: the batch result rests on one seed per setting." in text     # previous summary
    assert Path(first["meeting"]).name != Path(out["meeting"]).name
    assert Path(out["meeting"]).name.endswith("-robust-2.md")


def test_inputs_and_malformed_replies(paper, tmp_path):
    with pytest.raises(ValueError, match="agenda is empty"):
        wf.lab_meeting(str(paper), "  ")
    with pytest.raises(ValueError, match="rounds"):
        wf.lab_meeting(str(paper), AGENDA, rounds="9")
    with pytest.raises(ValueError, match="at most 5"):
        wf.lab_meeting(str(paper), AGENDA, members="a,b,c,d,e,f")
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.lab_meeting(str(tmp_path), AGENDA)
    with pytest.raises(ValueError, match="chose 2 members"):
        wf.check_team({"members": TEAM["members"][:2]}, [], 3)
    critic = {"role": "Scientific Critic", "expertise": "x", "goal": "y"}
    with pytest.raises(ValueError, match="added a critic"):
        wf.check_team({"members": TEAM["members"][:2] + [critic]}, [], 3)
    with pytest.raises(ValueError, match="changed the requested roles"):
        wf.check_team({"members": TEAM["members"][:1]}, ["Statistician"], 1)
    with pytest.raises(ValueError, match="not a list of statements"):
        wf.check_speech({"statements": []})


def test_ungrounded_meeting_fails(paper):
    nothing = [speech(fact("Loss was 0.5.")) for _ in range(3)]
    out, _ = run(paper, members_replies=nothing, critic_replies=[speech(opinion("Weak."))], rounds="1")
    assert out["status"] == "FAIL"


def test_citations_resolve_by_path_range_and_row_id():
    sources = {"workflow/results.tsv": RESULTS.splitlines(), "main.tex": TEX.splitlines(),
               "workflow/experiment_plan.csv": ["claim,experiment,kind,evidence,status", "C1,x,main,,planned"]}
    ids = src.row_ids(sources)
    assert ids["bbb2222"] == ("workflow/results.tsv", 3) and ids["X1"] == ("workflow/experiment_plan.csv", 2)
    assert src.resolve("results.tsv:2-3", sources, ids) == ("workflow/results.tsv", 2, 3)
    assert src.resolve("main.tex:99", sources, ids) is None and src.resolve("nope.md:1", sources, ids) is None
    s = src.check_statement(fact("ResNet-152 reached 0.0137 at step 7.", "results.tsv:2"), sources, ids, "")
    assert s["text"] == f"ResNet-152 reached 0.0137 at step {src.REMOVED}."     # 0.013727 rounds to 0.0137
