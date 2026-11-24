"""The whole entry with LaTeX tools replaced and a scripted reviewer."""

import importlib
import json
import shutil

import pytest

from openprogram.programs._authoring.testing import script_roles

wf = importlib.import_module("auto_research.tasks.revision.workflow")
build = importlib.import_module("auto_research.tasks.revision.steps.prepare.build")
pdftext = importlib.import_module("auto_research.tasks.revision.steps.prepare.pdftext")
venue = importlib.import_module("auto_research.tasks.revision.rules.venue")

HEAD = "\n".join(venue.REQUIRED_SOURCE) + r"""
\newcommand{\zxy}[1]{#1}
\newcommand{\zc}[0]{}
"""
BASE = HEAD + r"""
\begin{document}
Code is at \url{https://github.com/x/y} for everyone to use.

\section{Results}
\subsection{Agent Behavior Analysis (RQ3)}
The original paragraph explains the results of the agent in plain words here.

Ratings rise across development as shown in Figure~\ref{fig:a} for each task.
\begin{figure}[!t]
\includegraphics{figures/a}
\caption{Ratings.}
\label{fig:a}
\end{figure}
\bibliography{references}
\end{document}
"""


@pytest.fixture
def paper(tmp_path, monkeypatch):
    base = tmp_path / "base_tree"
    base.mkdir()
    (base / "0main.tex").write_text(BASE)
    (base / "references.bib").write_text("@misc{x}")
    (base / "figures").mkdir()
    (base / "figures" / "a.pdf").write_bytes(b"%PDF")
    repo = tmp_path / "paper"
    shutil.copytree(base, repo)
    (repo / ".git").mkdir()

    monkeypatch.setattr(build, "export_baseline", lambda paper, ref, target: shutil.copytree(base, target))
    monkeypatch.setattr(build, "compile_pdf", fake_compile)
    monkeypatch.setattr(build, "render_diff", fake_diff)
    monkeypatch.setattr(pdftext, "page_texts", lambda pdf: ["a", "b", "c", "References"])
    monkeypatch.setattr(pdftext, "bbox", lambda *args: [])
    monkeypatch.setattr(pdftext, "type3_fonts", lambda pdf: [])
    monkeypatch.setattr(venue, "REQUIRED_AUTHORS", ())
    return repo


def fake_diff(base, draft, main, target):
    target.write_bytes(b"%PDF")
    return True, ""


def fake_compile(root, main):
    (root / "0main.pdf").write_bytes(b"%PDF")
    return True, ""


def run(repo, replies, **kwargs):
    with script_roles({wf.reviewer: replies}) as calls:
        result = json.loads(wf.paper_revision_check(str(repo), **kwargs))
    return result, calls


def test_unchanged_paper_passes_without_a_model_call(paper):
    result, calls = run(paper, [])
    assert result["status"] == "PASS"
    assert calls[wf.reviewer] == []
    report = json.loads((paper / "drafts" / "checks").glob("*/report.json").__next__().read_text())
    assert report["added_paragraphs"] == []


