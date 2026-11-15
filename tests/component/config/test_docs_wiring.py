"""The docs check is reached from the places agents actually pass."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from openprogram.operations.self_update.versions import build as version_build
from scripts.docs_site.hook_status import stale_hooks
from scripts.testing import affected_tests
from scripts.testing.affected_tests import commands, prepare_web, run_planned, select, web_test_count

ROOT = Path(__file__).resolve().parents[3]

# Behaves like the real command for these tests: a style attribute is an error.
# It logs each call, and CHECK_EXIT forces a tool failure.
STUB_CHECK = """
import os, pathlib, subprocess, sys
if __name__ == "__main__":
    marker = pathlib.Path(os.environ["CHECK_LOG"])
    marker.open("a").write(" ".join(sys.argv[1:]) + "\\n")
    if "CHECK_EXIT" in os.environ:
        sys.exit(int(os.environ["CHECK_EXIT"]))
    if "--staged" in sys.argv:
        names = subprocess.run(["git", "diff", "--cached", "--name-only", "--", "docs"],
                               capture_output=True, text=True, check=True).stdout.split()
        texts = {n: subprocess.run(["git", "show", ":" + n], capture_output=True, text=True).stdout for n in names}
    else:
        texts = {str(p): p.read_text() for p in pathlib.Path("docs").rglob("*.html")}
    bad = [n for n, t in texts.items() if "style=" in t]
    for name in bad:
        print(f"{name}:1: error format.style-attribute: a style attribute. Fix: use a site class.")
    sys.exit(1 if bad else 0)
