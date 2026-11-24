"""The app backend: the project it reads, the files it shows and the images it hands the page, in plain code."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("auto_research_app_backend",
                                              Path(__file__).resolve().parents[1] / "app" / "backend.py")
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow" / "plans").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text("\\section{Intro}\nOld sentence here.\n\\input{sec}\n")
    (repo / "sec.tex").write_text("\\section{Results}\nWe win.\n")
    (repo / "refs.bib").write_text("@article{a,\n title={x}}\n")
    (repo / "workflow" / "plans" / "intro.md").write_text("Section: Intro\nApproved: no\n")
    (repo / "main.log").write_text("build noise")
    (repo / "drafts").mkdir()
    return repo


class Context:
    def __init__(self, path):
        self.project_path = str(path)


def test_reading_stays_inside_the_project(paper):
    assert backend.read_text({"path": "sec.tex"}, Context(paper))["text"].startswith("\\section{Results}")
    with pytest.raises(ValueError, match="outside"):
        backend.read_text({"path": "/etc/hosts"}, Context(paper))


def test_files_are_shown_by_kind(paper):
    assert [backend.kind_of(Path(n)) for n in ("a.PNG", "p.pdf", "e.csv", "c.md", "s.html", "m.tex", "x.bin")] == \
        ["image", "pdf", "table", "markdown", "html", "text", "other"]


def test_a_folder_lists_its_files_newest_first_without_build_products(paper):
    figures = paper / "workflow" / "figures"
    figures.mkdir()
    (figures / "old.png").write_bytes(b"x")
    (figures / "new.png").write_bytes(b"y")
    (figures / "plot.log").write_text("noise")
    import os
    os.utime(figures / "old.png", (1, 1))
    assert [p.name for p in backend.files_under(figures, 10)] == ["new.png", "old.png"]
    assert backend.files_under(paper / "missing", 10) == []
    assert backend.files_under(paper / "sec.tex", 10) == [paper / "sec.tex"]


def test_only_images_are_read_as_bytes(paper):
    (paper / "fig.png").write_bytes(b"\x89PNG")
    got = backend.read_file({"path": "fig.png"}, Context(paper))
    assert got["mime"] == "image/png" and got["data"] == "iVBORw=="
    with pytest.raises(ValueError, match="not an image"):
        backend.read_file({"path": "sec.tex"}, Context(paper))
    with pytest.raises(ValueError, match="outside"):
        backend.read_file({"path": "/etc/hosts"}, Context(paper))


def test_the_page_can_pick_a_project_other_than_the_bound_one(paper, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ValueError, match="not a Git repository"):
        backend.project(Context(home))
    assert backend.project(Context(home), {"project": str(paper)}) == paper.resolve()
    with pytest.raises(ValueError, match="choose a project"):
        backend.project(Context(""))


def test_the_folder_browser_lists_subfolders_and_marks_repositories(paper, tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / ".hidden").mkdir()
    listed = backend.browse({"path": str(tmp_path)}, Context(""))
    names = {e["name"]: e["is_repo"] for e in listed["entries"]}
    assert names[paper.name] is True and names["plain"] is False and ".hidden" not in names
    assert listed["parent"] == str(tmp_path.resolve().parent)
    with pytest.raises(ValueError, match="not a folder"):
        backend.browse({"path": str(tmp_path / "missing")}, Context(""))
