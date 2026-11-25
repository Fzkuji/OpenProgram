"""human_eval_analyze on answers written the way the survey page writes them."""

import csv
import io
import json
from pathlib import Path

import pytest
from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.humaneval import analyze as an
from auto_research.tasks.humaneval import survey as sv
from auto_research.tasks.proofread.workflow import apply_edit

from .data import answer, coin, make, survey_data, truth

CRIT = "pairwise: informativeness = Which summary tells you more?"
PILOT = {"answers": [{"page": "P1", "criterion": "informativeness", "answer": "1", "quote": "", "ambiguous": False,
                      "note": ""}]}


def build(repo, criteria=CRIT):
    with script_roles({sv.pilot_rater: [PILOT]}):
        out = json.loads(sv.human_eval_build(str(repo), "outputs.jsonl", criteria=criteria, out_dir=str(repo / "out")))
    data = survey_data(Path(out["survey"]).read_text())
    key = json.loads(Path(out["key"]).read_text())
    return data, key


def write(folder: Path, name: str, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)


def analyze(repo, responses="answers"):
    return json.loads(an.human_eval_analyze(str(repo), responses, out_dir=str(repo / "analysis")))


def test_agreeing_raters_pass_and_careless_one_is_dropped(tmp_path):
    repo = make(tmp_path, items=30)
    data, key = build(repo)
    folder = repo / "answers"
    for i in range(4):
        write(folder, f"r{i}.csv", answer(data, key, f"r{i}", truth(key, 0.95), seed=i))
    write(folder, "careless.csv", answer(data, key, "careless", coin, attentive=False))
    out = analyze(repo)
    assert out["raters"] == 5 and out["kept"] == 4 and out["status"] == "PASS"
    record = json.loads((Path(out["report"]).parent / "analysis.json").read_text())
    assert not record["raters"]["careless"]["kept"] and record["raters"]["careless"]["failed"] == 3
    assert record["raters"]["r0"]["repeats"] == 3 and record["raters"]["r0"]["consistency"] >= 0.6
    assert record["raters"]["r0"]["with_majority"] > 0.85 and "with_majority" not in record["raters"]["careless"]
    res = record["results"]["informativeness"]
    assert res["alpha_ordinal"] >= 0.667 and res["fleiss_kappa"] > 0.6
    t = res["systems"]["terse"]
    assert 0.6 < t["bt_win_rate"] < 0.9 and t["ci"][0] > 0.5 and t["wins"] > t["losses"]
    claim = record["claims"]["C1"]
    assert claim["systems"] == ["terse", "verbose"] and claim["has_ci"] and "C2" not in record["claims"]
    table = list(csv.DictReader(io.StringIO((repo / "workflow" / "human_eval.csv").read_text())))
    rows = {r["system"]: r for r in table}
    assert set(rows) == {"terse", "verbose", "terse>verbose", "verbose>terse"}
    assert rows["terse"]["claims"] == "C1" and float(rows["terse>verbose"]["ci_low"]) > 0.5
    edits = json.loads(Path(out["edits"]).read_text())["edits"]
    assert len(edits) == 1 and edits[0]["file"] == "workflow/evidence.csv"
    assert 'E2,C1,"Human raters preferred terse over verbose' in edits[0]["replacement"]
    assert apply_edit(str(repo), edits[0])
    evidence = list(csv.DictReader(io.StringIO((repo / "workflow" / "evidence.csv").read_text())))
    assert [e["id"] for e in evidence] == ["E1", "E2"] and evidence[1]["source"] == "workflow/human_eval.csv"
    report = Path(out["report"]).read_text()
    assert "剔除 1 位" in report and "区间不含 0.5" in report
    # The same evidence is not proposed twice.
    again = analyze(repo)
    assert json.loads(Path(again["edits"]).read_text())["edits"] == []


def test_random_raters_fail(tmp_path):
    repo = make(tmp_path, items=30, evidence=None)
    data, key = build(repo)
    folder = repo / "answers"
    for i in range(3):
        write(folder, f"r{i}.csv", answer(data, key, f"r{i}", coin, seed=i))
    out = analyze(repo)
    assert out["status"] == "FAIL" and out["alpha"]["informativeness"] < 0.667
    assert json.loads(Path(out["edits"]).read_text())["edits"] == []       # unreliable labels are no evidence
    assert "不作为证据" in Path(out["report"]).read_text()


def test_rating_mode_and_claim_without_ci(tmp_path):
    claims = ("# Claims\nApproved: yes\n\nC1: Raters score terse above verbose.\nRQ: RQ1\n"
              "C2: The ghost system is best.\nRQ: RQ1\n")
    repo = make(tmp_path, items=20, claims=claims, evidence=None)
    with script_roles({sv.pilot_rater: [{"answers": [{"page": "P1", "criterion": "clarity", "answer": "4", "quote": "",
                                                      "ambiguous": False, "note": ""}]}]}):
        out = json.loads(sv.human_eval_build(str(repo), "outputs.jsonl", criteria="rating: clarity",
                                             out_dir=str(repo / "out")))
    data = survey_data(Path(out["survey"]).read_text())
    key = json.loads(Path(out["key"]).read_text())
    terse = key["systems"]["terse"]

    def score(task, criterion, shown, rng):
        return "5" if shown[0] == terse else rng.choice(["1", "2"])
    for i in range(3):
        write(repo / "answers", f"r{i}.csv", answer(data, key, f"r{i}", score, seed=i))
    res = analyze(repo)
    record = json.loads((Path(res["report"]).parent / "analysis.json").read_text())
    systems = record["results"]["clarity"]["systems"]
    assert systems["terse"]["mean_rating"] == 5 and systems["verbose"]["mean_rating"] < 2
    assert systems["terse"]["bt_win_rate"] > 0.9 and record["raters"]["r0"]["within_one"] == 1.0
    assert "C2" not in record["claims"] and res["status"] == "PASS"
    edits = json.loads(Path(res["edits"]).read_text())["edits"]
    assert edits[0]["kind"] == "create" and edits[0]["replacement"].startswith("id,claim,statement,source,script\nE1,C1,")
    assert apply_edit(str(repo), edits[0]) and (repo / "workflow" / "evidence.csv").is_file()


def test_inputs(tmp_path):
    repo = make(tmp_path, items=6)
    data, key = build(repo)
    with pytest.raises(ValueError, match="responses is required"):
        an.human_eval_analyze(str(repo), "")
    with pytest.raises(ValueError, match="does not exist"):
        an.human_eval_analyze(str(repo), "nowhere")
    (repo / "empty").mkdir()
    with pytest.raises(ValueError, match="no .csv"):
        an.human_eval_analyze(str(repo), "empty")
    write(repo / "x", "a.csv", answer(data, key, "a", coin))
    write(repo / "x", "b.csv", answer(data | {"survey_id": "other"}, key, "b", coin))
    with pytest.raises(ValueError, match="more than one survey"):
        an.human_eval_analyze(str(repo), "x")
    write(repo / "y", "b.csv", answer(data | {"survey_id": "nokey"}, key, "b", coin))
    with pytest.raises(ValueError, match="no key"):
        an.human_eval_analyze(str(repo), "y/b.csv")
