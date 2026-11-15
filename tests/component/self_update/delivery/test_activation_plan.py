"""Plan activation from the installed App's revision in a real repository."""
from __future__ import annotations

import subprocess
from pathlib import Path

from openprogram.operations.self_update.delivery.activation import installed_revision, plan_for, predict

REV = "a" * 40


def _app(tmp_path: Path, revision: str, *, installer: bool = True) -> Path:
    resources = tmp_path / "OpenProgram.app/Contents/Resources"
    (resources / "update").mkdir(parents=True)
    (resources / "openprogram-source-revision").write_text(revision + "\n")
    if installer:
        (resources / "update/install-app.sh").write_text('case "$1" in --restart=*) ;; esac\n')
    return tmp_path / "OpenProgram.app"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_plan_for_diffs_installed_revision(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs/a.md").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "docs/a.md").write_text("two\n")
    _git(repo, "commit", "-qam", "docs")
    candidate = _git(repo, "rev-parse", "HEAD")

    app = _app(tmp_path, base)
    assert installed_revision(app) == base
    plan = plan_for(app, repo, candidate)
    assert (plan.restart, plan.components, plan.installed_revision) == ("hot", ("docs",), base)
    assert predict(app, repo, candidate) == plan
    assert plan_for(_app(tmp_path / "x", "b" * 40), repo, candidate).scope == "full"
    assert predict(_app(tmp_path / "y", base, installer=False), repo, candidate).scope == "full"


def test_dirty_installed_revision_is_unknown(tmp_path):
    assert installed_revision(_app(tmp_path, REV + "-dirty")) is None
