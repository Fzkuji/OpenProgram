"""Reading interview files into segments, and verbatim quote matching."""

import pytest

from auto_research.tasks.qualitative import segments as sg

from .data import IDS, make_project


@pytest.fixture
def paper(tmp_path):
    return make_project(tmp_path)


def test_formats_turns_and_ids(paper):
    files, skipped = sg.collect(paper, "data/interviews")
    assert [f.name for f in files] == ["p1.txt", "p2.md", "p3.vtt", "p4.docx"]
    assert sorted(skipped) == ["data/interviews/README.md", "data/interviews/audio.mp3"]
    notes = []
    segs = sg.read_all(paper, files, notes)
    assert not notes
    coded = [s for s in segs if s["code"]]
    assert [s["id"] for s in coded] == IDS
    by = {s["id"]: s for s in segs}
    assert by["p1.txt:7"]["asked"] == "Why only then?" and by["p1.txt:7"]["participant"] == "p1"
    assert by["p1.txt:9"]["text"] == "Yeah." and not by["p1.txt:9"]["code"]          # too short to code
    assert by["p2.md:3"]["speaker"] == "" and by["p2.md:3"]["participant"] == "p2"   # paragraphs, heading skipped
    assert by["p3.vtt:7"]["text"] == ("Preregistration felt like extra paperwork at first, but now I think it "
                                      "protects me from reviewers.")
    assert by["p3.vtt:7"]["asked"] == "Do you preregister?" and by["p3.vtt:7"]["speaker"] == "Ana"
    assert by["p4.docx:2"]["asked"] == "What would help you share more?"


def test_srt_focus_group_and_continuations(tmp_path):
    srt = tmp_path / "g.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nModerator: What do you think?\n\n"
                   "2\n00:00:03,000 --> 00:00:06,000\nKim: I share everything I can\n\n"
                   "3\n00:00:06,000 --> 00:00:08,000\nKim: as soon as I can.\n\n"
                   "4\n00:00:08,000 --> 00:00:10,000\nLee: I keep my data private for now.\n")
    segs = sg.read_file(tmp_path, srt)
    assert [(s["id"], s["participant"]) for s in segs] == [("g.srt:7", "g/Kim"), ("g.srt:15", "g/Lee")]
    assert segs[0]["text"] == "I share everything I can as soon as I can."
    txt = tmp_path / "t.txt"
    txt.write_text("**Interviewer:** Tell me more.\n**R2:** It started in my PhD.\nI was alone then.\n")
    segs = sg.read_file(tmp_path, txt)
    assert segs[0]["id"] == "t.txt:2" and segs[0]["text"] == "It started in my PhD.\nI was alone then."
    assert segs[0]["asked"] == "Tell me more."


def test_data_must_be_inside_the_project(paper, tmp_path):
    with pytest.raises(ValueError, match="not inside"):
        sg.collect(paper, str(tmp_path))
    with pytest.raises(ValueError, match="does not exist"):
        sg.collect(paper, "data/none")
    files, _ = sg.collect(paper, "data/interviews/p1.txt, data/interviews/README.md")
    assert [f.name for f in files] == ["p1.txt", "README.md"]          # a file named directly is data


def test_quotes():
    text = "Honestly I worry that someone will scoop my results before I publish."
    assert sg.quote_in("I worry that someone   will scoop", text)
    assert sg.quote_in("“honestly I worry … before I publish”", text)
    assert sg.quote_in("I worry that someone ... before I publish", text)
    assert not sg.quote_in("before I publish ... I worry", text)       # pieces out of order
    assert not sg.quote_in("I fear that someone will scoop", text)
    assert not sg.quote_in("I worry", text)                             # too short
    assert sg.quote_in("it’s fine, really", "Yes it's fine, really.")
