"""project_page with the webwriter scripted, pdftoppm and HEAD requests replaced."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.outreach import page as pg

TEX = r"""\documentclass{article}
\usepackage{graphicx}
\graphicspath{{figures/}}
\title{Sparse Memory for Agents}
\author{Jane Doe\thanks{Equal contribution.} \\ Example University \\ \texttt{jane@example.edu} \and John Roe \\ Lab Two}
\begin{document}
\maketitle
\begin{abstract}
We prune agent memory by usage and keep accuracy at 73.5\% on 1{,}000 tasks.
\end{abstract}
\section{Introduction}
Agents keep every memory they write, so retrieval slows down as the store grows.
\section{Method}
We score each memory by how often it is read and drop the lowest 20\% every 50 steps.
\begin{figure}[t]
\includegraphics[width=\linewidth]{overview}
\caption{Overview of \textbf{pruning}.}\label{fig:overview}
\end{figure}
\begin{figure}[t]
\includegraphics{curve.png}
\caption{Accuracy over 1{,}000 tasks.}\label{fig:curve}
\end{figure}
\section{Experiments}
Against 12 baselines, retrieval is 2.4 times faster with the same accuracy.
\end{document}
"""

CONTENT = {
    "tldr": "Forgetting on purpose keeps agents at 73.5% accuracy while retrieval gets 2.4 times faster.",
    "summary": "Agents keep every memory. We drop the rarely read ones. Accuracy holds at 73.5%. It beats 37 rivals.",
    "method": "Each memory is scored by how often it is read. The lowest 20% go every 50 steps.",
    "results": [{"headline": "Same accuracy", "detail": "73.5% on 1,000 tasks."},
                {"headline": "Faster retrieval", "detail": "2.4 times faster than 12 baselines."},
                {"headline": "Huge win", "detail": "Up 9.8 points."}],
    "figures": [{"id": "F1", "caption": "How pruning works."}, {"id": "F2", "caption": "Accuracy on 999 tasks."}],
    "thread": ["Agents that forget on purpose: 73.5% accuracy, 2.4x faster retrieval.",
               "We drop the lowest 20% of memories every 50 steps.",
               "It wins on 77 benchmarks.",
               "x" * 300,
               "Paper and code below."],
}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "figures").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "main.pdf").write_bytes(b"%PDF-1.4 paper")
    (repo / "figures" / "overview.pdf").write_bytes(b"%PDF-1.4 fig")
    (repo / "figures" / "curve.png").write_bytes(b"\x89PNG fake")
    (repo / "workflow" / "rules.json").write_text(json.dumps({
        "main_tex": "main.tex", "code": {"repo": "https://github.com/jane/sparse-memory"},
        "notes": {"arxiv": "https://arxiv.org/abs/2601.00001"}}))
    return repo


@pytest.fixture
def tools(monkeypatch):
    seen = {"pdftoppm": [], "head": []}
    monkeypatch.setattr(pg, "which", lambda t: f"/usr/bin/{t}")

    def pdf_to_png(pdf, target):
        seen["pdftoppm"].append(pdf.name)
        out = target.with_suffix(".png")
        out.write_bytes(b"png")
        return out

    def head(url):
        seen["head"].append(url)
        return 404 if "github" in url else 200

    monkeypatch.setattr(pg, "pdf_to_png", pdf_to_png)
    monkeypatch.setattr(pg, "head", head)
    return seen


def run(paper, **kw):
    with script_roles({pg.webwriter: [CONTENT]}) as calls:
        out = json.loads(pg.project_page(str(paper), **kw))
    return out, calls


def test_page_keeps_only_grounded_content(paper, tools):
    out, calls = run(paper)
    req = calls[pg.webwriter][0]
    assert "3 to 5 key results" in req.text and "drop the lowest 20" in req.text and "Accuracy over" in req.text
    assert out["status"] == "PASS_WITH_NOTES" and out["published"] == ""
    assert out["report"].startswith(str(paper / "drafts" / "project_page"))
    record = json.loads(Path(out["json"]).read_text())
    content = record["content"]
    assert content["summary"] == "Agents keep every memory. We drop the rarely read ones. Accuracy holds at 73.5%."
    assert [r["headline"] for r in content["results"]] == ["Same accuracy", "Faster retrieval"]
    assert len(content["thread"]) == 3
    whats = sorted((d["what"], tuple(d.get("numbers", [])), d.get("chars", 0)) for d in record["dropped"])
    assert whats == [("caption", ("999",), 0), ("post", (), 300), ("post", ("77",), 0),
                     ("result", ("9.8",), 0), ("summary", ("37",), 0)]
    # Authors, figures, links.
    assert [a["name"] for a in record["authors"]] == ["Jane Doe", "John Roe"]
    assert record["authors"][0]["affiliation"] == "Example University"
    html = Path(out["page"]).read_text()
    assert "<h1>Sparse Memory for Agents</h1>" in html and "Jane Doe" in html and "Example University" in html
    assert 'href="assets/paper.pdf"' in html and "https://arxiv.org/abs/2601.00001" in html
    assert "<b>TL;DR</b>" in html and "@article{doe" in html and "jane@example.edu" not in html
    assert tools["pdftoppm"] == ["overview.pdf"]
    figs = {f["file"]: f["caption"] for f in record["figures"]}
    assert figs == {"figures/overview.png": "How pruning works.", "figures/curve.png": "Accuracy over 1,000 tasks."}
    assert all((Path(out["page"]).parent / f).is_file() for f in figs)
    assert set(tools["head"]) == {"https://arxiv.org/abs/2601.00001", "https://github.com/jane/sparse-memory"}
    assert record["notes"] == ["https://github.com/jane/sparse-memory: answers 404"]
    thread = Path(out["thread"]).read_text()
    assert "## 1/3" in thread and "77 benchmarks" not in thread
    report = Path(out["report"]).read_text()
    assert "删掉的内容" in report and "300 字符" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["read", "write", "ground", "render", "links", "publish", "report"]
    assert set(trail["roles"]) == {"webwriter"}
    assert not (paper / "docs").exists()


def test_publish_yes_copies_docs_and_anonymous_paper_hides_authors(paper, tools):
    tex = TEX.replace(r"\documentclass{article}", r"\documentclass[anonymous]{article}")
    (paper / "main.tex").write_text(tex)
    out, _ = run(paper, publish="yes")
    assert out["published"] == str(paper / "docs" / "index.html")
    html = (paper / "docs" / "index.html").read_text()
    assert "Anonymous Author(s)" in html and "Jane Doe" not in html
    assert (paper / "docs" / "figures" / "overview.png").is_file() and (paper / "docs" / "assets" / "paper.pdf").is_file()


def test_missing_pdftoppm_and_a_broken_local_link(paper, tools, monkeypatch):
    monkeypatch.setattr(pg, "which", lambda t: None)
    real = pg.render_html

    def broken(*a):
        return real(*a).replace("</main>", '<a href="assets/missing.zip">x</a></main>')

    monkeypatch.setattr(pg, "render_html", broken)
    out, _ = run(paper, publish="yes")
    record = json.loads(Path(out["json"]).read_text())
    assert out["status"] == "FAIL" and out["published"] == "" and not (paper / "docs").exists()
    assert "pdftoppm not found: figure overview.pdf not converted (install poppler)" in record["notes"]
    assert record["errors"] == ["assets/missing.zip: the page links a file that does not exist"]
    assert [f["file"] for f in record["figures"]] == ["figures/curve.png"]


def test_reply_checks():
    with pytest.raises(ValueError, match="not 3 to 5"):
        pg.check_page(dict(CONTENT, results=CONTENT["results"][:2]), ["F1", "F2"])
    with pytest.raises(ValueError, match="unknown or repeated figure"):
        pg.check_page(dict(CONTENT, figures=[{"id": "F9", "caption": "x"}]), ["F1", "F2"])
    with pytest.raises(ValueError, match="posts"):
        pg.check_page(dict(CONTENT, thread=["a"]), ["F1", "F2"])
    schema = pg.page_schema(["F1", "F2"])["properties"]
    assert schema["figures"]["items"]["properties"]["id"]["enum"] == ["F1", "F2"]
    assert pg.parse_authors(r"A. One \\ Uni \And B. Two\thanks{x}") == [
        {"name": "A. One", "affiliation": "Uni"}, {"name": "B. Two", "affiliation": ""}]
