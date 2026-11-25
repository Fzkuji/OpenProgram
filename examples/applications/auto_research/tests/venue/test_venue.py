"""The venue tools on tiny paper repositories: LaTeX snippets for each template family, a
code folder with identifying text, and a scripted metadata_writer."""

import importlib
import json
import subprocess
import zipfile

import pytest

from openprogram.programs._authoring.testing import script_roles

venue = importlib.import_module("auto_research.tasks.venue")
facts = importlib.import_module("auto_research.tasks.venue.facts")
metadata = importlib.import_module("auto_research.tasks.venue.metadata")

try:
    import pypdf
except ImportError:
    pypdf = None

ACM = r"""\documentclass[sigconf,review,anonymous]{acmart}
\usepackage{lineno}
\begin{document}
\title{Memory for Agents}
\author{Anonymous}
\linenumbers
\maketitle
\section{Intro}
Our method is 12.5\% better. TODO: add numbers.
\textcolor{red}{check this}
\begin{figure}
\includegraphics{plot}
\caption{A plot.}
\end{figure}
% \begin{figure}\includegraphics{old}\end{figure}
Code: https://anonymous.4open.science/r/xyz
\end{document}
"""
ACM_FINAL = r"""\documentclass[sigconf]{acmart}
\setcopyright{acmlicensed}
\acmConference[KDD '26]{KDD}{2026}{Jeju}
\acmDOI{10.1145/1}
\begin{document}
\title{Memory for Agents}
\author{Jane Doe}
\affiliation{\institution{Example University}}
\begin{CCSXML}
<ccs2012></ccs2012>
\end{CCSXML}
\ccsdesc[500]{Computing methodologies~Machine learning}
\keywords{agents, memory}
\maketitle
\section{Intro}
Our method is 12.5\% better.
\begin{figure}
\includegraphics{plot}
\Description{A bar chart of accuracy.}
\caption{A plot.}
\end{figure}
\begin{acks}
Supported by NSFC grant 62276072 and RGC 11208023.
\end{acks}
\end{document}
"""
NEURIPS = r"""\documentclass{article}
\usepackage{neurips_2025}
\begin{document}
\title{Fast Memory Editing for LLM Agents}
\author{Jane Doe \\ Example University}
\begin{abstract}
We edit agent memory and gain 12.5\% on LongMemEval with 3 benchmarks.
\end{abstract}
\section{Introduction}
Text.
\section{Paper Checklist}
Answers.
\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def make_repo(tmp_path, main, venue_md="", name="paper"):
    repo = tmp_path / name
    (repo / "workflow").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "main.tex").write_text(main)
    if venue_md:
        (repo / "workflow" / "venue.md").write_text(venue_md)
    return repo


def commit(repo, name="Jane Doe", email="jane@uni.edu"):
    git(repo, "add", "-A")
    git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", "x")


def make_pdf(path, pages):
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)


def load(result):
    out = json.loads(result)
    return out, json.loads(open(out["findings"], encoding="utf-8").read())["findings"]


# camera_ready_check ------------------------------------------------------------------

def test_the_review_version_is_not_a_camera_ready(tmp_path):
    repo = make_repo(tmp_path, ACM, "# Venue\nName: KDD 2026\nFunding: NSFC 62276072\nCamera-ready pages: 9\n")
    out, found = load(venue.camera_ready_check(str(repo)))
    assert out["status"] == "FAIL" and out["family"] == "acm"
    kinds = {(f["kind"], f["level"]) for f in found}
    assert ("authors", "error") in kinds                                     # \author{Anonymous}
    assert ("review_mode", "error") in kinds                                 # [review,anonymous]
    assert ("line_numbers", "error") in kinds
    assert ("acknowledgments", "note") in kinds and ("funding", "error") in kinds
    assert {"acm_metadata", "acm_rights"} <= {f["kind"] for f in found}
    alt = [f for f in found if f["kind"] == "alt_text"]
    assert len(alt) == 1 and alt[0]["level"] == "error" and alt[0]["where"] == "main.tex:11"   # commented figure skipped
    drafts = [f["where"] for f in found if f["kind"] == "draft"]
    assert "main.tex:9" in drafts and "main.tex:10" in drafts
    assert any(f["kind"] == "anonymous_left" and f["where"] == "main.tex:16" for f in found)
    assert any(f["kind"] == "pages" and f["level"] == "note" for f in found)              # no PDF
    assert out["report"].startswith(str(repo / "drafts" / "camera_ready_check"))
    assert "不能提交" in open(out["report"], encoding="utf-8").read()


def test_a_finished_acm_camera_ready_passes(tmp_path):
    repo = make_repo(tmp_path, ACM_FINAL, "Name: KDD\nFunding: NSFC 62276072; RGC 11208023\nCamera-ready pages: 9\n")
    if pypdf:
        make_pdf(repo / "main.pdf", 9)
    out, found = load(venue.camera_ready_check(str(repo)))
    assert [f for f in found if f["level"] == "error"] == []
    assert out["status"] == ("PASS" if pypdf else "PASS_WITH_NOTES")
    if pypdf:
        assert out["pages"] == 9 and out["page_limit"] == 9
        make_pdf(repo / "main.pdf", 11)
        out, found = load(venue.camera_ready_check(str(repo)))
        assert out["status"] == "FAIL" and any(f["kind"] == "pages" and f["level"] == "error" for f in found)


def test_grant_numbers_missing_from_the_acks_fail(tmp_path):
    repo = make_repo(tmp_path, ACM_FINAL, "Funding: NSFC 62276072; ERC 999999\n")
    out, found = load(venue.camera_ready_check(str(repo)))
    funding = [f for f in found if f["kind"] == "funding"]
    assert out["status"] == "FAIL" and len(funding) == 1 and "999999" in funding[0]["problem"]


@pytest.mark.parametrize("line,extra,bad", [
    (r"\usepackage{neurips_2025}", "", True),
    (r"\usepackage[final]{neurips_2025}", "", False),
    (r"\usepackage{icml2025}", "", True),
    (r"\usepackage[accepted]{icml2025}", "", False),
    (r"\usepackage{iclr2026_conference,times}", "", True),
    (r"\usepackage{iclr2026_conference,times}", r"\iclrfinalcopy", False),
    (r"\usepackage[review]{acl}", "", True),
    (r"\usepackage[final]{acl}", "", False),
    (r"\usepackage[review]{cvpr}", "", True),
    (r"\usepackage{tmlr}", "", True),
])
def test_each_family_has_its_final_switch(tmp_path, line, extra, bad):
    main = "\\documentclass{article}\n" + line + "\n" + extra + "\n\\begin{document}\n\\author{Jane Doe}\n" \
           "\\section*{Acknowledgments}\nThanks.\n\\begin{figure}\\includegraphics{a}\n% alt: a chart\n\\end{figure}\n" \
           "\\end{document}\n"
    repo = make_repo(tmp_path, main)
    out, found = load(venue.camera_ready_check(str(repo)))
    assert any(f["kind"] == "review_mode" and f["level"] == "error" for f in found) == bad
    assert not any(f["kind"] == "alt_text" for f in found)                  # the alt comment counts outside ACM


def test_a_figure_without_alt_text_is_a_note_outside_acm(tmp_path):
    main = "\\documentclass{article}\n\\usepackage[final]{neurips_2025}\n\\begin{document}\n\\author{Jane Doe}\n" \
           "\\begin{figure}\\includegraphics{a}\\end{figure}\n\\end{document}\n"
    out, found = load(venue.camera_ready_check(str(make_repo(tmp_path, main))))
    assert out["status"] == "PASS_WITH_NOTES"
    assert [f["level"] for f in found if f["kind"] == "alt_text"] == ["note"]


# anonymous_package -------------------------------------------------------------------

def code_repo(tmp_path):
    repo = make_repo(tmp_path, NEURIPS, "Authors: Jane Doe, Bob Lee\nAffiliations: Example University\n")
    (repo / "code").mkdir()
    (repo / "code" / "train.py").write_text("# written by Jane Doe <jane@uni.edu>\n"
                                            "DATA = '/Users/jdoe/data/set'\nprint('ok')\n")
    (repo / "code" / "clean.py").write_text("x = 1\n")
    (repo / "README.md").write_text("Clone https://github.com/janedoe/memory and run.\n")
    (repo / "code" / "nb.ipynb").write_text(json.dumps({
        "cells": [{"cell_type": "code", "source": ["print(1)"], "execution_count": 3, "metadata": {"tags": ["x"]},
                   "outputs": [{"output_type": "stream", "text": ["/home/jdoe/run\n"]}]}],
        "metadata": {"kernelspec": {"name": "jdoe-env", "display_name": "Python (jdoe)", "language": "python"},
                     "language_info": {"name": "python", "version": "3.11"}},
        "nbformat": 4, "nbformat_minor": 5}))
    (repo / ".DS_Store").write_bytes(b"\0\0")
    (repo / "deploy.pem").write_text("KEY=secret\n")   # .env itself is unreadable in the check sandbox
    (repo / "code" / "__pycache__").mkdir()
    (repo / "code" / "__pycache__" / "train.cpython-311.pyc").write_bytes(b"\0")
    (repo / "refs.bib").write_text("@x{a}\n")
    (repo / "drafts").mkdir()
    (repo / "drafts" / "n.txt").write_text("draft")
    commit(repo)
    git(repo, "remote", "add", "origin", "git@github.com:janedoe/paper.git")
    return repo


def test_identifying_text_is_listed_and_blocks_the_bundle(tmp_path):
    repo = code_repo(tmp_path)
    out = json.loads(venue.anonymous_package(str(repo)))
    assert out["status"] == "FAIL" and out["zip"] == ""
    assert out["blocked"] == ["README.md", "code/train.py"]
    manifest = json.load(open(out["manifest"], encoding="utf-8"))
    hits = {(f["file"], f["line"], f["kind"]) for f in manifest["findings"]}
    assert {("code/train.py", 1, "name"), ("code/train.py", 1, "author_email"),
            ("code/train.py", 2, "home_path"), ("README.md", 1, "code_host")} <= hits
    assert "janedoe" in manifest["terms"]["accounts"] and "Example University" in manifest["terms"]["affiliations"]
    assert {".DS_Store", "deploy.pem", "code/__pycache__/train.cpython-311.pyc"} <= set(manifest["removed"])
    included = set(manifest["included"])
    assert "code/clean.py" in included and "main.tex" not in included and "refs.bib" not in included
    assert not any(p.startswith(("drafts/", "workflow/")) for p in included)
    assert manifest["notebooks_cleaned"] == ["code/nb.ipynb"]


def test_replace_scrubs_and_bundles(tmp_path):
    repo = code_repo(tmp_path)
    out = json.loads(venue.anonymous_package(str(repo), fix="replace"))
    assert out["status"] == "PASS_WITH_NOTES" and out["zip"].endswith("supplementary.zip")
    with zipfile.ZipFile(out["zip"]) as z:
        names = set(z.namelist())
        train = z.read("code/train.py").decode()
        readme = z.read("README.md").decode()
        nb = json.loads(z.read("code/nb.ipynb"))
    assert ".git/HEAD" not in names and "deploy.pem" not in names and ".DS_Store" not in names
    assert "Jane Doe" not in train and "jane@uni.edu" not in train and "/Users/jdoe" not in train
    assert "Anonymous" in train and "/Users/anonymous/" in train
    assert "janedoe" not in readme
    cell = nb["cells"][0]
    assert cell["outputs"] == [] and cell["execution_count"] is None and cell["metadata"] == {}
    assert "jdoe" not in json.dumps(nb)
    assert (repo / "code" / "train.py").read_text().startswith("# written by Jane Doe")     # the repo is untouched


def test_include_globs_pick_the_files(tmp_path):
    repo = code_repo(tmp_path)
    out = json.loads(venue.anonymous_package(str(repo), include="code/clean.py, code/*.ipynb"))
    manifest = json.load(open(out["manifest"], encoding="utf-8"))
    assert manifest["included"] == ["code/clean.py", "code/nb.ipynb"] and out["status"] == "PASS"
    with pytest.raises(ValueError):
        venue.anonymous_package(str(repo), fix="maybe")


# venue_move --------------------------------------------------------------------------

def test_moving_from_neurips_to_arr_plans_without_editing(tmp_path):
    repo = make_repo(tmp_path, NEURIPS, "# Venue\nName: NeurIPS 2025\n9 pages, double-blind review.\nFunding: NSFC 1\n")
    if pypdf:
        make_pdf(repo / "main.pdf", 11)
    commit(repo)
    before = (repo / "main.tex").read_text()
    out = json.loads(venue.venue_move(str(repo), "emnlp", write_venue="yes"))
    plan = json.load(open(out["plan"], encoding="utf-8"))
    assert plan["from"] == "neurips" and plan["to"] == "arr"
    assert r"\usepackage{neurips_2025}" in plan["preamble"]["remove"]
    assert r"\usepackage[review]{acl}" in plan["preamble"]["add"]
    assert plan["bib_style"] == {"current": "plainnat", "target": "acl_natbib", "change": True}
    actions = {(s["action"], s["section"].split(" (")[0]) for s in plan["sections"]}
    assert ("add", "Limitations") in actions
    assert ("remove", "NeurIPS Paper Checklist") in actions
    assert plan["anonymity"] == {"current": "anonymous", "target": "anonymous", "todo": []}   # neurips without final
    if pypdf:
        assert plan["pages"]["total"] == 11 and plan["pages"]["change"] == 8 - 11
    assert (repo / "main.tex").read_text() == before
    new = (repo / "workflow" / "venue.md").read_text()
    assert "ACL Rolling Review" in new and "page limit: 8" in new and "Funding: NSFC 1" in new
    assert (repo / "workflow" / "venue.neurips.md").read_text().startswith("# Venue\nName: NeurIPS")
    common = importlib.import_module("auto_research.tasks.submission.common")
    assert common.page_limit(repo) == 8 and common.double_blind(repo)
    assert "下载" in open(out["report"], encoding="utf-8").read()


def test_unknown_target_and_every_family_is_complete(tmp_path):
    repo = make_repo(tmp_path, NEURIPS)
    with pytest.raises(ValueError, match="unknown venue"):
        venue.venue_move(str(repo), "nature")
    for f in facts.FAMILIES.values():
        assert f.template.startswith("https://") and f.preamble and f.bib_style
    assert facts.family("KDD").key == "acm" and facts.family("naacl").key == "arr"
    out = json.loads(venue.venue_move(str(repo), "jmlr"))
    assert out["to"] == "jmlr" and not (repo / "workflow" / "venue.neurips.md").exists()
    plan = json.load(open(out["plan"], encoding="utf-8"))
    assert plan["anonymity"]["target"] == "named" and any("作者" in t for t in plan["anonymity"]["todo"])


# submission_metadata -----------------------------------------------------------------

def reply(**kw):
    base = {"tldr": "We edit LLM agent memory and gain 12.5% on LongMemEval.",
            "keywords": ["agent memory", "memory editing", "LLM agents", "long-term memory", "benchmarks"],
            "primary_area": "Deep learning",
            "lay_summary": "Chat assistants forget less when they can rewrite their own notes."}
    base.update(kw)
    return base


def test_metadata_is_drafted_and_checked(tmp_path):
    repo = make_repo(tmp_path, NEURIPS, "Name: NeurIPS 2026\n")
    with script_roles({metadata.metadata_writer: [reply()]}) as calls:
        out = json.loads(venue.submission_metadata(str(repo)))
    request = calls[metadata.metadata_writer][0]
    assert "never add results" in request.text and "at most 250 characters" in request.text
    assert "Fast Memory Editing for LLM Agents" in request.text and "Deep learning" in request.text
    assert out["status"] == "PASS" and out["venue"] == "neurips"
    text = (repo / "workflow" / "submission_metadata.md").read_text()
    assert "12.5% on LongMemEval" in text and "agent memory, memory editing" in text
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["read", "draft", "check"] and set(run["roles"]) == {"metadata_writer"}


def test_metadata_checks_numbers_area_and_lengths():
    source = NEURIPS
    areas = facts.areas()["neurips"]
    assert metadata.check(reply(), source, areas) == []
    problems = metadata.check(reply(tldr="We gain 40% on 7 tasks.", keywords=["a", "b"], primary_area="Vision",
                                    lay_summary="One. Two."), source, areas)
    assert any("40" in p and "7" in p for p in problems)
    assert any("关键词" in p for p in problems) and any("主领域" in p for p in problems)
    assert any("通俗摘要" in p for p in problems)
    assert metadata.check(reply(primary_area="graph mining"), source, []) == []
    assert set(facts.areas()) == {"iclr", "neurips", "icml", "arr"}