"""


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], capture_output=True, text=True
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository holding the real hooks, with a stand-in for the check."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    monkeypatch.setenv("CHECK_LOG", str(tmp_path / "check.log"))
    monkeypatch.delenv("CHECK_EXIT", raising=False)
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", "-b", "dev", str(repository)], check=True)
    for relative in ("scripts/git-hooks/pre-commit", "scripts/git-hooks/pre-push", "scripts/git-hooks/post-merge", "scripts/git-hooks/install.sh"):
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    _write(repository / "scripts/docs_site/check.py", STUB_CHECK)
    _write(repository / "docs/good.html", "<h1>Good</h1>\n")
    _write(repository / ".gitignore", ".venv/\n")
    assert _git(repository, "add", ".").returncode == 0
    assert _git(repository, "commit", "--quiet", "-m", "First").returncode == 0
    (repository / ".venv/bin").mkdir(parents=True)
    (repository / ".venv/bin/python").symlink_to(sys.executable)
    subprocess.run(["sh", str(repository / "scripts/git-hooks/install.sh")], check=True)
    return repository


def _log(repository: Path) -> str:
    log = repository.parent / "check.log"
    return log.read_text() if log.exists() else ""


# ------------------------------------------------------------------- hook


def test_hook_refuses_a_staged_violation_and_names_it(repository: Path) -> None:
    _write(repository / "docs/bad.html", '<h1 style="x">Bad</h1>\n')
    _git(repository, "add", "docs/bad.html")

    result = _git(repository, "commit", "--quiet", "-m", "Bad page")

    assert result.returncode != 0
    assert "docs/bad.html:1: error format.style-attribute" in result.stdout + result.stderr
    assert "commit refused" in result.stderr
    assert _git(repository, "log", "--format=%s", "-1").stdout.strip() == "First"


def test_hook_judges_the_commit_not_another_sessions_dirty_page(repository: Path) -> None:
    # Another session's page, unsaved and unstaged, breaks the format.
    _write(repository / "docs/theirs.html", '<h1 style="x">Theirs</h1>\n')
    _write(repository / "docs/mine.html", "<h1>Mine</h1>\n")
    _git(repository, "add", "docs/mine.html")

    assert _git(repository, "commit", "--quiet", "-m", "Mine").returncode == 0
    assert _log(repository).strip() == "--staged"


def test_hook_reads_only_the_paths_of_a_partial_commit(repository: Path) -> None:
    _write(repository / "docs/theirs.html", '<h1 style="x">Theirs</h1>\n')
    _git(repository, "add", "docs/theirs.html")
    _write(repository / "docs/good.html", "<h1>Better</h1>\n")

    assert _git(repository, "commit", "--quiet", "-m", "Mine", "--", "docs/good.html").returncode == 0


def test_hook_stays_out_of_commits_without_docs(repository: Path) -> None:
    _write(repository / "a.py", "x = 1\n")
    _git(repository, "add", "a.py")

    assert _git(repository, "commit", "--quiet", "-m", "Code").returncode == 0
    assert _log(repository) == ""


@pytest.mark.parametrize("breakage", ["tool", "venv"])
def test_hook_does_not_block_when_the_tool_cannot_judge(
    repository: Path, monkeypatch: pytest.MonkeyPatch, breakage: str
) -> None:
    if breakage == "tool":
        monkeypatch.setenv("CHECK_EXIT", "2")
    else:
        shutil.rmtree(repository / ".venv")
    _write(repository / "docs/bad.html", '<h1 style="x">Bad</h1>\n')
    _git(repository, "add", "docs/bad.html")

    result = _git(repository, "commit", "--quiet", "-m", "Page")

    assert result.returncode == 0, result.stderr
    assert "docs check" in result.stderr


def test_hook_still_refuses_commits_on_main(repository: Path) -> None:
    _git(repository, "checkout", "--quiet", "-b", "main")
    _write(repository / "a.py", "x = 1\n")
    _git(repository, "add", "a.py")

    result = _git(repository, "commit", "--quiet", "-m", "Code")

    assert result.returncode != 0 and "committing on main is refused" in result.stderr


def test_install_reaches_the_shared_hooks_from_a_worktree(repository: Path) -> None:
    linked = repository.parent / "linked"
    assert _git(repository, "worktree", "add", "--quiet", "--detach", str(linked)).returncode == 0
    (repository / ".git/hooks/pre-commit").unlink()
    (linked / "scripts/git-hooks/pre-push").write_text("#!/bin/sh\n# newer\n")

    assert sorted(stale_hooks(linked)) == ["pre-commit", "pre-push"]
    subprocess.run(["sh", str(linked / "scripts/git-hooks/install.sh")], check=True)

    assert stale_hooks(linked) == []
    installed = repository / ".git/hooks/pre-push"
    assert installed.read_text().endswith("# newer\n") and os.access(installed, os.X_OK)
    assert not list((repository / ".git/hooks").glob(".pre-*"))


# ---------------------------------------------------------------- publish


@pytest.fixture
def publishable(repository: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("publish-github.sh", "retired_accounts.py"):
        shutil.copy2(ROOT / "scripts" / name, repository / "scripts" / name)
    monkeypatch.setenv("OPENPROGRAM_DOCS_PYTHON", sys.executable)
    assert _git(repository, "add", ".").returncode == 0
    assert _git(repository, "commit", "--quiet", "-m", "Script").returncode == 0
    return repository


def _publish(repository: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/publish-github.sh", "-m", "Version", "--no-push"],
        cwd=repository, capture_output=True, text=True,
    )


def test_publish_checks_the_commit_it_ships(publishable: Path) -> None:
    repository = publishable
    # stable has the violation; this checkout has moved on and repaired it.
    _write(repository / "docs/bad.html", '<h1 style="x">Bad</h1>\n')
    _git(repository, "add", ".")
    _git(repository, "commit", "--no-verify", "--quiet", "-m", "Bad")
    _git(repository, "branch", "stable")
    (repository / "docs/bad.html").unlink()
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Repaired")

    result = _publish(repository)

    assert result.returncode == 1
    assert "docs/bad.html:1: error" in result.stdout and "refusing to publish" in result.stderr
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode != 0
    assert len(_git(repository, "worktree", "list").stdout.splitlines()) == 1
    assert "--all" in _log(repository)


def test_publish_ignores_a_violation_that_is_not_shipping(publishable: Path) -> None:
    repository = publishable
    _git(repository, "branch", "stable")
    _write(repository / "docs/bad.html", '<h1 style="x">Bad</h1>\n')  # dirty, unshipped

    assert _publish(repository).returncode == 0
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode == 0


# --------------------------------------------------------- deploy and build


def test_deploy_reports_docs_problems_and_quarantines_the_build() -> None:
    script = (ROOT / "scripts/refresh-dev-instance.sh").read_text(encoding="utf-8")
    report = script.index('-m scripts.docs_site.check --base "$last_revision" --report-only')
    assert script.index("scripts/git-hooks/install.sh") < report
    assert report < script.index("export OPENPROGRAM_DOCS_FORMAT=quarantine")
    assert script.index("export OPENPROGRAM_DOCS_FORMAT=quarantine") < script.index("stage-release-assets.sh")
    subprocess.run(["bash", "-n", str(ROOT / "scripts/refresh-dev-instance.sh")], check=True)


def test_stable_refresh_quarantines_when_it_has_to_build() -> None:
    script = (ROOT / "scripts/refresh-local-app.sh").read_text(encoding="utf-8")
    assert 'OPENPROGRAM_DOCS_FORMAT=quarantine $build_nice "$repo_root/scripts/release/stage-release-assets.sh"' in script


def test_self_update_builds_docs_in_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    class Stop(Exception):
        pass

    def fake_run(command, *, environment, **options):
        seen.update(environment)
        raise Stop

    monkeypatch.setattr(version_build, "_ensure_toolchain", lambda *a: tmp_path)
    monkeypatch.setattr(version_build, "_run", fake_run)
    with pytest.raises(Stop):
        version_build._stage_frontend(None, tmp_path, tmp_path, Path("python"), {"PATH": "/bin"})

    assert seen["OPENPROGRAM_DOCS_FORMAT"] == "quarantine"


# --------------------------------------------------------------- selector


def test_the_real_repository_runs_contracts_for_a_web_only_change() -> None:
    selection = select(["apps/web/lib/utils.ts"], ROOT)

    contracts = [path for path in selection.python_tests if path.startswith("tests/contracts/")]
    assert len(contracts) >= 20
    assert selection.docs_check == ["apps/web/lib/utils.ts"]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["ℹ tests 12\n", "ℹ pass 12\n"], 12),
        (["\x1b[34mℹ tests 0\x1b[39m\n"], 0),
        (["# tests 3\n"], 3),
        (["no summary here\n"], None),
    ],
)
def test_web_summary_is_read(lines: list[str], expected: int | None) -> None:
    assert web_test_count(lines) == expected


WEB_PLAN = [("apps/web", ["node", "--test", "tests/a.test.mjs", "tests/b.test.mjs"]),
            ("apps/web", ["npx", "tsc", "--noEmit", "-p", "."])]


def test_a_web_run_of_zero_tests_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "apps/web").mkdir(parents=True)
    monkeypatch.setattr(affected_tests, "prepare_web", lambda root: None)
    monkeypatch.setattr(affected_tests, "_stream", lambda command, directory: (0, ["ℹ tests 0\n"]))

    assert run_planned(WEB_PLAN[:1], tmp_path) == 1


def test_web_checks_that_cannot_run_exit_two_and_say_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(affected_tests, "prepare_web", lambda root: "node is not on PATH")
    monkeypatch.setattr(affected_tests, "_stream", lambda command, directory: (ran.append(command), [])[0] or (0, []))

    status = run_planned([*WEB_PLAN, (".", ["python", "-m", "pytest"])], tmp_path)

    assert status == 2
    assert ran == [["python", "-m", "pytest"]], "what can run still runs"
    assert "COULD NOT RUN 2 Web test files and the typecheck: node is not on PATH" in capsys.readouterr().err


def test_a_failure_outranks_web_being_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(affected_tests, "prepare_web", lambda root: "no node")
    monkeypatch.setattr(affected_tests, "_stream", lambda command, directory: (1, []))

    assert run_planned([*WEB_PLAN, (".", ["python", "-m", "pytest"])], tmp_path) == 1


def test_prepare_web_clones_from_the_main_checkout_without_links(repository: Path) -> None:
    (repository / "node_modules/pkg").mkdir(parents=True)
    (repository / "node_modules/pkg/index.js").write_text("x")
    (repository / "apps/web/node_modules/local").mkdir(parents=True)
    linked = repository.parent / "linked"
    assert _git(repository, "worktree", "add", "--quiet", "--detach", str(linked)).returncode == 0

    assert prepare_web(linked) is None

    assert (linked / "node_modules/pkg/index.js").read_text() == "x"
    assert not (linked / "node_modules").is_symlink()
    assert (linked / "apps/web/node_modules/local").is_dir()
    assert not list(linked.glob("node_modules.cloning-*"))
    # Already there: nothing is cloned again.
    (linked / "node_modules/marker").write_text("kept")
    assert prepare_web(linked) is None and (linked / "node_modules/marker").exists()


def test_prepare_web_names_what_is_missing(repository: Path) -> None:
    linked = repository.parent / "linked"
    assert _git(repository, "worktree", "add", "--quiet", "--detach", str(linked)).returncode == 0

    assert "no main checkout copy to clone" in prepare_web(linked)


def test_prepare_web_needs_node(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(affected_tests.shutil, "which", lambda name: None)

    assert prepare_web(repository) == "node is not on PATH"


def test_a_real_change_selecting_no_test_is_a_failure(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(affected_tests, "ROOT", repository)

    assert affected_tests.main(["docs/good.html"]) == 1
    assert "no test covers" in capsys.readouterr().err


def test_no_change_is_not_a_failure(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(affected_tests, "ROOT", repository)

    assert affected_tests.main([]) == 0


def test_main_gives_the_docs_check_the_scope_it_was_given(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repository / "tests/unit/test_x.py", "def test_x(): pass\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Test")
    monkeypatch.setattr(affected_tests, "ROOT", repository)

    assert affected_tests.main(["--base", "HEAD~1"]) == 0
    assert "-m scripts.docs_site.check --base 'HEAD~1'" in capsys.readouterr().out
    assert commands(select(["a.py"], repository), repository)[-1][1][-1] == "a.py"
