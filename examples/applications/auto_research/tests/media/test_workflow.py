"""The paper_media entry with the scriptwriter scripted and the audio and video tools replaced."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.media import workflow as wf

TEX = r"""\documentclass{article}
\title{Sparse Memory for Agents}
\begin{document}
\maketitle
\begin{abstract}
We prune agent memory by usage and keep accuracy at 73.5\% on 1{,}000 tasks.
\end{abstract}
\section{Introduction}
Agents keep every memory they write, so retrieval slows down as the store grows.
\section{Method}
We score each memory by how often it is read and drop the lowest 20\% every 50 steps.
\section{Experiments}
Against 12 baselines, retrieval is 2.4 times faster with the same accuracy.
\section{Limitations}
We test one benchmark only.
\end{document}
"""

PAGES = ["Sparse Memory for Agents", "Results: 73.5% accuracy, 2.4x faster than 12 baselines"]


def line(speaker, text):
    return {"speaker": speaker, "text": text}


SCRIPT = {"title": "Forgetting on purpose", "lines": [
    line("A", "This paper asks what happens when an agent forgets on purpose."),
    line("B", "Why would forgetting help?"),
    line("A", "Retrieval slows as the store grows. They drop the lowest 20% every 50 steps."),
    line("B", "And does it hold up?"),
    line("A", "Accuracy stays at 73.5% on 1,000 tasks, and retrieval is 2.4 times faster."),
    line("B", "I heard it beats 37 baselines by 9.8 points."),        # numbers the paper does not have
    line("A", "No, they compare against 12 baselines, and test one benchmark only."),
]}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "rules.json").write_text(json.dumps(
        {"main_tex": "main.tex", "deliverables": [{"name": "Slides", "path": "slides.pdf"}]}))
    return repo


def two_page_pdf(path: Path) -> Path:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=640, height=360)
    writer.add_blank_page(width=640, height=360)
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def tools(monkeypatch):
    """All tools present; the synthesis helpers write small fake files instead of running."""
    made = {"say": [], "segments": []}
    monkeypatch.setattr(wf, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(wf, "available_voices", lambda: ["Samantha", "Daniel", "Alex"])

    def say_to(text, voice, path):
        made["say"].append((voice, text))
        path.write_bytes(b"x" * 10)

    def join_audio(clips, out):
        assert all(c.exists() for c in clips)
        out.write_bytes(b"m4a" * 1000)

    def render_pages(pdf, n, folder):
        out = [folder / f"page-{i}.png" for i in range(1, n + 1)]
        for p in out:
            p.write_bytes(b"png")
        return out

    def make_segment(image, audio, out):
        made["segments"].append((image.name, audio.name if audio else None))
        out.write_bytes(b"seg")

    def concat_video(segments, out):
        out.write_bytes(b"mp4" * 1000)

    monkeypatch.setattr(wf, "say_to", say_to)
    monkeypatch.setattr(wf, "join_audio", join_audio)
    monkeypatch.setattr(wf, "render_pages", render_pages)
    monkeypatch.setattr(wf, "make_segment", make_segment)
    monkeypatch.setattr(wf, "concat_video", concat_video)
    monkeypatch.setattr(wf, "probe", lambda path: 1.5)
    return made


def run(paper, **kw):
    return json.loads(wf.paper_media(str(paper), **kw))


def test_podcast_drops_lines_with_numbers_the_paper_lacks(paper, tools):
    with script_roles({wf.scriptwriter: [SCRIPT]}) as calls:
        out = run(paper, minutes="1")
    req = calls[wf.scriptwriter][0]
    assert "HOST B is a smart outsider" in req.text                          # docstring
    assert "drop the lowest 20" in req.text and "Limitations" in req.text    # the paper, by section
    assert out["kind"] == "podcast" and out["status"] == "PASS_WITH_NOTES" and out["dropped"] == 1
    assert out["media"].endswith("podcast.m4a") and Path(out["media"]).exists()
    assert out["report"].startswith(str(paper / "drafts" / "paper_media"))
    record = json.load(open(Path(out["report"]).with_name("media.json"), encoding="utf-8"))
    assert record["dropped"] == [{"line": 6, "speaker": "B", "text": SCRIPT["lines"][5]["text"],
                                  "numbers": ["37", "9.8"]}]
    assert len(record["lines"]) == 6 and record["voices"] == {"A": "Samantha", "B": "Daniel"}
    assert [v for v, _ in tools["say"]] == ["Samantha", "Daniel"] * 2 + ["Samantha"] * 2   # one clip per kept line
    assert all(item["seconds"] == 1.5 for item in record["lines"])
    assert not list(Path(out["report"]).parent.glob("_clips*"))           # temp clips are removed
    report = open(out["report"], encoding="utf-8").read()
    assert "**HOST A（Samantha）**" in report and "37、9.8" in report
    trail = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]] == ["read", "script", "ground", "synthesize", "report"]
    assert set(trail["roles"]) == {"scriptwriter"}


def test_voice_override_and_unknown_voice(paper, tools):
    with script_roles({wf.scriptwriter: [SCRIPT]}):
        out = run(paper, minutes="1", voice="Alex,Samantha")
    assert json.load(open(Path(out["report"]).with_name("media.json")))["voices"] == {"A": "Alex", "B": "Samantha"}
    with pytest.raises(ValueError, match="not installed"):
        with script_roles({wf.scriptwriter: [SCRIPT]}):
            run(paper, minutes="1", voice="Nobody,Daniel")


def test_missing_tools_write_only_the_script(paper, tools, monkeypatch):
    monkeypatch.setattr(wf, "which", lambda t: None if t == "ffmpeg" else f"/usr/bin/{t}")
    with script_roles({wf.scriptwriter: [SCRIPT]}):
        out = run(paper, minutes="1")
    assert out["status"] == "PASS_WITH_NOTES" and out["media"] == "" and out["seconds"] == 0
    assert tools["say"] == []
    record = json.load(open(Path(out["report"]).with_name("media.json")))
    assert record["notes"] == ["ffmpeg not found: only the script was written"]
    assert Path(out["report"]).read_text().count("HOST") == 6


def test_video_narrates_the_slides_page_by_page(paper, tools, monkeypatch):
    two_page_pdf(paper / "slides.pdf")
    monkeypatch.setattr(wf, "page_texts", lambda pdf: list(PAGES))
    narration = {"pages": [
        {"page": 2, "narration": "Accuracy stays at 73.5%. It is 2.4 times faster. It wins on 99 datasets."},
        {"page": 1, "narration": "This talk is about agents that forget on purpose."}]}
    with script_roles({wf.scriptwriter: [narration]}) as calls:
        out = run(paper, kind="video", minutes="1")
    req = calls[wf.scriptwriter][0]
    assert "Write the spoken narration of a slide video" in req.text and "2.4x faster than 12 baselines" in req.text
    assert out["kind"] == "video" and out["media"].endswith("video.mp4") and out["dropped"] == 1
    record = json.load(open(Path(out["report"]).with_name("media.json")))
    assert record["source"] == "slides" and record["voices"] == {"narrator": "Samantha"}
    assert [p["narration"] for p in record["pages"]] == [
        "This talk is about agents that forget on purpose.", "Accuracy stays at 73.5%. It is 2.4 times faster."]
    assert record["dropped"] == [{"text": "It wins on 99 datasets.", "numbers": ["99"], "page": 2}]
    assert tools["segments"] == [("page-1.png", "0001.aiff"), ("page-2.png", "0002.aiff")]
    assert not list(Path(out["report"]).parent.glob("_segments*"))


def test_video_falls_back_to_the_paper_pdf_and_caps_pages(paper, tools, monkeypatch):
    two_page_pdf(paper / "main.pdf")
    monkeypatch.setattr(wf, "page_texts", lambda pdf: ["p1", "p2", "p3", "p4"])
    narration = {"pages": [{"page": 1, "narration": "The problem."}]}
    with script_roles({wf.scriptwriter: [narration]}):
        out = run(paper, kind="video", minutes="0.5")                     # one page for half a minute
    record = json.load(open(Path(out["report"]).with_name("media.json")))
    assert record["source"] == "paper" and len(record["pages"]) == 1 and out["status"] == "PASS"


def test_video_without_a_pdf_and_bad_arguments(paper):
    with pytest.raises(ValueError, match="no PDF"):
        wf.paper_media(str(paper), kind="video")
    with pytest.raises(ValueError, match="kind"):
        wf.paper_media(str(paper), kind="radio")
    with pytest.raises(ValueError, match="minutes"):
        wf.paper_media(str(paper), minutes="90")


def test_number_grounding():
    source = r"accuracy 73.5\% on 1{,}000 tasks against 12 baselines in 2024"
    assert wf.numbers("We ran 1,000 tasks, 3 seeds, 0.5 dropout and 12 baselines.") == ["1000", "0.5", "12"]
    assert wf.ungrounded("73.5% on 1,000 tasks with 12 baselines", source) == []
    assert wf.ungrounded("73 percent, 3.5 points, 120 runs", source) == ["73", "3.5", "120"]
    kept, dropped = wf.ground_sentences("Fine in 2024. Bad at 88.1. Three seeds.", source)
    assert kept == "Fine in 2024. Three seeds." and dropped == [{"text": "Bad at 88.1.", "numbers": ["88.1"]}]


def test_script_checks_raise():
    with pytest.raises(ValueError, match="host A or B"):
        wf.check_podcast({"lines": [line("C", "hi")] * 4}, 4, 8)
    with pytest.raises(ValueError, match="both hosts"):
        wf.check_podcast({"lines": [line("A", "hi")] * 4}, 4, 8)
    with pytest.raises(ValueError, match="not 4 to 8"):
        wf.check_podcast({"lines": [line("A", "hi"), line("B", "yo")]}, 4, 8)
    with pytest.raises(ValueError, match="unknown or repeated page"):
        wf.check_narration({"pages": [{"page": 1, "narration": "a"}, {"page": 1, "narration": "b"}]}, 2)
    with pytest.raises(ValueError, match="without narration"):
        wf.check_narration({"pages": [{"page": 1, "narration": "a"}]}, 2)
    assert wf.check_narration(json.dumps({"pages": [{"page": 1, "narration": " a  b "}]}), 1) == ["a b"]
    schema = wf.narration_schema(3, 50)["properties"]["pages"]
    assert schema["minItems"] == schema["maxItems"] == 3
    lines = wf.podcast_schema(4, 12)["properties"]["lines"]
    assert (lines["minItems"], lines["maxItems"]) == (4, 12)
    assert lines["items"]["properties"]["speaker"]["enum"] == ["A", "B"]


def test_a_failing_tool_is_a_note(paper, tools, monkeypatch):
    def broken(text, voice, path):
        raise RuntimeError("say exited 1: no voice")
    monkeypatch.setattr(wf, "say_to", broken)
    with script_roles({wf.scriptwriter: [SCRIPT]}):
        out = run(paper, minutes="1")
    record = json.load(open(Path(out["report"]).with_name("media.json")))
    assert out["media"] == "" and record["notes"] == ["synthesis failed: say exited 1: no voice"]
    assert not list(Path(out["report"]).parent.glob("_clips*"))


@pytest.mark.skipif(not (shutil.which("say") and shutil.which("ffmpeg")), reason="needs macOS say and ffmpeg")
def test_real_two_line_podcast(tmp_path):
    voices = wf.pick_voices("", wf.available_voices(), 2)
    out = wf.synthesize_podcast([line("A", "Hello there."), line("B", "Hi.")], voices, tmp_path / "podcast.m4a")
    assert Path(out).exists() and Path(out).stat().st_size > 1024
    assert [p.name for p in tmp_path.iterdir()] == ["podcast.m4a"]
