"""paper_translate with the termer and translator scripted and latexmk replaced."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.style import latex
from auto_research.tasks.style import translate as wf

MAIN = r"""\documentclass{article}
\usepackage{amsmath}
\title{Small Linear Probes}
\begin{document}
\maketitle
\begin{abstract}
Linear probes are cheap. We reach 26\% lower loss \cite{he2016}.
\end{abstract}
\input{intro}
\section{Method}
We train a linear probe with $\eta = 0.05$ % tuned by hand
for 10 epochs, see Table~\ref{tab:a}.
\begin{equation}\label{eq:loss}
L = \frac{1}{n}\sum_i (y_i - w^\top x_i)^2
\end{equation}

The probe is evaluated on held-out data.
\begin{table}[t]
\begin{tabular}{lc}
Model & Loss \\
Probe & 0.01 \\
\end{tabular}
\caption{Loss of the \emph{linear probe}.}\label{tab:a}
\end{table}
\bibliography{refs}
\end{document}
"""
INTRO = r"""\section{Introduction}
A linear probe measures representation quality \cite{dosovitskiy2021}.
"""
WORDS = {"Linear probes": "线性探针", "linear probe": "线性探针", "Introduction": "引言", "Method": "方法",
         "Loss": "损失", "Model": "模型", "Probe": "探针", "probe": "探针"}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex"}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "intro.tex").write_text(INTRO)
    (repo / "refs.bib").write_text("@article{he2016, title={ResNet}, year={2016}}\n")
    (repo / "main.pdf").write_bytes(b"old")
    (repo / "main.aux").write_text("aux")
    (repo / "figures").mkdir()
    (repo / "figures" / "a.png").write_bytes(b"png")
    return repo


def units_of(paper):
    units, slots = wf.read_units(paper, latex.included(paper, "main.tex"))
    return units, slots


def zh(masked: str) -> str:
    out = masked
    for en, cn in WORDS.items():
        out = out.replace(en, cn)
    return out


def replies(units, broken=(), percent=()):
    """One translator reply per chunk; broken ids lose their first placeholder, percent
    ids gain a raw %."""
    out = []
    for chunk in wf.chunks(units):
        items = []
        for u in chunk:
            text = zh(u["masked"])
            if u["id"] in broken:
                text = latex.SLOT.sub("", text, count=1)
            if u["id"] in percent:
                text += " 26%"
            items.append({"id": u["id"], "text": text})
        out.append({"paragraphs": items})
    return out


TERMS = {"terms": [{"term": "linear probe", "translation": "线性探针"},
                   {"term": "held-out data", "translation": "留出数据"},
                   {"term": "not in the paper", "translation": "无"}]}


@pytest.fixture
def latexmk(monkeypatch):
    seen = []

    def fake(cmd, folder):
        seen.append((cmd, folder))
        tex = (folder / "main.tex").read_text()
        line = next(i for i, l in enumerate(tex.splitlines(), 1) if "\\section{" in l)
        (folder / "main.log").write_text(f"./main.tex:{line}: Undefined control sequence.\nl.{line} ...\n")
        (folder / "main.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(wf, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(wf, "run_latexmk", fake)
    return seen


def run(paper, script, **kw):
    with script_roles(script) as calls:
        out = json.loads(wf.paper_translate(str(paper), **kw))
    return out, calls


def test_translates_keeps_latex_and_compiles(paper, latexmk):
    units, slots = units_of(paper)
    sent = [u for u in units if u["send"]]
    first = replies(units, broken={sent[2]["id"]}, percent={sent[1]["id"]})
    retry = {"paragraphs": [{"id": u["id"], "text": zh(u["masked"]) if u is sent[1]
                             else latex.SLOT.sub("", zh(u["masked"]), count=1)} for u in (sent[1], sent[2])]}
    out, calls = run(paper, {wf.termer: [TERMS], wf.translator: first + [retry]})
    assert out["status"] == "PASS_WITH_NOTES" and out["kept"] == 1 and out["compile_errors"] == 1
    # sections start new calls; the retry carries the problem
    assert len(calls[wf.translator]) == len(first) + 1
    assert "placeholders missing" in calls[wf.translator][-1].text
    assert "⟦" in calls[wf.translator][0].text and "\\cite" not in calls[wf.translator][0].text
    tree = Path(out["source"])
    main = (tree / "main.tex").read_text()
    assert main.startswith("\\documentclass{article}\n\\usepackage[UTF8,fontset=fandol]{ctex}\n")
    assert "\\section{方法}" in main and "% tuned by hand" in main and "\\cite{he2016}" in main
    assert "26\\%" in main and "\\label{eq:loss}" in main and "w^\\top x_i" in main
    assert "Model & Loss" not in main and "模型 & 损失" in main
    assert "\\caption{损失 of the \\emph{线性探针}.}" in main
    assert (tree / "intro.tex").read_text() == INTRO
    assert latex.structure(main) == latex.structure(MAIN.replace("\\documentclass{article}\n",
                                                                "\\documentclass{article}\n\\usepackage[UTF8,fontset=fandol]{ctex}\n"))
    assert (tree / "refs.bib").exists() and (tree / "figures" / "a.png").exists()
    assert not (tree / "main.aux").exists() and not (tree / ".git").exists() and not (tree / "workflow").exists()
    assert latexmk[0][0][1] == "-xelatex"
    assert Path(out["pdf"]).name == "paper.zh.pdf" and Path(out["pdf"]).read_bytes() == b"%PDF"
    record = json.loads(Path(out["translate"]).read_text())
    assert record["kept"][0]["id"] == sent[2]["id"] and sent[2]["file"] == "intro.tex"
    error = record["build"]["errors"][0]
    assert error["file"] == "main.tex" and "\\section{" in error["text"]
    # the source paragraph that failed twice keeps its English
    assert latex.restore(sent[2]["masked"], slots) in (tree / "intro.tex").read_text()
    glossary = (paper / "workflow" / "glossary.md").read_text()
    assert "| Term | zh |" in glossary and "| linear probe | 线性探针 |" in glossary and "not in the paper" not in glossary
    assert record["missed_terms"] and "held-out data" in json.dumps(record["missed_terms"])
    report = Path(out["report"]).read_text()
    assert "编译错误" in report and "保留原文" in report and "术语未按术语表翻译" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]][:3] == ["read", "terms", "glossary"]
    assert (paper / "main.tex").read_text() == MAIN


def test_author_glossary_overrides_and_grows(paper, latexmk):
    (paper / "workflow" / "glossary.md").write_text("# Glossary\n\n| Term | zh |\n| --- | --- |\n| linear probe | 线性探测器 |\n")
    units, _ = units_of(paper)
    out, calls = run(paper, {wf.termer: [TERMS], wf.translator: replies(units)})
    assert "线性探测器" in calls[wf.translator][0].text and "线性探针\"" not in calls[wf.translator][0].text
    text = (paper / "workflow" / "glossary.md").read_text()
    assert text.count("linear probe") == 1 and "| held-out data | 留出数据 |" in text
    record = json.loads(Path(out["translate"]).read_text())
    assert record["glossary"]["linear probe"] == "线性探测器" and record["added_terms"] == ["held-out data"]


def test_other_language_glossary_is_left_alone(paper, latexmk):
    (paper / "workflow" / "glossary.md").write_text("| Term | ja |\n| --- | --- |\n| linear probe | 線形プローブ |\n")
    units, _ = units_of(paper)
    out, _ = run(paper, {wf.termer: [TERMS], wf.translator: replies(units)}, language="de")
    assert "線形プローブ" in (paper / "workflow" / "glossary.md").read_text()
    assert "held-out" not in (paper / "workflow" / "glossary.md").read_text()
    main = (Path(out["source"]) / "main.tex").read_text()
    assert "ctex" not in main and "xeCJK" not in main
    assert "is for ja, not de" in Path(out["report"]).read_text()


def test_no_latexmk_and_failed_build(paper, monkeypatch):
    units, _ = units_of(paper)
    monkeypatch.setattr(wf, "which", lambda t: None)
    out, _ = run(paper, {wf.termer: [TERMS], wf.translator: replies(units)})
    assert out["pdf"] == "" and out["status"] == "PASS_WITH_NOTES"
    assert "latexmk not found" in Path(out["report"]).read_text()
    monkeypatch.setattr(wf, "which", lambda t: "/usr/bin/latexmk")
    monkeypatch.setattr(wf, "run_latexmk", lambda cmd, folder: (folder / "main.log").write_text("! Emergency stop.\nl.3 x\n"))
    out, _ = run(paper, {wf.termer: [TERMS], wf.translator: replies(units)})
    assert out["status"] == "FAIL" and out["compile_errors"] == 1


def test_bad_inputs(paper):
    with pytest.raises(ValueError, match="language must be one of"):
        wf.paper_translate(str(paper), language="xx")
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.paper_translate(str(paper / "figures"))


def test_mask_round_trip_and_problems():
    src = MAIN + INTRO + r"\href{http://x}{text} \\[2pt] \textbf{bold {nested} words} 50\$ \(a\) \[b\]" + "\n"
    masked, slots = latex.mask(src)
    table = {k: s for k, s in enumerate(slots, 1)}
    assert latex.restore(masked, table) == src
    assert "bold" in masked and "nested" in masked and "http" not in masked and "he2016" not in masked
    p = latex.paragraphs(masked)
    assert "".join(p) == masked
    one = next(x for x in p if "Method" in x)
    assert latex.problems(one, one, table) == []
    assert latex.problems(one, one + "⟦1⟧", table)[0].startswith("placeholders added")
    assert "special characters" in latex.problems(one, one + " {x}", table)[0]
    included = latex.included(Path("/nonexistent"), "main.tex")
    assert included == []
