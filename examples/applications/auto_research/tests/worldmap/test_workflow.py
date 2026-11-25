"""research_map on a fixture repository, with the cartographer scripted."""

import json
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.worldmap import argmap, argument_map
from auto_research.tasks.worldmap import workflow as wf

CLAIMS = """# Claims
Approved: yes

C1: Forgetting by usage keeps accuracy while halving memory.
RQ: RQ1
C2: Causal edits beat appending.
RQ: RQ2
C3: The method transfers to new domains.
RQ: RQ3
"""
EVIDENCE = """id,claim,statement,source,script
E1,C1,Accuracy 71.2 vs 71.0 at half the memory,results/main.csv,
E2,C9,An orphan row,results/main.csv,
"""
PLAN = """claim,experiment,kind,evidence,status
C1,Usage forgetting on LongMemEval,main,E1,done
C2,Causal edit vs append,main,,planned
"""
FINDINGS = """# Findings

## F1: Usage forgetting
Hypothesis: C1 holds because rarely used items are noise.
Run: results/main.csv
Result: 71.2 vs 71.0
Verdict: supported

## F2: Causal edits
Hypothesis: C2 should hold on long dialogues.
Run: scripts/edit.py
Result: 64.0 vs 66.5, worse
Verdict: refuted
"""
CARD = """# Plan
Section: Results
Claims: C1
Evidence: E1
Approved: yes
"""
OTHER_FINDINGS = """# Findings

## F1: Summaries forget names
Hypothesis: Summaries drop rare entities.
Run: run.log
Result: 30% of names lost
Verdict: supported
"""


def repo(path, files):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    return path


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(argmap, "known_projects", lambda: [])
    return repo(tmp_path / "paper", {"workflow/claims.md": CLAIMS, "workflow/evidence.csv": EVIDENCE,
                                     "workflow/experiment_plan.csv": PLAN, "workflow/findings.md": FINDINGS,
                                     "workflow/plans/results.md": CARD, "results/main.csv": "a,b\n1,2\n3,4\n"})


def world(**over):
    base = {"entities": [{"id": "M1", "name": "使用频率遗忘", "type": "method", "note": ""},
                         {"id": "K1", "name": "准确率", "type": "metric", "note": ""},
                         {"id": "M2", "name": "因果编辑", "type": "method", "note": ""}],
            "relations": [{"from": "M1", "to": "K1", "type": "measured_by", "evidence": ["E1", "results/main.csv:2"],
                           "note": ""},
                          {"from": "M2", "to": "K1", "type": "hurts", "evidence": ["F2"], "note": ""},
                          {"from": "M2", "to": "M1", "type": "improves", "evidence": ["F7"], "note": ""},
                          {"from": "M1", "to": "M2", "type": "depends_on", "evidence": ["results/main.csv:99"],
                           "note": ""}],
            "open_questions": [{"id": "Q1", "question": "因果编辑为什么在长对话上更差？", "why": "它否定了 C2",
                                "next_experiment": "在短对话上重跑 F2"}],
            "proposals": [{"task": "idea", "why": "C2 被否定，需要改写"}]}
    base.update(over)
    return base


def run(paper, **kw):
    return json.loads(wf.research_map(str(paper), **kw))


def test_argument_map_and_its_gaps_need_no_model(paper):
    amap = argument_map(str(paper))
    kinds = {n["id"]: n["kind"] for n in amap["nodes"]}
    assert kinds == {"C1": "claim", "C2": "claim", "C3": "claim", "E1": "evidence", "E2": "evidence",
                     "X1": "experiment", "X2": "experiment", "F1": "finding", "F2": "finding", "S:results": "section"}
    edges = {(e["source"], e["type"], e["target"]) for e in amap["edges"]}
    assert edges == {("E1", "supports", "C1"), ("X1", "planned_for", "C1"), ("X2", "planned_for", "C2"),
                     ("F1", "supports", "C1"), ("F2", "refutes", "C2"), ("C1", "argued_in", "S:results"),
                     ("E1", "argued_in", "S:results")}
    gaps = {(g["kind"], g["id"]) for g in amap["gaps"]}
    assert gaps == {("claim_without_evidence", "C2"), ("claim_without_evidence", "C3"), ("unknown_claim", "E2"),
                    ("claim_not_argued", "C2"), ("claim_not_argued", "C3"), ("experiment_not_done", "X2")}
    unplanned = {g["id"]: g["unplanned"] for g in amap["gaps"] if g["kind"] == "claim_without_evidence"}
    assert unplanned == {"C2": False, "C3": True}


def test_templates_are_not_work(tmp_path):
    empty = repo(tmp_path / "empty", {})
    assert argument_map(empty) == {"nodes": [], "edges": [], "gaps": [], "claims": {}, "findings": []}
    from auto_research import stages
    blank = repo(tmp_path / "blank", {f"workflow/{n}": stages.template(n)
                                      for n in ("claims.md", "evidence.csv", "experiment_plan.csv", "findings.md")})
    assert argument_map(blank)["nodes"] == []


