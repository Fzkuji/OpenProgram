"""thematic_analysis with both coders, the merger and the themer scripted."""

import csv
import json
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.qualitative import thematic as wf

from .data import IDS, make_project

QUOTE = {"p1.txt:3": "upload my data to a public repository",
         "p1.txt:7": "someone will scoop my results",
         "p2.md:3": "nobody in my lab has time",
         "p2.md:5": "funder now requires a data management plan",
         "p3.vtt:7": "now I think it protects me from reviewers",
         "p4.docx:2": "Training in version control would help"}
TRUE = {"p1.txt:3": ["Sharing data"], "p1.txt:7": ["Fear of scooping"], "p2.md:3": ["Time constraints"],
        "p2.md:5": ["Mandates"], "p3.vtt:7": ["Preregistration"], "p4.docx:2": ["Training needs"]}
NAMES = ["Sharing data", "Fear of scooping", "Time constraints", "Mandates", "Preregistration", "Training needs"]


@pytest.fixture(autouse=True)
def one_worker(monkeypatch):
    monkeypatch.setattr(wf, "WORKERS", 1)          # scripted replies are answered in order


@pytest.fixture
def paper(tmp_path):
    return make_project(tmp_path)


def open_reply(prefix):
    segs = [{"segment": s, "codes": [{"label": f"{prefix} {TRUE[s][0].lower()}", "definition": "What it says.",
                                      "quote": QUOTE[s]}]} for s in IDS]
    segs[0]["codes"].append({"label": f"{prefix} invented", "definition": "x", "quote": "words nobody said here"})
    return {"segments": segs}


def merger_reply():
    codes = []
    for n in NAMES:
        codes.append({"name": n, "definition": f"Talk about {n.lower()}.", "include": "When it is said.",
                      "exclude": "Otherwise.", "from_a": [f"sem {n.lower()}", "never used"],
                      "from_b": [f"lat {n.lower()}"]})
    return {"codes": codes}


def apply_reply(ids, extra=None, fake=False):
    out = []
    for s in ids:
        codes = [{"code": c, "quote": QUOTE[s]} for c in TRUE[s]]
        for c in (extra or {}).get(s, []):
            codes.append({"code": c, "quote": QUOTE[s]})
        out.append({"segment": s, "codes": codes})
    if fake:
        out[0]["codes"].append({"code": "Mandates", "quote": "a sentence that is not in the segment"})
    return {"segments": out}


def themes_reply():
    return {"themes": [
        {"name": "Openness is weighed against career risk", "concept": "Sharing waits until it is safe.",
         "codes": ["Sharing data", "Fear of scooping"],
         "quotes": [{"segment": "p1.txt:3", "quote": "I always upload my data to a public repository"},
                    {"segment": "p1.txt:7", "quote": "I worry that someone will scoop my results"}]},
        {"name": "Structures push and enable openness", "concept": "Rules and skills make sharing routine.",
         "codes": ["Mandates", "Training needs", "Preregistration", "Fear of scooping"],
         "quotes": [{"segment": "p2.md:5", "quote": "Our funder now requires a data management plan"},
                    {"segment": "p4.docx:2", "quote": "Training in version control would help me share code"},
                    {"segment": "p2.md:3", "quote": "nobody in my lab has time to clean it up"}]}]}


def run(paper, script, **kw):
    with script_roles(script) as calls:
        out = json.loads(wf.thematic_analysis(str(paper), "data/interviews", **kw))
    return out, calls


