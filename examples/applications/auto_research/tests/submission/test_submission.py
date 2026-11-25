"""The submission tools on a tiny paper repository: a fake .log, a PDF made with pypdf
(its asserts skip without pypdf) and a venue asking for 8 pages and double-blind review."""

import importlib
import json
import subprocess
import zipfile

import pytest

sub = importlib.import_module("auto_research.tasks.submission")
check = importlib.import_module("auto_research.tasks.submission.check")
common = importlib.import_module("auto_research.tasks.submission.common")

try:
    import pypdf
except ImportError:
    pypdf = None

MAIN = r"""\documentclass{article}
\usepackage{mystyle}
\usepackage{amsmath}
\graphicspath{{figures/}}
\author{Jane Doe}
\begin{document}
\input{sections/intro}
% \input{sections/old}
\includegraphics[width=\linewidth]{plot}
50\% of runs % a comment to drop
\url{https://example.com/a%20b}
\begin{verbatim}
% kept in verbatim
\end{verbatim}
\begin{comment}
secret draft text
\end{comment}
\section*{Acknowledgments}
We thank our funders.
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
INTRO = "As shown in our previous work [3], the code is at https://github.com/jane/repo.\n"
LOG = r"""This is pdfTeX, Version 3.141592653
(./main.tex
LaTeX2e <2024-11-01>
(./sections/intro.tex
! Undefined control sequence.
l.12 \foo
         {bar}
)
LaTeX Warning: Citation `smith2024' on page 1 undefined on input line 20.

LaTeX Warning: Reference `fig:x' on page 2 undefined on input line 30.

LaTeX Warning: Label `sec:a' multiply defined.

Overfull \hbox (15.2pt too wide) in paragraph at lines 40--42
Overfull \hbox (2.0pt too wide) in paragraph at lines 50--52
)
"""
VENUE = "# Venue\n\n## Venue\nWorkshop, 8 pages, double-blind review.\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo, message="x"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)


def make_pdf(path, pages, author=""):
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if author:
        writer.add_metadata({"/Author": author})
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "figures").mkdir()
    (repo / "workflow").mkdir()
    (repo / "drafts").mkdir()
    git(repo, "init", "-q")
    (repo / "main.tex").write_text(MAIN)
    (repo / "sections" / "intro.tex").write_text(INTRO)
    (repo / "sections" / "old.tex").write_text("old\n")
    (repo / "mystyle.sty").write_text("% style\n")
    (repo / "figures" / "plot.png").write_bytes(b"png")
    (repo / "figures" / "unused.png").write_bytes(b"unused")
    (repo / "refs.bib").write_text("@article{smith2024, title={X}}\n")
    (repo / "main.bbl").write_text("\\begin{thebibliography}{1}\\end{thebibliography}\n")
    (repo / "main.log").write_text(LOG)
    (repo / "workflow" / "venue.md").write_text(VENUE)
    (repo / "drafts" / "notes.txt").write_text("draft")
    if pypdf:
        make_pdf(repo / "main.pdf", 10, author="Jane Doe")
    commit(repo)
    return repo


def run_check(paper):
    result = json.loads(sub.submission_check(str(paper)))
    data = json.loads(open(result["findings"], encoding="utf-8").read())
    return result, data["findings"]


def test_a_path_that_is_not_a_git_repository_is_refused(tmp_path):
    for tool in (sub.submission_check, sub.arxiv_package, sub.ai_disclosure):
        with pytest.raises(ValueError):
            tool(str(tmp_path))


def test_the_log_gives_errors_with_file_and_line_and_undefined_names(paper):
    result, findings = run_check(paper)
    assert result["status"] == "FAIL" and result["double_blind"] and result["page_limit"] == 8
    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    assert by_kind["latex_error"][0]["where"] == "sections/intro.tex:12"
    assert "Undefined control sequence" in by_kind["latex_error"][0]["problem"]
    assert "smith2024" in by_kind["undefined_citation"][0]["problem"]
    assert "fig:x" in by_kind["undefined_reference"][0]["problem"]
    assert by_kind["duplicate_label"][0]["level"] == "note"
    assert [f["where"] for f in by_kind["overfull"]] == ["sections/intro.tex:40"]
    assert all(f["problem"] and f["fix"] for f in findings)
    run = json.loads(open(result["run"], encoding="utf-8").read())
    assert run["workflow"] == "submission_check" and [s["stage"] for s in run["stages"]] == ["log", "pdf", "anonymity"]
    assert "投稿前检查" in open(result["report"], encoding="utf-8").read()


def test_a_double_blind_venue_flags_what_names_the_authors(paper):
    _, findings = run_check(paper)
    anon = " ".join(f["problem"] for f in findings if f["kind"] == "anonymity" and f["level"] == "error")
    assert "\\author" in anon and "致谢" in anon and "github.com/jane/repo" in anon and "our previous work" in anon
    if pypdf:
        assert "Jane Doe" in anon


@pytest.mark.skipif(pypdf is None, reason="pypdf is not installed")
def test_pages_over_the_venue_limit_fail(paper):
    result, findings = run_check(paper)
    assert result["pages"] == 10
    pages = [f for f in findings if f["kind"] == "pages"]
    assert pages and pages[0]["level"] == "error" and "8" in pages[0]["problem"]


@pytest.mark.skipif(pypdf is None, reason="pypdf is not installed")
def test_a_clean_anonymous_paper_passes(paper):
    (paper / "main.tex").write_text("\\documentclass{article}\n\\author{Anonymous}\n\\begin{document}\nHi.\n\\end{document}\n")
    (paper / "main.log").write_text("This is pdfTeX\n(./main.tex)\nOutput written on main.pdf (3 pages).\n")
    make_pdf(paper / "main.pdf", 3)
    result, findings = run_check(paper)
    assert [f["kind"] for f in findings if f["level"] == "error"] == []
    assert result["status"] in ("PASS", "PASS_WITH_NOTES")
    assert result["pages"] == 3


def test_file_line_error_logs_and_the_page_limit_wording():
    found = check.log_findings("./sec/a.tex:7: Missing $ inserted.\n", "main.tex")
    assert found[0]["where"] == "sec/a.tex:7" and "$" in found[0]["fix"]


def test_page_limit_patterns(paper):
    (paper / "workflow" / "venue.md").write_text("Page limit: 9 (references excluded).\n")
    assert common.page_limit(paper) == 9
    (paper / "workflow" / "venue.md").write_text("No limit given.\n")
    assert common.page_limit(paper) is None


def test_comments_are_stripped_safely():
    text = "a % c\n% whole\n50\\% b\n\\url{x%20y} % z\n\\begin{verbatim}\n% v\n\\end{verbatim}\n" \
           "\\begin{comment}\nhidden\n\\end{comment}\nend\n"
    out = common.strip_comments(text)
    assert out == "a %\n50\\% b\n\\url{x%20y} %\n\\begin{verbatim}\n% v\n\\end{verbatim}\nend\n"


def test_the_arxiv_bundle_holds_only_used_files_without_comments(paper):
    result = json.loads(sub.arxiv_package(str(paper)))
    assert result["status"] == "PASS", result
    with zipfile.ZipFile(result["zip"]) as z:
        names = set(z.namelist())
        main = z.read("main.tex").decode()
    assert names == {"main.tex", "sections/intro.tex", "mystyle.sty", "figures/plot.png", "main.bbl"}
    assert "a comment to drop" not in main and "sections/old" not in main and "secret" not in main
    assert "50\\%" in main and "a%20b" in main and "% kept in verbatim" in main
    manifest = json.loads(open(result["manifest"], encoding="utf-8").read())
    dropped = {f["path"] for f in manifest["dropped"]}
    assert {"figures/unused.png", "refs.bib", "sections/old.tex", "drafts/", "workflow/", ".git/"} <= dropped
    assert all(f["bytes"] >= 0 for f in manifest["included"])
    assert json.loads(open(result["run"], encoding="utf-8").read())["workflow"] == "arxiv_package"


def test_the_arxiv_bundle_fails_without_the_bbl_or_a_figure(paper):
    (paper / "main.bbl").unlink()
    (paper / "figures" / "plot.png").unlink()
    result = json.loads(sub.arxiv_package(str(paper)))
    assert result["status"] == "FAIL" and result["zip"] == ""
    assert "main.bbl" in result["problems"][0]
    assert any("plot" in p for p in result["problems"][1:])


def test_the_disclosure_drafts_each_stage_from_runs_and_trailers(paper):
    for workflow, folder, calls, cost in (("paper_review", "paper_review/1", 3, 0.5),
                                          ("citation_check", "citation_check/1", 1, 0.1),
                                          ("idea_generation", "idea_generation/1", 2, 0.2)):
        run_dir = paper / "drafts" / folder
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "workflow": workflow, "finished": "2026-11-01T10:00:00", "roles": {"reviewer": {}},
            "stages": [{"stage": "s", "kind": "model"}], "usage": {"calls": calls, "cost": cost}}))
    (paper / "sections" / "intro.tex").write_text(INTRO + "More.\n")
    (paper / "plot.py").write_text("print(1)\n")
    commit(paper, "Edit intro\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>")
    (paper / "sections" / "intro.tex").write_text(INTRO + "Human edit.\n")
    commit(paper, "Human edit\n\nCo-Authored-By: Bob <bob@example.com>")
    before = (paper / "main.tex").read_text()
    result = json.loads(sub.ai_disclosure(str(paper)))
    assert result["status"] == "PASS"
    assert result["model_calls"] == 6 and result["cost"] == 0.8 and result["ai_commits"] == 1
    assert {"ideation", "writing", "references", "code"} <= set(result["stages"])
    text = open(result["report"], encoding="utf-8").read()
    assert "**Ideation and literature.**" in text and "**Writing and review.**" in text
    assert "**References.**" in text and "Claude Opus" in text and "verified the results" in text
    assert "中文摘要" in text and "| 写作与审阅 |" in text
    assert (paper / "main.tex").read_text() == before
