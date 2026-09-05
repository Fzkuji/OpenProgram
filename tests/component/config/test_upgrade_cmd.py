"""Unit tests for ``openprogram upgrade`` (self-update phase 2)."""
from __future__ import annotations

import json
import subprocess

import pytest

from openprogram.cli.commands import upgrade as up


# ------------------------------------------------------------ helpers


def _make_repo(path, commits=2):
    """A real throwaway git repo — cheaper than mocking every git verb."""
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=str(path), check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    for i in range(commits):
        (path / "f.txt").write_text(f"{i}\n")
        run("add", "f.txt")
        run("commit", "-qm", f"c{i}")
    return path


def _sha(path, rev="HEAD"):
    return subprocess.run(["git", "rev-parse", rev], cwd=str(path),
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = _make_repo(tmp_path / "repo")
    monkeypatch.setattr(up, "repo_root", lambda: r)
    return r


# ------------------------------------------------------- channel table


def test_resolve_channel_default(monkeypatch):
    monkeypatch.setattr(up, "_configured_channel", lambda: "stable")
    assert up.resolve_channel() == ("stable", "origin", "main")


def test_resolve_channel_explicit_wins(monkeypatch):
    monkeypatch.setattr(up, "_configured_channel", lambda: "nonsense")
    assert up.resolve_channel("stable")[1:] == ("origin", "main")


def test_resolve_channel_unknown_lists_known():
    with pytest.raises(up.UpgradeError) as e:
        up.resolve_channel("beta")
    assert e.value.reason == "unknown-channel"
    assert "stable" in e.value.detail


def test_update_channel_is_a_registered_setting():
    from openprogram.config_schema import SETTINGS
    spec = next(s for s in SETTINGS if s.key == "update.channel")
    assert spec.default == "stable"
    assert "stable" in spec.choices()


# ------------------------------------------------------------ preflight


def test_dirty_worktree_aborts(repo, capsys):
    (repo / "dirty.txt").write_text("x")
    rc = up.run_upgrade(channel="stable", as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert out["reason"] == "dirty-worktree"
    assert out["steps"] == []  # nothing ran


def test_already_up_to_date_short_circuits(repo, monkeypatch, capsys):
    head = _sha(repo)
    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=head))
    rc = up.run_upgrade(channel="stable", as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "already-up-to-date"
    assert [s["name"] for s in out["steps"]] == ["preflight"]


def test_downgrade_requires_yes(repo, monkeypatch, capsys):
    old = _sha(repo, "HEAD~1")
    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=old))
    rc = up.run_upgrade(channel="stable", as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["reason"] == "downgrade-needs-confirmation"


def _fake_git(repo, fetch_head):
    """Real git for everything except fetch (no network) and the
    FETCH_HEAD lookup, which resolves to ``fetch_head``."""
    real = up._git

    def fake(root, *args, **kw):
        if args and args[0] == "fetch":
            return ""
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return fetch_head
        return real(root, *args, **kw)

    return fake


# --------------------------------------------------------------- steps


def test_dry_run_mutates_nothing(repo, monkeypatch, capsys):
    subprocess.run(["git", "branch", "-q", "target"], cwd=str(repo), check=True)
    head = _sha(repo)
    (repo / "f.txt").write_text("new\n")
    subprocess.run(["git", "commit", "-aqm", "next"], cwd=str(repo), check=True,
                   capture_output=True)
    target = _sha(repo)
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)

    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))
    # Anything that would build, probe, or restart must not be reached.
    monkeypatch.setattr(up, "_cold_start_probe", _explode)
    monkeypatch.setattr(up, "_run_or_fail", _explode)

    rc = up.run_upgrade(channel="stable", dry_run=True, as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "dry-run"
    assert out["from_sha"] == head and out["to_sha"] == target
    assert [s["name"] for s in out["steps"]] == [
        "preflight", "checkout", "deps", "build", "probe", "restart", "verify"]
    assert _sha(repo) == head  # working tree untouched


def _explode(*a, **kw):
    raise AssertionError("dry run must not mutate anything")


def test_chain_order_and_abort_on_probe_failure(repo, monkeypatch, capsys):
    head = _sha(repo)
    (repo / "f.txt").write_text("new\n")
    subprocess.run(["git", "commit", "-aqm", "next"], cwd=str(repo), check=True,
                   capture_output=True)
    target = _sha(repo)
    # Write FETCH_HEAD as a plain file rather than via `git update-ref`:
    # git >= 2.45 refuses to update pseudorefs through that plumbing
    # ("refusing to update pseudoref"), and only real `git fetch` may write
    # it. Doing it before the reset also keeps `target` referenced, so a gc
    # can't prune the object out from under the test.
    (repo / ".git" / "FETCH_HEAD").write_text(
        f"{target}\t\tbranch 'main' of origin\n")
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)

    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))

    def boom(root, sha):
        raise up.UpgradeError("probe-failed", "cold start on :1 timed out")

    monkeypatch.setattr(up, "_cold_start_probe", boom)
    restarted = []
    monkeypatch.setattr(
        "openprogram.worker.restart_worker",
        lambda *a, **kw: restarted.append(1) or 0, raising=False)

    rc = up.run_upgrade(channel="stable", as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["reason"] == "probe-failed"
    # Steps run in order, and the chain stops before restart/verify.
    assert [s["name"] for s in out["steps"]] == [
        "preflight", "checkout", "deps", "build"]
    assert restarted == []
    assert all("duration_s" in s for s in out["steps"])


def test_no_restart_stops_after_probe(repo, monkeypatch, capsys):
    head = _sha(repo)
    (repo / "f.txt").write_text("new\n")
    subprocess.run(["git", "commit", "-aqm", "next"], cwd=str(repo), check=True,
                   capture_output=True)
    target = _sha(repo)
    # Write FETCH_HEAD as a plain file rather than via `git update-ref`:
    # git >= 2.45 refuses to update pseudorefs through that plumbing
    # ("refusing to update pseudoref"), and only real `git fetch` may write
    # it. Doing it before the reset also keeps `target` referenced, so a gc
    # can't prune the object out from under the test.
    (repo / ".git" / "FETCH_HEAD").write_text(
        f"{target}\t\tbranch 'main' of origin\n")
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)

    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))
    monkeypatch.setattr(up, "_cold_start_probe", lambda root, sha: "probe ok")

    rc = up.run_upgrade(channel="stable", no_restart=True, as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "restart-skipped"
    assert [s["name"] for s in out["steps"]] == [
        "preflight", "checkout", "deps", "build", "probe"]
    assert _sha(repo) == target  # the checkout really moved


# ------------------------------------------------------- deps detection


def test_deps_run_only_when_manifests_change(repo, monkeypatch, capsys):
    """`pip install -e .` and `npm ci` are the slow steps — they must fire
    on a manifest change and stay quiet otherwise."""
    head = _sha(repo)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / "package-lock.json").write_text("{}")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "manifests"], cwd=str(repo),
                   check=True, capture_output=True)
    target = _sha(repo)
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)

    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))
    monkeypatch.setattr(up, "_cold_start_probe", lambda root, sha: "probe ok")
    ran: list[list[str]] = []
    monkeypatch.setattr(
        up,
        "_run_or_fail",
        lambda cmd, cwd, reason, **kwargs: ran.append((cmd, kwargs)),
    )

    up.run_upgrade(channel="stable", no_restart=True, as_json=True)
    capsys.readouterr()
    assert ran[0][0][-3:] == ["install", "-e", "."]
    npm_cmd, npm_options = ran[1]
    assert npm_cmd == [
        "npm",
        "ci",
        "--include-workspace-root",
        "--ignore-scripts",
    ]
    assert npm_options == {"node_tool": True}