def test_full_run_writes_codebook_and_agrees(paper):
    first = [s for s in IDS if s != "p4.docx:2"]
    script = {wf.coder_a: [open_reply("sem"), apply_reply(first), apply_reply(["p4.docx:2"])],
              wf.coder_b: [open_reply("lat"), apply_reply(IDS, extra={"p2.md:3": ["Fear of scooping"]}, fake=True)],
              wf.merger: [merger_reply()], wf.themer: [themes_reply()]}
    out, calls = run(paper, script)
    assert out["segments"] == 6 and out["participants"] == 4 and out["codes"] == 6
    assert out["kappa"] == 0.906 and out["low_kappa"] == ["Fear of scooping"]
    assert out["status"] == "PASS" and out["themes"] == 2
    # Coders are blind: each sees the segments, never the other's codes.
    a_open, b_open = calls[wf.coder_a][0], calls[wf.coder_b][0]
    assert "inductive, no codebook" in a_open.text and "looking past" not in a_open.text
    assert "looking past" in b_open.text and "beneath the words" in b_open.text
    assert "lat " not in a_open.text and "sem " not in b_open.text
    assert "How do linguists decide whether to share their data and code?" in a_open.text     # RQ of claims.md
    assert "Why only then?" in a_open.text and "Yeah." not in a_open.text
    assert len(calls[wf.coder_a]) == 3                                  # the left-out segment asked again
    merge = calls[wf.merger][0].text
    assert "sem sharing data" in merge and "lat sharing data" in merge and "sem invented" not in merge
    themer = calls[wf.themer][0].text
    assert "Talk about mandates." in themer and "reflexive thematic analysis" in themer
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "thematic_analysis"
    book = (paper / "workflow" / "codebook.md").read_text()
    assert "## Fear of scooping" in book and "Include: When it is said." in book
    assert wf.parse_codebook(book)[1]["definition"] == "Talk about fear of scooping."
    rows = list(csv.DictReader((folder / "coded.csv").open()))
    assert {(r["segment"], r["code"], r["coder"]) for r in rows} >= {("p2.md:3", "Fear of scooping", "B"),
                                                                     ("p4.docx:2", "Training needs", "A")}
    assert not any(r["code"] == "Mandates" and r["segment"] == "p1.txt:3" for r in rows)    # bad quote dropped
    assert len(list(csv.DictReader((folder / "open_codes.csv").open()))) == 12
    record = json.loads((folder / "analysis.json").read_text())
    t1, t2 = record["themes"]
    assert t1["participants"] == 1 and t1["segments"] == 2 and len(t1["quotes"]) == 2
    assert t2["codes"] == ["Mandates", "Training needs", "Preregistration"]      # duplicate code left out
    assert [q["segment"] for q in t2["quotes"]] == ["p2.md:5", "p4.docx:2"]       # quote outside its codes dropped
    assert t2["participants"] == 3 and t2["segments"] == 3
    assert [r["new"] for r in record["saturation"]] == [2, 2, 1, 1] and not record["saturated"]
    assert [r["emerged_new"] for r in record["saturation"]] == [2, 2, 1, 1]
    notes = " ".join(record["notes"])
    assert "Time constraints" in notes and "already in an earlier theme" in notes
    report = (folder / "report.md").read_text()
    assert "合并 Cohen's kappa 0.91" in report and "⚠" in report and "## 饱和度" in report
    assert "来自 claims.md" in report
    quotes = (folder / "quotes.md").read_text()
    assert '"I worry that someone will scoop my results" (p1, p1.txt:7)' in quotes
    assert r"\begin{quote}" in (folder / "quotes.tex").read_text()
    run_json = json.loads(Path(out["run"]).read_text())
    assert set(run_json["roles"]) == {"coder_a", "coder_b", "merger", "themer"}


def test_second_run_uses_the_authors_codebook(paper):
    (paper / "workflow" / "codebook.md").write_text(wf.render_codebook(
        [{"name": n, "definition": f"Author: {n}.", "include": "", "exclude": ""} for n in NAMES], "RQ"))
    disagree = {s: [n for n in NAMES if n not in TRUE[s]][:2] for s in IDS}
    script = {wf.coder_a: [apply_reply(IDS)], wf.coder_b: [apply_reply(IDS, extra=disagree)],
              wf.themer: [themes_reply()]}
    out, calls = run(paper, script, question="Why do researchers share late?")
    assert not calls[wf.merger] and len(calls[wf.coder_a]) == 1
    assert "Author: Mandates." in calls[wf.coder_a][0].text and "Apply the codebook" in calls[wf.coder_a][0].text
    assert "Why do researchers share late?" in calls[wf.themer][0].text
    assert out["status"] == "PASS_WITH_NOTES" and out["kappa"] < 0.6
    folder = Path(out["report"]).parent
    assert not (folder / "open_codes.csv").exists()
    report = (folder / "report.md").read_text()
    assert "参数给定" in report and "开放编码新出现" not in report


def test_bad_inputs(paper, tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        wf.thematic_analysis(str(paper), "data/interviews", codebook="workflow/none.md")
    with pytest.raises(ValueError, match="not inside"):
        wf.thematic_analysis(str(paper), str(tmp_path))
    with pytest.raises(ValueError, match="Git repository"):
        wf.thematic_analysis(str(tmp_path), "x")
    (paper / "data" / "empty").mkdir()
    (paper / "data" / "empty" / "a.txt").write_text("Yes.\n")
    with pytest.raises(ValueError, match="words or more"):
        wf.thematic_analysis(str(paper), "data/empty")


def test_failed_batch_is_a_note(paper):
    script = {wf.coder_a: [open_reply("sem"), RuntimeError("model down"), RuntimeError("model down")],
              wf.coder_b: [open_reply("lat"), apply_reply(IDS)],
              wf.merger: [merger_reply()], wf.themer: [themes_reply()]}
    out, _ = run(paper, script)
    record = json.loads((Path(out["report"]).parent / "analysis.json").read_text())
    assert out["status"] == "PASS_WITH_NOTES" and len(record["missing"]) == 6
    assert any("could not apply the codebook" in n for n in record["notes"])