def test_rule_breaking_draft_fails_and_reviewer_sees_only_added_text(paper, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(paper, draft, ignore=shutil.ignore_patterns(".git", "drafts"))
    text = BASE.replace(
        "\\begin{figure}[!t]", "Table~\\ref{tab:z} reports whether gains, ties, losses, and draws matter.\n\n\\begin{figure}[b]")
    (draft / "0main.tex").write_text(text)
    reply = {"issues": [{"paragraph": 0, "rule": 1, "quote": "Table~\\ref{tab:z} reports",
                         "problem": "Opens with a table.", "fix": "State the point first."}]}
    result, calls = run(paper, [reply], draft_dir=str(draft))

    assert result["status"] == "FAIL"
    request = calls[wf.reviewer][0].text
    assert "reports whether gains" in request
    assert "The original paragraph explains" not in request
    assert "Open each section or paragraph with the point" in request
    report = (paper / "drafts" / "checks").glob("*/report.md").__next__().read_text()
    assert "placement" in report and "opening" in report and "rule 1" in report
    assert "[Agent Behavior Analysis (RQ3)]" in report
    assert report.index("### 1. Process") < report.index("### 4. Wording") < report.index("### 8. Layout")
    record = json.loads(open(result["run"]).read())
    assert list(record["roles"]) == ["reviewer"] and record["args"]["scope"] == "changes"
    assert [s["stage"] for s in record["stages"]] == ["prepare", "checks", "review batch 1", "report"]
    batch = record["stages"][2]
    assert batch["role"] == "reviewer" and "reports whether gains" in json.dumps(batch["context"])
    assert batch["output"] == reply and "Report only real violations" in batch["instructions"]


def test_removed_figure_needs_approval(paper, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(paper, draft, ignore=shutil.ignore_patterns(".git", "drafts"))
    start = BASE.index("\\begin{figure}")
    end = BASE.index("\\end{figure}") + len("\\end{figure}")
    (draft / "0main.tex").write_text(BASE[:start] + BASE[end:])
    result, _ = run(paper, [], draft_dir=str(draft))
    assert result["status"] == "FAIL"
    result, _ = run(paper, [], draft_dir=str(draft), approved_removals="fig:a")
    assert result["status"] == "PASS"


def test_reviewer_citing_unknown_paragraph_is_rejected(paper, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(paper, draft, ignore=shutil.ignore_patterns(".git", "drafts"))
    (draft / "0main.tex").write_text(BASE.replace(
        "\\begin{figure}", "A new paragraph says the ratings rise in every task.\n\n\\begin{figure}"))
    bad = {"issues": [{"paragraph": 5, "rule": 1, "quote": "x", "problem": "y", "fix": "z"}]}
    with pytest.raises(ValueError, match="unknown paragraph"):
        run(paper, [bad], draft_dir=str(draft))


def test_missing_repository_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.paper_revision_check(str(tmp_path))


def test_report_lists_every_guide_item(paper):
    checklist = importlib.import_module("auto_research.tasks.revision.rules.checklist")
    result, _ = run(paper, [])
    assert result["status"] == "PASS"
    assert any("plagiarism" in item for item in result["manual_items"])
    report = json.loads((paper / "drafts" / "checks").glob("*/report.json").__next__().read_text())
    assert len(report["checklist"]) == len(checklist.CHECKLIST)
    assert {row["status"] for row in report["checklist"]} <= {"PASS", "TODO", "N/A"}


def test_paper_scope_reviews_every_paragraph(paper):
    result, calls = run(paper, [{"issues": []}], scope="paper")
    request = calls[wf.reviewer][0].text
    assert "The original paragraph explains" in request and "Ratings rise across" in request


def test_unknown_scope_is_rejected(paper):
    with pytest.raises(ValueError, match="scope"):
        wf.paper_revision_check(str(paper), scope="all")


def test_worktree_baseline_compares_against_uncommitted_paper(paper, tmp_path):
    (paper / "0main.tex").write_text(BASE.replace("in plain words here.", "in plain words here, edited."))
    draft = tmp_path / "draft"
    shutil.copytree(paper, draft, ignore=shutil.ignore_patterns(".git", "drafts"))
    result, calls = run(paper, [], draft_dir=str(draft), baseline="WORKTREE")
    assert result["status"] == "PASS" and calls[wf.reviewer] == []


CARD = """Section: Agent Behavior Analysis (RQ3)
Claims: C3
Evidence: E1
Approved: {approved}

## Purpose
Show that recorded actions follow the workflow conditions.

## What the evidence shows
All eligible states take the coded branch.

## What it cannot claim
No grouping of actions into kinds.

## Storyline
1. Actions follow the conditions.
"""


def test_reviewer_reads_paragraphs_against_approved_plan_cards(paper, tmp_path):
    cards = tmp_path / "plans"
    cards.mkdir()
    (cards / "rq3.md").write_text(CARD.format(approved="yes"))
    result, calls = run(paper, [{"issues": []}], scope="paper", plan_dir=str(cards))
    request = calls[wf.reviewer][0].text
    assert "No grouping of actions into kinds." in request
    assert "Nothing the evidence does not contain" in request


def test_unapproved_plan_card_is_not_sent(paper, tmp_path):
    cards = tmp_path / "plans"
    cards.mkdir()
    (cards / "rq3.md").write_text(CARD.format(approved="no"))
    result, calls = run(paper, [{"issues": []}], scope="paper", plan_dir=str(cards))
    assert "No grouping of actions into kinds." not in calls[wf.reviewer][0].text


def test_missing_plan_folder_is_rejected(paper, tmp_path):
    with pytest.raises(ValueError, match="not a folder"):
        wf.paper_revision_check(str(paper), plan_dir=str(tmp_path / "none"))


def test_phases_limit_the_checks_the_reviewer_and_the_report(paper, tmp_path):
    draft = tmp_path / "draft"
    shutil.copytree(paper, draft, ignore=shutil.ignore_patterns(".git", "drafts"))
    (draft / "0main.tex").write_text(BASE.replace(
        "\\begin{figure}[!t]", "Table~\\ref{tab:z} reports whether gains, ties, losses, and draws matter.\n\n\\begin{figure}[b]"))
    result, calls = run(paper, [], draft_dir=str(draft), phases="6")
    assert calls[wf.reviewer] == []                      # phase 6 has no reviewer rules
    report = json.loads((paper / "drafts" / "checks").glob("*/report.json").__next__().read_text())
    assert {f["check"] for f in report["findings"]} == {"placement", "deleted"}   # both in phase 6
    assert report["phases"] == ["6. Figures and tables"] and result["status"] == "FAIL"

    result, calls = run(paper, [{"issues": []}], draft_dir=str(draft), phases="4")
    request = calls[wf.reviewer][0].text
    assert "Open each section or paragraph with the point" in request          # rule 1 is in phase 4
    assert "Introduction: background and significance" not in request         # rule 14 is not
    assert "The paper states first place" not in request


def test_sections_limit_the_paragraphs_under_review(paper, tmp_path):
    text = BASE.replace("\\bibliography", "\\section{Discussion}\nA discussion paragraph about other things here.\n\n\\bibliography")
    (paper / "0main.tex").write_text(text)
    result, calls = run(paper, [{"issues": []}], scope="paper", sections="Agent Behavior")
    request = calls[wf.reviewer][0].text
    assert "The original paragraph explains" in request and "A discussion paragraph" not in request
    with pytest.raises(ValueError, match="no section titled Methods"):
        run(paper, [], scope="paper", sections="Methods")


def test_unknown_phase_is_rejected(paper):
    with pytest.raises(ValueError, match="unknown phase 9"):
        wf.paper_revision_check(str(paper), phases="9")
