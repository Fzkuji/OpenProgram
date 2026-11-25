"""talk_prep with the asker and answerer scripted, slides as a small pptx."""

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.present import talk as wf

TEX = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We prune agent memory by usage and keep accuracy at 73.5\% on 1{,}000 tasks.
\end{abstract}
\section{Method}
We score each memory by how often it is read and drop the lowest 20\% every 50 steps.
\section{Limitations}
We test one benchmark only, so other domains remain open.
\end{document}
"""

WORK = "arxiv:2401.00001"


def slide(text):
    runs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in text)
    return f'<p:sld xmlns:a="a" xmlns:p="p"><p:cSld><p:spTree>{runs}</p:spTree></p:cSld></p:sld>'


def make_pptx(path: Path, slides: dict[int, list[str]]):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for n, text in slides.items():
            z.writestr(f"ppt/slides/slide{n}.xml", slide(text))
        z.writestr("ppt/slides/_rels/slide1.xml.rels", "<Relationships/>")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "rules.json").write_text(json.dumps(
        {"main_tex": "main.tex", "deliverables": [{"name": "Poster", "path": "poster.pdf"},
                                                  {"name": "Talk", "path": "talk.pptx"}]}))
    make_pptx(repo / "talk.pptx", {1: ["Sparse Memory", "for Agents"], 2: ["Results &amp; costs"],
                                   10: ["word " * 60]})
    (repo / "workflow" / "evidence.csv").write_text(
        "id,claim,statement,source,script\nE1,C1,Retrieval is 2.4 times faster.,results.tsv,\n")
    (repo / "workflow" / "watch.csv").write_text(
        "id,title,url,date,claim,verdict,reason,handled\n"
        f"{WORK},MemPrune: Forgetting Agents,https://x,2025-01-01,C1,overlap,same idea,\n"
        "arxiv:2,Unrelated,https://y,2025-01-01,,unrelated,no,\n")
    review = repo / "drafts" / "paper_review" / "20260101-000000"
    review.mkdir(parents=True)
    (review / "review.json").write_text(json.dumps({"weaknesses": [
        {"id": "W1", "severity": "major", "where": "Limitations", "issue": "Only one benchmark.", "fix": "Add one."}]}))
    return repo


KINDS = ["so_what", "clarifying", "comparison", "limitations", "methodological"] * 3


def questions():
    out = []
    for i, kind in enumerate(KINDS, 1):
        out.append({"kind": kind, "question": f"Question {i} of kind {kind}?",
                    "work": WORK if kind == "comparison" else ""})
    return {"questions": out}


def answers(ids):
    out = [{"id": i, "answer": "It is grounded.", "quote": "drop the lowest 20\\% every 50 steps", "evidence": ""}
           for i in ids]
    out[0].update(quote="", evidence="E1", answer="Retrieval is 2.4 times faster.")
    out[1].update(quote="", answer="We never ran that.")                                  # unsupported
    out[2].update(answer="Accuracy rises to 99.1 points.")                              # number not in the paper
    out[3].update(quote="a sentence the paper never wrote at all")                       # bad quote
    out[4].update(answer="word " * 80)                                                   # longer than 30 seconds
    return {"answers": out}


def run(paper, **kw):
    ids = [f"Q{i}" for i in range(1, 16)]
    with script_roles({wf.asker: [questions()], wf.answerer: [answers(ids)]}) as calls:
        out = json.loads(wf.talk_prep(str(paper), **kw))
    return out, calls


def test_questions_answers_and_flags(paper):
    out, calls = run(paper, minutes="2")
    ask = calls[wf.asker][0]
    assert "ordered from easy to hard" in ask.text
    assert "Only one benchmark." in ask.text and "MemPrune" in ask.text and "Unrelated" not in ask.text
    assert "Results & costs" in ask.text                                       # slide text, unescaped
    answer = calls[wf.answerer][0]
    assert "at most 75" in answer.text and "Retrieval is 2.4 times faster." in answer.text
    assert out["questions"] == 15 and out["needs_answer"] == 3 and out["too_long"] == 1
    assert out["status"] == "PASS_WITH_NOTES" and out["slides"] == 3
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "talk_prep"
    record = json.loads((folder / "qa.json").read_text())
    items = record["items"]
    assert [i["kind"] for i in items][:3] == ["clarifying"] * 3 and [i["kind"] for i in items][-3:] == ["so_what"] * 3
    assert [i["id"] for i in items] == [f"Q{i}" for i in range(1, 16)]
    assert items[0]["evidence"] == "E1" and items[0]["status"] == "backed"
    assert "no quote of the paper or evidence row" in " ".join(items[1]["flags"])
    assert items[2]["flags"] == ["numbers not in the paper: 99.1"]
    assert "the quote is not in the paper" in items[3]["flags"]
    assert items[4]["long"] and not items[4]["flags"]
    assert items[5]["section"] == "Method" and items[5]["status"] == "backed"
    timing = record["timing"]
    assert timing["slides"] == 3 and timing["dense"] == [3] and timing["words"][:2] == [4, 3]
    report = (folder / "qa.md").read_text()
    assert "## 对比（comparison）" in report and "MemPrune: Forgetting Agents" in report and wf.NEEDS in report
    assert record["audio"] == "" and not (folder / "qa.aiff").exists()
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "ask", "answer", "check", "report"]


def test_audio_speaks_the_questions_only(paper, monkeypatch):
    spoken = []

    def say_file(script, out):
        spoken.append(script.read_text())
        out.write_bytes(b"aiff")

    monkeypatch.setattr(wf, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(wf, "say_file", say_file)
    out, _ = run(paper, audio="yes")
    assert out["audio"].endswith("qa.aiff") and Path(out["audio"]).exists()
    assert spoken[0].count("[[slnc 8000]]") == 15 and "Question 1. Question 2 of kind clarifying?" in spoken[0]
    assert "grounded" not in spoken[0]
    assert not (Path(out["audio"]).parent / "qa_questions.txt").exists()
    monkeypatch.setattr(wf, "which", lambda t: None)
    out, _ = run(paper, audio="yes")
    assert out["audio"] == "" and "say` not found" in Path(out["report"]).read_text()


def test_asker_must_name_a_competing_work_and_use_every_kind(paper):
    bad = questions()
    bad["questions"][2]["work"] = ""
    with pytest.raises(ValueError, match="must name one of the competing works"):
        with script_roles({wf.asker: [bad]}):
            wf.talk_prep(str(paper))
    bad = questions()
    for q in bad["questions"]:
        q.update(kind="clarifying", work="")
    with pytest.raises(ValueError, match="left out question kinds"):
        with script_roles({wf.asker: [bad]}):
            wf.talk_prep(str(paper))


def test_arguments_and_slide_sources(paper, tmp_path):
    with pytest.raises(ValueError, match="minutes"):
        wf.talk_prep(str(paper), minutes="0")
    with pytest.raises(ValueError, match="audio"):
        wf.talk_prep(str(paper), audio="maybe")
    assert wf.pptx_text(paper / "talk.pptx")[0] == "Sparse Memory for Agents"
    t = wf.time_slides({"pages": ["a b"] * 20}, 10)
    assert "cut about 10" in t["notes"][0]


def test_pdf_slides_by_pdftotext(paper):
    pypdf = pytest.importorskip("pypdf")
    if not wf.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=640, height=360)
    writer.add_blank_page(width=640, height=360)
    with open(paper / "slides.pdf", "wb") as f:
        writer.write(f)
    (paper / "workflow" / "rules.json").write_text(json.dumps(
        {"main_tex": "main.tex", "deliverables": [{"name": "Slides", "path": "slides.pdf"}]}))
    out, _ = run(paper, minutes="2")
    assert out["slides"] == 2
