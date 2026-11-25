"""The brief (the paper's kind of work) and the reading of where each task stands."""

import importlib
import json
import subprocess

import pytest

wf = importlib.import_module("auto_research.workflow")
registry = importlib.import_module("auto_research.registry")
brief = importlib.import_module("auto_research.brief")
progress = importlib.import_module("auto_research.progress")

CLAIMS = "# Claims\nApproved: {approved}\n\nC1: Memory edits cut errors.\nRQ: RQ1\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "references.bib").write_text("@article{smith2024,\n title={x}}\n")
    (repo / "0main.tex").write_text("\\section{Introduction}\nAgents forget things over long chats.\n"
                                    "\\section{Method}\nWe edit memory.\n")
    commit(repo)
    return repo


def test_the_brief_keeps_the_kind_and_claim_and_the_rest_of_the_file(paper):
    assert brief.read(paper) == {"kind": "", "claim": ""}
    brief.write(paper, kind="method")
    assert brief.read(paper) == {"kind": "method", "claim": ""}
    note = brief.path(paper).read_text() + "\nThe author's own note.\n"
    brief.path(paper).write_text(note)
    brief.write(paper, claim="Editing   memory\ncuts errors.")
    assert brief.read(paper) == {"kind": "method", "claim": "Editing memory cuts errors."}
    assert "The author's own note." in brief.path(paper).read_text()
    with pytest.raises(ValueError, match="kind must be"):
        brief.write(paper, kind="novel")


def test_the_kind_decides_which_tasks_are_listed():
    listed = lambda kind: {t.name for t in registry.TASKS if registry.serves(t, kind)}
    assert listed("") == set(registry.BY_NAME)
    assert "experiments" in listed("method") and "field_survey" not in listed("method")
    assert {"experiments", "code_release", "idea_generation"}.isdisjoint(listed("survey"))
    assert "field_survey" in listed("survey") and "plan" in listed("survey")
    assert not registry.check_registry()


def test_a_survey_plan_may_cite_works_of_the_bibliography(paper):
    (paper / "workflow" / "plans").mkdir(parents=True)
    (paper / "workflow" / "claims.md").write_text(CLAIMS.format(approved="yes"))
    (paper / "workflow" / "plans" / "introduction.md").write_text(
        "Section: Introduction\nClaims: C1\nEvidence: smith2024\nApproved: yes\n\n## Purpose\nx\n\n"
        "## What the evidence shows\nx\n\n## What it cannot claim\nx\n\n## Storyline\n1. x\n")
    found = lambda: registry.gate_plan(paper, [], {"section": "Introduction"})
    assert any("unknown evidence smith2024" in p for p in found())
    brief.write(paper, kind="survey")
    assert found() == []


def test_a_check_decides_what_it_can(paper):
    rows = {r["name"]: r for r in wf.status(paper)["tasks"]}
    assert progress.checked(rows["idea"])["state"] == "todo" and progress.checked(rows["idea"])["sure"]
    assert progress.checked(rows["plan"])["sure"] is False              # judged per section: the reader decides
    assert progress.checked(rows["paper_review"])["sure"] is False      # a workflow that never ran
    (paper / "workflow").mkdir()
    (paper / "workflow" / "claims.md").write_text(CLAIMS.format(approved="no"))
    idea = progress.checked({r["name"]: r for r in wf.status(paper)["tasks"]}["idea"])
    assert (idea["state"], idea["sure"], idea["approval"]) == ("doing", True, True)


def test_a_reading_keeps_the_checks_above_the_reader(paper, monkeypatch):
    brief.write(paper, kind="survey")
    (paper / "workflow" / "claims.md").write_text(CLAIMS.format(approved="no"))
    seen = {}

    def read(paper_, tasks, trail):
        seen["tasks"] = [t["task"] for t in tasks]
        return [{"task": t["task"], "state": "done", "evidence": "看到了", "missing": "还差"} for t in tasks]
    monkeypatch.setattr(progress, "read_tasks", read)
    data = progress.scan(paper)
    rows = {r["task"]: r for r in data["tasks"]}
    assert "experiments" not in seen["tasks"] and "field_survey" in seen["tasks"]
    assert (rows["idea"]["state"], rows["idea"]["source"], rows["idea"]["approval"]) == ("doing", "check", True)
    assert rows["paper_review"] == {"task": "paper_review", "state": "done", "source": "agent", "evidence": "看到了",
                                    "missing": "", "approval": False}
    assert data["confirmed"] is False and json.loads(progress.path(paper).read_text())["kind"] == "survey"
    progress.set_task(paper, "paper_review", "todo")
    assert progress.confirm(paper)["confirmed"] is True
    assert {r["task"]: r for r in progress.load(paper)["tasks"]}["paper_review"]["source"] == "author"


def test_the_view_shows_later_checks_and_changes_since_the_reading(paper, monkeypatch):
    monkeypatch.setattr(progress, "read_tasks", lambda p, tasks, trail: [
        {"task": t["task"], "state": "todo", "evidence": "没有", "missing": "全部"} for t in tasks])
    progress.scan(paper)
    commit(paper)
    status = wf.status(paper)["tasks"]
    assert progress.view(paper, status)["changed"] == []
    (paper / "0main.tex").write_text("\\section{Introduction}\nMore.\n")
    import os
    import time
    os.utime(paper / "0main.tex", (time.time() + 5, time.time() + 5))
    stamp = "2999-01-01T00:00:00"
    state = paper / "drafts" / "auto_research" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"citation_check": {"task": "citation_check", "status": "PASS", "at": stamp,
                                                    "inputs": {}, "run": "r"}}))
    shown = progress.view(paper, wf.status(paper)["tasks"])
    assert shown["changed"] == ["0main.tex"]
    row = {r["task"]: r for r in shown["tasks"]}["citation_check"]
    assert (row["state"], row["source"]) == ("done", "check")
