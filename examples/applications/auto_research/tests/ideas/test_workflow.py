"""The idea_generation entry with the search replaced and both roles scripted."""

import json
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.ideas import workflow as wf
from auto_research.tasks.survey import search

SURVEY = {"direction": "LLM agent memory", "directions": {"cards": [
    {"title": "记忆的遗忘", "problem": "现有记忆只增不删 [p1][p2]", "evidence": ["p1.L1", "p2.L1"], "active": [],
     "cost": "单卡", "first_step": "在 LongMemEval 上加遗忘策略"}]}}
LITERATURE = "# Literature\n\n- key: contribution; difference from our work\n- wang2024: workflow memory; ours edits it\n"
PRIOR = search.candidate("Forgetting in agent memory", "We prune agent memory by usage.", "2025-03-01",
                         ["A B"], "ICLR", arxiv="2503.00001")
OFFTOPIC = search.candidate("Cooking with robots", "Kitchen robotics.", "2024-01-01", ["C D"], "", arxiv="2401.00002")


def idea(title, gaps, queries):
    return {"title": title, "hypothesis": "假设", "approach": "方法", "outcome": "结果", "effort": "medium",
            "risk": "风险", "gaps": gaps, "queries": queries}


IDEAS = {"ideas": [idea("按使用频率遗忘", ["G1"], ["agent memory forgetting"]),
                   idea("记忆的因果编辑", ["G1", "L1"], ["causal memory editing agents"])]}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    run_dir = repo / "workflow" / "surveys" / "llm-agent-memory" / "20261101-120000"
    run_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (run_dir / "survey.json").write_text(json.dumps(SURVEY, ensure_ascii=False))
    (repo / "workflow" / "literature.md").write_text(LITERATURE)

    def fake(q, n, since):
        return [dict(PRIOR), dict(OFFTOPIC)] if "forgetting" in q else []
    monkeypatch.setattr(search, "SEARCHES", {"arxiv": fake})
    return repo


def run(paper, **kw):
    return json.loads(wf.idea_generation(str(paper), **kw))


def test_ideas_come_from_the_gaps_and_are_ranked_against_the_records(paper):
    verdicts = {"assessments": [
        {"id": "I1", "novelty": "incremental", "closest": ["R1"], "differentiation": "只换了遗忘的依据",
         "feasibility": 5, "impact": 3, "note": ""},
        {"id": "I2", "novelty": "novel", "closest": [], "differentiation": "", "feasibility": 3, "impact": 4,
         "note": ""}]}
    with script_roles({wf.ideator: [IDEAS], wf.assessor: [verdicts]}) as calls:
        out = run(paper)
    gen = calls[wf.ideator][0]
    assert "Range from incremental to ambitious" in gen.text                     # docstring
    assert "现有记忆只增不删" in gen.text and "wang2024" in gen.text             # both kinds of gap
    assert "key: contribution" not in gen.text                                   # the template line is not a gap
    judged = calls[wf.assessor][0]
    assert "Forgetting in agent memory" in judged.text and "a bounded search does not prove" in judged.text
    record = json.load(open(out["ideas"], encoding="utf-8"))
    # I2 found no record, so it is unverified whatever the assessor said; unverified still ranks above incremental
    assert [(i["id"], i["novelty"]) for i in record["ideas"]] == [("I2", "unverified"), ("I1", "incremental")]
    assert record["ideas"][1]["closest"][0]["title"] == "Forgetting in agent memory"
    assert out["status"] == "PASS_WITH_NOTES"
    assert "最接近的工作：Forgetting in agent memory" in open(out["report"], encoding="utf-8").read()
    run_record = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run_record["stages"]] == ["gather", "generate", "search", "assess", "rank"]
    assert set(run_record["roles"]) == {"ideator", "assessor"}
    assert out["report"].startswith(str(paper / "drafts" / "idea_generation"))
    assert (paper / "workflow" / "literature.md").read_text() == LITERATURE


def test_direction_picks_one_survey_and_no_gaps_is_rejected(paper, tmp_path):
    gaps, topics = wf.gather(paper, "LLM agent memory")
    assert topics == ["LLM agent memory"] and [g["id"] for g in gaps] == ["G1", "L1"]
    gaps, _ = wf.gather(paper, "something else")
    assert [g["id"] for g in gaps] == ["L1"]
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(ValueError, match="no gaps"):
        run(empty)
    with pytest.raises(ValueError, match="count"):
        run(paper, count="9")


def test_the_roles_can_point_only_at_what_they_were_given():
    with pytest.raises(ValueError, match="no known gap"):
        wf.check_ideas({"ideas": [idea("x", ["G9"], ["q q q"])]}, ["G1"])
    ideas = [{"id": "I1"}]
    with pytest.raises(ValueError, match="not given"):
        wf.check_assessments({"assessments": [{"id": "I1", "novelty": "known", "closest": ["R3"],
                                               "feasibility": 1, "impact": 1}]}, ideas, {"I1": [PRIOR]})
    with pytest.raises(ValueError, match="without naming"):
        wf.check_assessments({"assessments": [{"id": "I1", "novelty": "known", "closest": [],
                                               "feasibility": 1, "impact": 1}]}, ideas, {"I1": [PRIOR]})
    with pytest.raises(ValueError, match="unassessed"):
        wf.check_assessments({"assessments": []}, ideas, {"I1": []})
