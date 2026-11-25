"""writing_style with the profiler and editor scripted; own papers as .tex and .txt."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.proofread import apply_edit
from auto_research.tasks.style import profile as measure
from auto_research.tasks.style import style as wf

MAIN = r"""\documentclass{article}
\begin{document}
\begin{abstract}
In order to evaluate probes, an evaluation of the schedule is performed by us on 3 datasets.
\end{abstract}
\section{Introduction}
Linear probes are widely used \cite{he2016}. It is worth noting that the schedule was chosen by hand in most work.

We study the schedule. We find that it matters a lot for the ranking of models.
\section{Method}
The model is trained with $\eta=0.05$ for 10 epochs, see Table~\ref{tab:a}.
\end{document}
"""
OWN_TEX = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Why do agents forget what they learned yesterday? We argue that memory pruning is the missing piece of
long-running agents, and we show it with three experiments.

Our contributions are threefold. First, we define usage-based pruning. Second, we prove it keeps accuracy.
Third, we release the code.

We test it on long tasks and we find that it works.
\section{Method}
We score each memory by how often it is read. We drop the lowest fifth every fifty steps.
\end{document}
"""
OWN_TXT = """1 Introduction

Large models write fluent text, but we still cannot tell when they are wrong. We propose a check that runs in
seconds and catches most errors.

We measure it on four benchmarks. However, the check misses rare errors, and we discuss why.

In this paper we show that a cheap check is enough for most uses of the model in practice.
"""
PROFILE = {"motivation": "Opens with a direct question or a failure.", "contributions": "Numbered, first/second/third.",
           "voice": "Active, first person plural, short sentences.",
           "examples": [{"quote": "Why do agents forget what they learned yesterday?", "shows": "question opening"},
                        {"quote": "a sentence nobody wrote in any paper", "shows": "invented"}]}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow" / "own_papers" / "memory").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex"}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "workflow" / "own_papers" / "memory" / "main.tex").write_text(OWN_TEX)
    (tmp_path / "check.txt").write_text(OWN_TXT)
    return repo


def edits_for(title, *items):
    return {"edits": [dict(zip(("paragraph", "rule", "old", "new", "why"), i)) for i in items]}


def script(paper, profile=True):
    paras = wf.paper_paragraphs(wf.split_sections(wf.body(wf.flatten(paper, "main.tex"))))
    ids = {p["section"]: [x["id"] for x in paras if x["section"] == p["section"]] for p in paras}
    replies = [
        edits_for("Abstract",
                  (ids["Abstract"][0], "clutter", "In order to evaluate", "To evaluate", "删去冗词。"),
                  (ids["Abstract"][0], "nominalisation", "an evaluation of the schedule is performed by us on 3 datasets",
                   "we evaluate the schedule on 3 datasets", "用动词。"),
                  (ids["Abstract"][0], "clutter", "text the paper never had", "x", "编造。")),
        edits_for("Introduction",
                  (ids["Introduction"][0], "clutter", "Linear probes are widely used \\cite{he2016}.",
                   "Linear probes are widely used.", "删引用。"),
                  (ids["Introduction"][0], "active_voice", "the schedule was chosen by hand",
                   "authors chose the schedule by hand in 12 papers", "新数字。"),
                  (ids["Introduction"][1], "topic_sentence", "We study the schedule.",
                   "The schedule decides how models rank.", "先说要点。")),
        edits_for("Method",
                  (ids["Method"][0], "active_voice", "The model is trained with $\\eta=0.05$ for 10 epochs",
                   "We train the model with $\\eta=0.05$ for 10 epochs", "主动语态。")),
    ]
    return ({wf.profiler: [PROFILE]} if profile else {}) | {wf.editor: replies}


def run(paper, roles, **kw):
    with script_roles(roles) as calls:
        out = json.loads(wf.writing_style(str(paper), **kw))
    return out, calls


