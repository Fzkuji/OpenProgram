"""Unit tests for the security_review agentic function
(``openprogram/functions/agentics/security_review/``): baseline
selection, diff collection over a real git repository, the short-circuit
that returns no findings without spawning an agent, and the shape of
what comes back.

The review turn goes through the module-level ``_run_review_turn`` seam
(the same shape ``goal`` and ``task_list`` use), so these tests stub it
and never reach a provider. Git is real — baseline selection is exactly
the part that is worth testing against git rather than against a mock of
it."""
from __future__ import annotations

import json
import subprocess

import pytest

import openprogram.functions.agentics.security_review as SR


def _run(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository on a feature branch with one commit on top of
    ``main``, with the module's cwd pointed at it."""
    path = tmp_path / "repo"
    path.mkdir()
    _run(path, "init", "-b", "main")
    _run(path, "config", "user.email", "t@t")
    _run(path, "config", "user.name", "t")
    (path / "base.py").write_text("x = 1\n")
    _run(path, "add", ".")
    _run(path, "commit", "-m", "base")
    _run(path, "checkout", "-b", "feature")
    monkeypatch.setattr(
        "openprogram.worktree.context.current_worktree_path",
        lambda: str(path))
    return path


@pytest.fixture
def no_agent(monkeypatch):
    """Fails the test if a review turn is spawned."""
    def _boom(*a, **k):
        raise AssertionError("a review agent was spawned")
    monkeypatch.setattr(SR, "_run_review_turn", _boom)


def _reply(*findings):
    return json.dumps({"findings": list(findings)})


# ---------------------------------------------------------------------------
# Baseline selection
# ---------------------------------------------------------------------------

def test_base_is_merge_base_with_default_branch(repo) -> None:
    (repo / "new.py").write_text("y = 2\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "work")

    base = SR.resolve_base(str(repo))

    expect = subprocess.run(["git", "rev-parse", "main"], cwd=repo,
                            capture_output=True, text=True).stdout.strip()
    assert base == expect          # the fork point, not HEAD~1


def test_upstream_wins_over_default_branch(repo) -> None:
    """A configured upstream is what the branch will be compared against,
    so it beats the default branch even when both exist."""
    _run(repo, "checkout", "-b", "shared", "main")
    (repo / "shared.py").write_text("s = 1\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "shared work")
    shared = subprocess.run(["git", "rev-parse", "shared"], cwd=repo,
                            capture_output=True, text=True).stdout.strip()
    _run(repo, "checkout", "feature")
    _run(repo, "merge", "--no-edit", "shared")
    _run(repo, "branch", "--set-upstream-to=shared")

    assert SR.resolve_base(str(repo)) == shared


def test_no_baseline_raises_with_explanation(tmp_path) -> None:
    """A repository with no upstream and no default branch has no range
    to review — it says so rather than guessing one."""
    path = tmp_path / "lonely"
    path.mkdir()
    _run(path, "init", "-b", "wip")
    _run(path, "config", "user.email", "t@t")
    _run(path, "config", "user.name", "t")
    (path / "a.py").write_text("a = 1\n")
    _run(path, "add", ".")
    _run(path, "commit", "-m", "only")

    with pytest.raises(SR.NoBaselineError) as exc:
        SR.resolve_base(str(path))
    assert "no upstream" in str(exc.value)


def test_outside_a_repository_raises(tmp_path) -> None:
    with pytest.raises(SR.NoBaselineError):
        SR.resolve_base(str(tmp_path))


# ---------------------------------------------------------------------------
# Diff collection
# ---------------------------------------------------------------------------

def test_diff_covers_committed_staged_and_unstaged(repo) -> None:
    (repo / "committed.py").write_text("c = 1\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "committed")
    (repo / "staged.py").write_text("s = 1\n")
    _run(repo, "add", "staged.py")
    (repo / "base.py").write_text("x = 99\n")          # unstaged edit

    diff, files = SR.collect_diff(SR.resolve_base(str(repo)), str(repo))

    assert set(files) == {"committed.py", "staged.py", "base.py"}
    assert "x = 99" in diff


def test_untracked_files_are_reviewed(repo) -> None:
    """A new file is where a hardcoded secret lives, and a plain diff
    cannot see it."""
    (repo / "secrets.py").write_text('KEY = "sk-live-abc"\n')

    diff, files = SR.collect_diff(SR.resolve_base(str(repo)), str(repo))

    assert "secrets.py" in files
    assert "sk-live-abc" in diff


def test_ignored_files_stay_out(repo) -> None:
    (repo / ".gitignore").write_text("build/\n")
    _run(repo, "add", ".gitignore")
    _run(repo, "commit", "-m", "ignore build")
    (repo / "build").mkdir()
    (repo / "build" / "out.py").write_text("generated = 1\n")

    _diff, files = SR.collect_diff(SR.resolve_base(str(repo)), str(repo))

    assert not any(f.startswith("build/") for f in files)


def test_collect_diff_leaves_the_index_alone(repo) -> None:
    """Reviewing must not touch the index the author is composing."""
    (repo / "staged.py").write_text("s = 1\n")
    _run(repo, "add", "staged.py")
    (repo / "untracked.py").write_text("u = 1\n")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True).stdout

    SR.collect_diff(SR.resolve_base(str(repo)), str(repo))

    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True).stdout
    assert before == after


def test_oversized_diff_is_cut_and_says_so() -> None:
    cut = SR.clip_diff("x" * (SR.DIFF_MAX_CHARS + 100))
    assert "truncated" in cut
    assert len(cut) < SR.DIFF_MAX_CHARS + 400


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def test_empty_diff_returns_no_findings_without_an_agent(repo,
                                                         no_agent) -> None:
    out = SR.run_security_review(base="", session_id="s1")

    assert out["findings"] == []
    assert out["files_reviewed"] == 0
    assert out["base"]


def test_explicit_base_skips_baseline_selection(repo, monkeypatch) -> None:
    def _no_resolve(*a, **k):
        raise AssertionError("resolve_base ran despite an explicit base")
    monkeypatch.setattr(SR, "resolve_base", _no_resolve)
    monkeypatch.setattr(SR, "_run_review_turn", lambda *a, **k: _reply())
    (repo / "new.py").write_text("n = 1\n")

    out = SR.run_security_review(base="main", session_id="s1")

    assert out["base"] == "main"


def test_findings_come_back_structured_and_ordered(repo, monkeypatch) -> None:
    prompts = []

    def _turn(sid, prompt, *, agent_id, spawn_caller):
        prompts.append(prompt)
        return "Here is what I found.\n" + _reply(
            {"severity": "low", "file": "a.py", "line": 3, "title": "verbose error",
             "scenario": "any caller sees the stack trace",
             "recommendation": "log it, return a generic message"},
            {"severity": "critical", "file": "b.py", "line": "12",
             "title": "shell injection",
             "scenario": "a caller passes `; rm -rf /` as name",
             "recommendation": "pass a list to subprocess, no shell=True"},
        )

    monkeypatch.setattr(SR, "_run_review_turn", _turn)
    (repo / "b.py").write_text("import os\nos.system(name)\n")

    out = SR.run_security_review(session_id="s1")

    assert out["files_reviewed"] == 1
    assert [f["severity"] for f in out["findings"]] == ["critical", "low"]
    first = out["findings"][0]
    assert set(first) == {"severity", "file", "line", "title", "scenario",
                          "recommendation"}
    assert first["line"] == 12                      # coerced from the string
    # the prompt carries the scope rules and the payload
    assert "introduces or makes worse" in prompts[0]
    assert "<changed_files>\nb.py\n</changed_files>" in prompts[0]
    assert "os.system(name)" in prompts[0]


def test_unnamed_finding_is_dropped_and_bad_severity_normalised() -> None:
    out = SR._clean_findings([
        {"severity": "high", "file": "a.py"},                 # no title
        {"severity": "spicy", "title": "odd label"},
        "not a dict",
    ])

    assert len(out) == 1
    assert out[0]["severity"] == "medium"
    assert out[0]["line"] == 0


def test_reply_without_json_is_an_error(repo, monkeypatch) -> None:
    monkeypatch.setattr(SR, "_run_review_turn",
                        lambda *a, **k: "looks fine to me")
    (repo / "new.py").write_text("n = 1\n")

    with pytest.raises(ValueError):
        SR.run_security_review(session_id="s1")


def test_reviewer_gets_no_write_tools() -> None:
    assert not ({"write", "edit", "apply_patch", "task"} & set(SR.REVIEW_TOOLS))