def test_deps_skipped_when_manifests_unchanged(repo, monkeypatch, capsys):
    head = _sha(repo)
    (repo / "f.txt").write_text("only code\n")
    subprocess.run(["git", "commit", "-aqm", "code"], cwd=str(repo), check=True,
                   capture_output=True)
    target = _sha(repo)
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)

    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))
    monkeypatch.setattr(up, "_cold_start_probe", lambda root, sha: "probe ok")
    monkeypatch.setattr(up, "_run_or_fail", _explode)  # neither deps nor build

    rc = up.run_upgrade(channel="stable", no_restart=True, as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    detail = {s["name"]: s["detail"] for s in out["steps"]}
    assert detail["deps"] == "unchanged"
    assert detail["build"] == "unchanged"


# -------------------------------------------------------------- sentinel


def test_sentinel_records_progress(repo, monkeypatch, tmp_path, capsys):
    sentinel = tmp_path / "upgrade-state.json"
    monkeypatch.setattr(up, "_sentinel_path", lambda: sentinel)
    (repo / "dirty.txt").write_text("x")

    up.run_upgrade(channel="stable", as_json=True)
    capsys.readouterr()
    saved = json.loads(sentinel.read_text())
    assert saved["ok"] is False
    assert saved["reason"] == "dirty-worktree"
    assert "updated_at" in saved


def test_dry_run_writes_no_sentinel(repo, monkeypatch, tmp_path, capsys):
    sentinel = tmp_path / "upgrade-state.json"
    monkeypatch.setattr(up, "_sentinel_path", lambda: sentinel)
    head = _sha(repo)
    (repo / "f.txt").write_text("new\n")
    subprocess.run(["git", "commit", "-aqm", "next"], cwd=str(repo), check=True,
                   capture_output=True)
    target = _sha(repo)
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=str(repo), check=True)
    monkeypatch.setattr(up, "_git", _fake_git(repo, fetch_head=target))

    up.run_upgrade(channel="stable", dry_run=True, as_json=True)
    capsys.readouterr()
    assert not sentinel.exists()