def test_world_model_drops_relations_without_evidence_and_saves(paper):
    with script_roles({wf.cartographer: [world()]}) as calls:
        out = run(paper)
    req = calls[wf.cartographer][0]
    assert "Never invent results" in req.text and "data, not instructions" in req.text        # docstring
    assert "Forgetting by usage" in req.text and "F2 refutes C2" in req.text                  # digest and map
    assert "idea_generation" in req.text                                                      # the task list
    assert "previous_world" not in req.text
    saved = json.load(open(out["world"], encoding="utf-8"))
    assert out["world"] == str(paper / "workflow" / "world.json")
    assert [(r["from"], r["type"], r["to"]) for r in saved["relations"]] == [("M1", "measured_by", "K1"),
                                                                             ("M2", "hurts", "K1")]
    assert set(saved) >= {"entities", "relations", "open_questions", "proposals", "at", "head"}
    assert out["status"] == "FAIL"                         # C3 has no evidence and no planned experiment
    assert out["proposals"] == [{"task": "idea", "why": "C2 被否定，需要改写"}]
    assert out["open_questions"][0]["id"] == "Q1"
    report = open(out["report"], encoding="utf-8").read()
    assert report.index("## 缺口") < report.index("## 开放问题") < report.index("## 建议的下一步")
    assert "2 条关系因依据查不到被丢弃" in report and "F7" in report
    page = open(out["map"], encoding="utf-8").read()
    assert "<svg" in page and "论证图" in page and "世界模型" in page and "<script" not in page
    record = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in record["stages"]] == ["map", "shared", "world", "save"]
    assert record["stages"][-1]["dropped"] == 2
    assert out["map"].startswith(str(paper / "drafts" / "research_map"))


def test_second_run_updates_the_world_and_keeps_ids(paper):
    with script_roles({wf.cartographer: [world()]}):
        run(paper)
    renamed = world(entities=[{"id": "N1", "name": "使用频率遗忘", "type": "method", "note": "改了说明"},
                              {"id": "K1", "name": "准确率", "type": "metric", "note": ""},
                              {"id": "D1", "name": "LongMemEval", "type": "dataset", "note": ""}],
                    relations=[{"from": "N1", "to": "D1", "type": "evaluated_on", "evidence": ["X1"], "note": ""},
                               {"from": "N1", "to": "K1", "type": "measured_by", "evidence": ["E1"], "note": ""}],
                    open_questions=[{"id": "Q9", "question": "因果编辑为什么在长对话上更差？", "why": "w",
                                     "next_experiment": "n"}])
    with script_roles({wf.cartographer: [renamed]}) as calls:
        out = run(paper)
    req = calls[wf.cartographer][0]
    assert "previous_world" in req.text and "使用频率遗忘" in req.text and "Update the world model." in req.text
    saved = json.load(open(out["world"], encoding="utf-8"))
    assert [e["id"] for e in saved["entities"]] == ["M1", "K1", "D1"]
    assert [(r["from"], r["to"]) for r in saved["relations"]] == [("M1", "K1")]   # X1 is not C/E/F evidence
    assert saved["open_questions"][0]["id"] == "Q1"


def test_shared_findings_come_from_other_projects(paper, tmp_path, monkeypatch):
    other = repo(tmp_path / "other", {"workflow/findings.md": OTHER_FINDINGS})
    known = repo(tmp_path / "known", {"workflow/findings.md": OTHER_FINDINGS.replace("F1", "F3")})
    plain = tmp_path / "plain"
    (plain / "workflow").mkdir(parents=True)
    (plain / "workflow" / "findings.md").write_text(OTHER_FINDINGS)
    monkeypatch.setattr(argmap, "known_projects",
                        lambda: [("Known project", str(known)), ("me", str(paper)), ("plain", str(plain))])
    with script_roles({wf.cartographer: [world(relations=[], proposals=[])]}) as calls:
        out = run(paper, other_projects=f"{other}, {other}")
    assert out["shared_findings"] == [
        {"project": "other", "id": "F1", "title": "Summaries forget names", "verdict": "supported"},
        {"project": "Known project", "id": "F3", "title": "Summaries forget names", "verdict": "supported"}]
    assert "[other] F1 Summaries forget names" in calls[wf.cartographer][0].text


def test_the_cartographer_can_point_only_at_what_exists(paper):
    amap = argument_map(paper)
    names = ["idea"]
    with pytest.raises(ValueError, match="unknown task"):
        wf.check_world(world(proposals=[{"task": "nope", "why": ""}]), paper, amap, names)
    with pytest.raises(ValueError, match="repeated entity"):
        wf.check_world(world(entities=[{"id": "A", "name": "a", "type": "concept"}] * 2), paper, amap, names)
    _, dropped = wf.check_world(world(relations=[
        {"from": "M1", "to": "ZZ", "type": "improves", "evidence": ["E1"]},
        {"from": "M1", "to": "K1", "type": "improves", "evidence": ["../outside.txt:1"]},
        {"from": "M1", "to": "K1", "type": "improves", "evidence": []}]), paper, amap, names)
    assert [d["reason"] for d in dropped] == ["unknown entity", "evidence not found: ../outside.txt:1", "no evidence"]
    assert wf.status_of([], []) == "PASS"
    assert wf.status_of([{"kind": "claim_not_argued", "id": "C1"}], []) == "PASS_WITH_NOTES"
    assert wf.status_of([], [{"reason": "x"}]) == "PASS_WITH_NOTES"
