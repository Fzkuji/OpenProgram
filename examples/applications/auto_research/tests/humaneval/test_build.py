"""human_eval_build with the guide writer and pilot rater scripted."""

import json
from pathlib import Path

import pytest
from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.humaneval import guide as gd
from auto_research.tasks.humaneval import survey as sv

from .data import make, survey_data

DRAFT = {"mode": "pairwise", "scale": 5,
         "instructions": "Read both summaries. Length alone is no merit. Some pages are attention checks.",
         "criteria": [{"name": "Informativeness", "question": "Which summary tells you more of what happened?",
                       "guidance": "Prefer concrete events. terse or not, judge content.", "claim": "C1"},
                      {"name": "Fluency", "question": "Which summary reads better?", "guidance": "Smooth sentences.", "claim": "C9"}]}


def pilot_reply(pages, criteria, pick):
    """Answer each page with pick(page) and quote the first five words of response 1."""
    out = []
    for p in pages:
        quote = " ".join(p["response_1"].split()[:5])
        for c in criteria:
            out.append({"page": p["id"], "criterion": c, "answer": pick(p), "quote": quote,
                        "ambiguous": c == "fluency", "note": "Reads better is vague." if c == "fluency" else ""})
    return {"answers": out}


def minimal(cid):
    """A pilot that answers one question only (the run reports the rest as missing)."""
    return {"answers": [{"page": "P1", "criterion": cid, "answer": "1", "quote": "", "ambiguous": False, "note": ""}]}


def run(repo, guide_reply=None, pilot=None, **kw):
    script = {sv.pilot_rater: [pilot or minimal("informativeness")]}
    if guide_reply is not None:
        script[sv.guide_writer] = [guide_reply]
    with script_roles(script) as calls:
        out = json.loads(sv.human_eval_build(str(repo), "outputs.jsonl", out_dir=str(repo / "out"), **kw))
    return out, calls


def test_drafted_guide_blind_page_and_key(tmp_path):
    repo = make(tmp_path)
    out, calls = run(repo, DRAFT)
    asked = calls[sv.guide_writer][0].text
    assert "Summaries from terse" in asked and "We compare a terse summarizer" in asked
    assert "system_names_never_to_use" in asked
    assert out["status"] == "PASS_WITH_NOTES" and out["mode"] == "pairwise" and out["tasks"] == 12
    guide = gd.parse((repo / "workflow" / "eval_guide.md").read_text())
    assert not guide["approved"] and [c["claim"] for c in guide["criteria"]] == ["C1", ""]   # C9 is not a claim
    html = Path(out["survey"]).read_text()
    assert "terse" not in html and "verbose" not in html and "[hidden] or not" in html   # blind: guide redacted
    assert "<script src" not in html and "http" not in html.split("<script")[0]
    data = survey_data(html)
    key = json.loads(Path(out["key"]).read_text())
    assert Path(out["key"]) == repo / "workflow" / "human_eval" / f"{out['survey_id']}.json"
    assert set(key["systems"]) == {"terse", "verbose"} and len(key["attention"]) == 3
    assert all("expect" not in a for a in data["attention"])          # the answer is only in the key
    assert data["repeats"] == 2 and len(data["tasks"]) == 12
    study = Path(out["study"]).read_text()
    assert "Completion code: " + data["completion_code"] in study and "PROLIFIC_PID" in study
    report = Path(out["report"]).read_text()
    assert "未确认" in report and "至少需要" in report               # 12 items are too few for power 0.8
    # The guide is kept as the author edits it.
    path = repo / "workflow" / "eval_guide.md"
    path.write_text(path.read_text().replace("Approved: no", "Approved: yes"))
    again, calls = run(repo)
    assert sv.guide_writer not in calls
    assert json.loads(Path(again["key"]).read_text())["guide_approved"] is True