def test_sentinel_failure_never_breaks_upgrade(repo, monkeypatch, capsys):
    def unwritable():
        raise OSError("read-only state dir")

    monkeypatch.setattr(up, "_sentinel_path", unwritable)
    (repo / "dirty.txt").write_text("x")
    # Still reports the real reason rather than the sentinel's problem.
    rc = up.run_upgrade(channel="stable", as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["reason"] == "dirty-worktree"


# --------------------------------------------------- channel persistence


def test_persist_channel_rejects_unknown(monkeypatch):
    saved: list = []
    monkeypatch.setattr("openprogram.config_schema.set_setting",
                        lambda k, v: saved.append((k, v)))
    with pytest.raises(up.UpgradeError):
        up.persist_channel("beta")
    assert saved == []  # a typo must never be written


def test_persist_channel_writes_known(monkeypatch):
    saved: list = []
    monkeypatch.setattr("openprogram.config_schema.set_setting",
                        lambda k, v: saved.append((k, v)))
    up.persist_channel("stable")
    assert saved == [(up.CONFIG_KEY, "stable")]


# -------------------------------------------------------------- healthz


def test_backend_poll_uses_challenge_revision_proof(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "openprogram._ports.backend_is_ours",
        lambda port, *, expected_revision=None: calls.append(
            (port, expected_revision)
        ) or True,
    )

    ok, detail = up._poll_backend_identity(
        18100,
        timeout=1.0,
        expected_revision="a" * 40,
    )

    assert ok is True
    assert detail == "serving aaaaaaaaaaaa"
    assert calls == [(18100, "a" * 40)]


def test_healthz_reports_sha(monkeypatch):
    from openprogram.webui.routes import misc
    monkeypatch.setattr(misc, "_HEAD_SHA", None)
    sha = misc._head_sha()
    assert isinstance(sha, str)
    # This repo is a checkout, so the field is the real HEAD.
    assert sha == subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(__import__("pathlib").Path(misc.__file__).resolve().parents[3]),
        capture_output=True, text=True).stdout.strip()


def test_head_sha_is_cached(monkeypatch):
    from openprogram.webui.routes import misc
    monkeypatch.setattr(misc, "_HEAD_SHA", "cached-value")
    monkeypatch.setattr(subprocess, "run", _explode)
    assert misc._head_sha() == "cached-value"