def test_learns_style_and_proposes_exact_edits(paper):
    out, calls = run(paper, script(paper), own_papers="../check.txt")
    assert out["profile"] == "learned" and out["own_papers"] == 2 and out["status"] == "PASS_WITH_NOTES"
    prof = calls[wf.profiler][0].text
    assert "Why do agents forget" in prof and "Our contributions are threefold" in prof and "We propose a check" in prof
    style = (paper / "workflow" / "style.md").read_text()
    assert "Opens with a direct question" in style and "| passive_share |" in style
    assert "Why do agents forget what they learned yesterday?" in style and "nobody wrote" not in style
    edit = calls[wf.editor][0].text
    assert "Opens with a direct question" in edit and "clutter: 'In order to'" in edit and "passive: 'is performed'" in edit
    assert len(calls[wf.editor]) == 3
    record = json.loads(Path(out["style"]).read_text())
    assert record["dropped"] == {"old text not in the paragraph": 1, "citations, references or math changed": 1,
                                 "new numbers": 1}
    assert out["proposals"] == 4 and out["exact_edits"] == 4
    assert record["current"]["passive_share"] > 0 and record["learned"]["first_person_per_1k"] > 50
    edits = json.loads(Path(out["edits"]).read_text())
    assert edits["root"] == str(paper)
    e = edits["edits"][0]
    assert set(e) == {"id", "file", "section", "kind", "quote", "replacement", "problem"}
    assert e["file"] == "main.tex" and e["kind"] == "clutter" and e["id"] == "E1"
    assert all(apply_edit(str(paper), x) for x in edits["edits"])
    text = (paper / "main.tex").read_text()
    assert "To evaluate probes, we evaluate the schedule on 3 datasets." in text
    assert "We train the model with $\\eta=0.05$ for 10 epochs" in text
    report = Path(out["report"]).read_text()
    assert "从作者旧论文学到的风格" in report and "## 删繁就简（1）" in report and "被动句占比" in report


def test_style_md_is_kept_and_its_values_used(paper):
    (paper / "workflow" / "style.md").write_text("# Writing style\n\n| Measure | Value |\n| --- | --- |\n"
                                                 "| passive_share | 0.0 |\n\n## Own rules\n\nNever say 'novel'.\n")
    out, calls = run(paper, script(paper, profile=False))
    assert out["profile"] == "style.md" and wf.profiler not in calls
    assert "Never say 'novel'." in calls[wf.editor][0].text
    record = json.loads(Path(out["style"]).read_text())
    assert record["target"]["passive_share"] == 0.0 and "passive_share" in out["deviations"]
    assert "Never say" in (paper / "workflow" / "style.md").read_text()


def test_rules_only_without_earlier_papers(paper):
    for f in (paper / "workflow" / "own_papers").rglob("*.tex"):
        f.unlink()
    out, calls = run(paper, script(paper, profile=False))
    assert out["profile"] == "rules" and out["own_papers"] == 0
    assert not (paper / "workflow" / "style.md").exists()
    assert "none: use the rules only" in calls[wf.editor][0].text
    report = Path(out["report"]).read_text()
    assert "只用 Writing in the Sciences 规则" in report and "checked against the Writing-in-the-Sciences rules only" in report


def test_bad_own_paper_and_missing_path(paper):
    with pytest.raises(ValueError, match="does not exist"):
        wf.writing_style(str(paper), own_papers="nowhere.pdf")
    (paper / "workflow" / "own_papers" / "broken.pdf").write_bytes(b"not a pdf")
    out, _ = run(paper, script(paper))
    assert "broken.pdf" in Path(out["report"]).read_text() and out["own_papers"] == 1


def test_measures():
    text = ("We propose a method. The model is trained in order to perform an evaluation of the data. "
            "However, results may vary, e.g. on small sets.")
    m = measure.metrics([text])
    assert m["sentences"] == 3 and m["passive_share"] == 0.33 and m["connectives_per_1k"]["however"] > 0
    assert m["hedges_per_1k"] > 0 and "evaluation" in m["top_nominalisations"]
    flags = measure.paragraph_flags(text, 35, 200)
    assert "passive: 'is trained'" in flags and "nominalisation: 'perform an evaluation of'" in flags
    gaps = measure.deviations({"passive_share": 0.5, "mean_sentence_words": 22.5}, measure.RULE_TARGETS)
    assert [g["measure"] for g in gaps] == ["passive_share"]
    assert measure.sentences("See Fig. 3 for details on it. Then we stop here now.") == [
        "See Fig. 3 for details on it.", "Then we stop here now."]