def test_pilot_flags_swapped_inconsistency_and_ambiguity(tmp_path):
    repo = make(tmp_path, items=40)
    first, _ = run(repo, criteria="pairwise: informativeness = Which summary tells you more?; fluency")
    run_json = json.loads(Path(first["run"]).read_text())
    pages = next(s for s in run_json["stages"] if s["stage"] == "pilot")["context"]["pages"]
    assert len(pages) == 2 * sv.PILOT_TASKS
    # Always "1": the swapped copy of each task gets the other system, so every pair disagrees.
    out, _ = run(repo, pilot=pilot_reply(pages, ["informativeness", "fluency"], lambda p: "1"),
                 criteria="pairwise: informativeness = Which summary tells you more?; fluency")
    pilot = json.loads((Path(out["report"]).parent / "pilot.json").read_text())
    info = pilot["criteria"]["informativeness"]
    assert info["inconsistent"] == info["pairs"] == sv.PILOT_TASKS and info["unclear"]
    assert pilot["criteria"]["fluency"]["ambiguous"] == 12 and out["status"] == "PASS_WITH_NOTES"
    assert info["bad_quotes"] == 0
    # Picking by content is consistent; fluency stays ambiguous.
    good = lambda p: "1" if "flooded twice" in p["response_1"] else "2"
    out, _ = run(repo, pilot=pilot_reply(pages, ["informativeness", "fluency"], good),
                 criteria="pairwise: informativeness = Which summary tells you more?; fluency")
    pilot = json.loads((Path(out["report"]).parent / "pilot.json").read_text())
    assert pilot["criteria"]["informativeness"]["inconsistent"] == 0
    assert not pilot["criteria"]["informativeness"]["unclear"] and out["unclear"] == ["fluency"]
    report = Path(out["report"]).read_text()
    assert "Reads better is vague." in report and "建议招募" in report


def test_clear_approved_guide_passes_and_raters_override(tmp_path):
    repo = make(tmp_path, items=40)
    crit = "pairwise: informativeness = Which summary tells you more?"
    first, _ = run(repo, criteria=crit)
    pages = next(s for s in json.loads(Path(first["run"]).read_text())["stages"]
                 if s["stage"] == "pilot")["context"]["pages"]
    reply = pilot_reply(pages, ["informativeness"], lambda p: "1" if "flooded twice" in p["response_1"] else "2")
    for a in reply["answers"]:
        a["ambiguous"], a["note"] = False, ""
    out, _ = run(repo, pilot=reply, criteria=crit, raters="25")
    assert out["status"] == "PASS" and out["raters"] == 25
    assert "Places: 25" in Path(out["study"]).read_text()


def test_rating_mode_when_one_system_and_inputs(tmp_path):
    repo = make(tmp_path)
    lines = [json.loads(x) for x in (repo / "outputs.jsonl").read_text().splitlines()]
    (repo / "single.csv").write_text("item,input,model,response\n" + "".join(
        f"{r['id']},\"{r['prompt']}\",{r['system']},\"{r['output']}\"\n" for r in lines if r["system"] == "terse"))
    with script_roles({sv.pilot_rater: [{"answers": [{"page": "P1", "criterion": "accuracy", "answer": "4",
                                                       "quote": "", "ambiguous": False, "note": ""}]}]}):
        out = json.loads(sv.human_eval_build(str(repo), "single.csv", criteria="accuracy; clarity",
                                             out_dir=str(repo / "out")))
    assert out["mode"] == "rating" and out["tasks"] == 12
    data = survey_data(Path(out["survey"]).read_text())
    assert data["scale"] == 5 and data["tasks"][0]["text"].startswith("Report 1")
    key = json.loads(Path(out["key"]).read_text())
    assert set(key["attention"].values()) <= {"1", "5"}
    with pytest.raises(ValueError, match="outputs is required"):
        sv.human_eval_build(str(repo), "")
    with pytest.raises(ValueError, match="does not exist"):
        sv.human_eval_build(str(repo), "missing.jsonl")
    with pytest.raises(ValueError, match="raters"):
        sv.human_eval_build(str(repo), "outputs.jsonl", raters="one")
    (repo / "bad.csv").write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="no row"):
        sv.human_eval_build(str(repo), "bad.csv")


def test_criteria_parsing():
    g = gd.from_criteria("rating 1-7: accuracy = Is every fact right?; clarity", can_pair=True)
    assert g["mode"] == "rating" and g["scale"] == 7 and g["approved"]
    assert [c["id"] for c in g["criteria"]] == ["accuracy", "clarity"]
    assert g["criteria"][0]["question"] == "Is every fact right?"
    assert gd.from_criteria("helpfulness, harmlessness", can_pair=False)["mode"] == "rating"
    with pytest.raises(ValueError):
        gd.from_criteria("rating 1-12: x", can_pair=True)
    text = gd.render(g)
    assert gd.parse(text)["criteria"][0]["question"] == "Is every fact right?"
