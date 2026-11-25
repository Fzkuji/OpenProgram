"""What the author does with a run's results: edits, answers, placed files, the list of
uncommitted changes, their diff, undo and commit. A temporary git project, no model."""

import json
import subprocess
from pathlib import Path

import pytest

from auto_research import results


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "main.tex").write_text("\\section{Intro}\nWe show a big win.\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "publications.html").write_text("<p>old</p>")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def make_run(paper, task="publication_list", name="20261125-100000", status="FAIL", at="2026-11-25T10:00:00"):
    folder = paper / "drafts" / task / name
    folder.mkdir(parents=True)
    (folder / "report.md").write_text(f"# 报告\n\n结论：{status}　两处要改。\n", encoding="utf-8")
    (folder / "edits.json").write_text(json.dumps({"root": str(paper), "edits": [
        {"id": "E1", "file": "main.tex", "section": "Intro", "kind": "replace", "problem": "Overclaims.",
         "quote": "a big win", "replacement": "a small gain"},
        {"id": "E2", "file": "notes/new.md", "kind": "create", "problem": "Missing.", "quote": "", "replacement": "hi\n"},
        {"id": "E3", "file": "main.tex", "kind": "replace", "quote": "absent", "replacement": "x"},
        {"id": "E4", "file": "main.tex", "kind": "replace", "quote": "same", "replacement": "same"},
    ]}), encoding="utf-8")
    (folder / "questions.json").write_text(json.dumps({"questions": [
        {"id": "doi:10.1/x", "title": "Cardiac", "detail": "2019", "options": [{"id": "include", "label": "收录"},
                                                                              {"id": "exclude", "label": "排除"}]}]}))
    (folder / "publications.html").write_text("<p>new</p>")
    (folder / "publications.json").write_text("{}")
    (folder / "run.json").write_text("{}")
    state = paper / "drafts" / "auto_research" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(state.read_text()) if state.exists() else {}
    data[f"{task}:{name}"] = {"task": task, "status": status, "at": at, "report": f"drafts/{task}/{name}/report.md"}
    state.write_text(json.dumps(data))
    return folder.relative_to(paper).as_posix()


def test_a_run_lists_what_is_left_for_the_author(paper):
    run = make_run(paper)
    r = results.result(paper, "publication_list")
    assert r["run"] == run and r["status"] == "FAIL" and r["verdict"] == "两处要改。"
    assert [(e["id"], e["state"]) for e in r["edits"]] == [("E1", "pending"), ("E2", "pending"), ("E3", "stale")]
    assert [f["name"] for f in r["files"]] == ["publications.html"]
    assert r["files"][0]["place"] == "docs/publications.html" and not r["files"][0]["placed"]
    assert r["todo"] == {"edits": 2, "questions": 1, "places": 1, "fix": 1} and r["pending"]
    assert [p["task"] for p in results.pending(paper)] == ["publication_list"]


def test_the_verdict_drops_the_status_word_however_it_is_written(tmp_path):
    report = tmp_path / "report.md"
    for line, status in (("结论：**FAIL**，有设置和代码不一致。", "FAIL"), ("结论：PASS_WITH_NOTES　有设置和代码不一致。", "PASS_WITH_NOTES")):
        report.write_text(f"# x\n\n{line}\n", encoding="utf-8")
        assert results.verdict(report) == "有设置和代码不一致。" and results.status_of(report) == status


def test_applied_and_rejected_edits_are_remembered_and_logged(paper):
    run = make_run(paper)
    out = results.apply(paper, "publication_list", run, ["E1", "E2", "E3"])
    assert out == {"applied": ["E1", "E2"], "failed": ["E3"]}
    assert "a small gain" in (paper / "main.tex").read_text() and (paper / "notes" / "new.md").read_text() == "hi\n"
    results.reject(paper, run, ["E3"])
    states = {e["id"]: e["state"] for e in results.result(paper, "publication_list")["edits"]}
    assert states == {"E1": "applied", "E2": "applied", "E3": "rejected"}
    changed = {c["path"]: c for c in results.changes(paper)}
    assert set(changed) == {"main.tex", "notes/new.md"}
    assert changed["notes/new.md"]["state"] == "new" and changed["main.tex"]["state"] == "modified"
    assert changed["main.tex"]["by"] and changed["main.tex"]["added"] == 1 == changed["main.tex"]["removed"]


def test_placing_a_file_copies_it_and_refuses_the_author_s_own_changes(paper):
    run = make_run(paper)
    assert results.place(paper, "publication_list", run, "publications.html") == {"placed": "docs/publications.html"}
    assert (paper / "docs" / "publications.html").read_text() == "<p>new</p>"
    assert results.result(paper, "publication_list")["files"][0]["placed"]
    git(paper, "checkout", "--", "docs/publications.html")
    results._forget(paper, ["docs/publications.html"])
    (paper / "docs" / "publications.html").write_text("<p>mine</p>")
    with pytest.raises(ValueError, match="not committed"):
        results.place(paper, "publication_list", run, "publications.html")
    with pytest.raises(ValueError, match="no place"):
        results.place(paper, "publication_list", run, "run.json")


def test_diff_revert_and_commit_touch_only_the_given_files(paper):
    run = make_run(paper)
    results.apply(paper, "publication_list", run, ["E1", "E2"])
    assert "-We show a big win." in results.diff(paper, "main.tex")
    assert results.diff(paper, "notes/new.md").startswith("--- /dev/null") and "+hi" in results.diff(paper, "notes/new.md")
    assert results.message(paper, ["main.tex"]).startswith("Update ") and "main.tex" in results.message(paper, ["main.tex"])
    assert results.revert(paper, ["notes/new.md"]) == {"reverted": ["notes/new.md"]}
    assert not (paper / "notes" / "new.md").exists()
    out = results.commit(paper, ["main.tex"])
    assert out["message"].startswith("Update ") and "a small gain" in git(paper, "show", "HEAD:main.tex")
    assert results.changes(paper) == []
    with pytest.raises(ValueError):
        results.commit(paper, [])
    with pytest.raises(ValueError):
        results.diff(paper, "../outside")


def test_setting_a_run_aside_and_history(paper):
    make_run(paper, name="20261124-090000", status="PASS", at="2026-11-24T09:00:00")
    run = make_run(paper)
    assert results.set_aside(paper, run) == {"set_aside": True}
    assert results.result(paper, "publication_list")["set_aside"] and results.pending(paper) == []
    past = results.history(paper, "publication_list")
    assert [p["status"] for p in past] == ["FAIL", "PASS"] and past[1]["at"] == "2026-11-24T09:00:00"


def test_answers_go_through_the_task_handler(paper, monkeypatch):
    run = make_run(paper)
    seen = []
    monkeypatch.setitem(results.ANSWERS, "publication_list",
                        lambda p, folder, q, o: seen.append((q, o)) or ["workflow/publication_list.json"])
    out = results.answer(paper, "publication_list", run, "doi:10.1/x", "include")
    assert out == {"answered": "doi:10.1/x", "changed": ["workflow/publication_list.json"]} and seen == [("doi:10.1/x", "include")]
    assert results.result(paper, "publication_list")["questions"][0]["answer"] == "include"
    with pytest.raises(ValueError, match="no such"):
        results.answer(paper, "publication_list", run, "doi:10.1/x", "maybe")
