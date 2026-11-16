"""Promotion moves `stable` only to the commit the development instance serves."""
import os
import subprocess
import time

import pytest

from openprogram.operations.self_update.control import supervisor
from openprogram.operations.worker import dev_instance as instances


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    shas = []
    for n in range(3):
        (repo / "f").write_text(str(n))
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", f"c{n}")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    _git(repo, "branch", "stable", shas[0])
    home = tmp_path / "home"
    monkeypatch.setattr(instances.Path, "home", lambda: home)
    monkeypatch.setenv("OPENPROGRAM_SOURCE_REPO", str(repo))
    monkeypatch.setattr(instances, "healthy", lambda port: True)
    runs, backups, checked_out = [], [], []
    monkeypatch.setattr(instances, "_managed_worktree",
                        lambda repo, path, rev: checked_out.append(rev) or path)

    def refresh(script, cwd, env=None):
        # The refresh moves stable, compare-and-swap, once the new version is healthy.
        runs.append(script.name)
        _git(repo, "update-ref", "refs/heads/stable", checked_out[-1],
             env["OPENPROGRAM_PROMOTE_FROM"] or "0" * 40)

    monkeypatch.setattr(instances, "_run", refresh)
    import openprogram.operations.backup as backup
    monkeypatch.setattr(backup, "create_backup", lambda **kw: backups.append(kw))
    monkeypatch.setattr(supervisor, "active_work", lambda: [])
    monkeypatch.setattr(instances, "_stable_worker_pid", lambda: 4242)

    def serve(sha):
        target = instances.dev_runtime_dir()
        target.mkdir(parents=True, exist_ok=True)
        (target / "source-revision").write_text(sha + "\n")

    return repo, shas, serve, runs, backups


def test_promotes_the_served_commit(world):
    repo, shas, serve, runs, backups = world
    serve(shas[2])
    assert [c["subject"] for c in instances.status()["promotable"]] == ["c2", "c1"]
    assert instances.promote() == shas[2]
    assert _git(repo, "rev-parse", "stable") == shas[2]
    assert backups == [{"label": "pre-promote"}]
    assert runs == ["refresh-local-app.sh"]


def test_promotion_hands_the_refresh_the_commit_and_the_stable_tip(world, monkeypatch):
    _, shas, serve, _, _ = world
    serve(shas[2])
    envs = []
    monkeypatch.setattr(instances, "_run", lambda script, cwd, env=None: envs.append(env))
    instances.promote()
    # The dev build is reused, stable moves from the tip read here, and the
    # refresh is waited for even from inside a conversation.
    assert envs == [{"OPENPROGRAM_PREBUILT_DIR": str(instances.dev_artifacts_dir()),
                     "OPENPROGRAM_PROMOTE_FROM": shas[0],
                     "OPENPROGRAM_SESSION_ID": None}]


def test_the_first_promotion_moves_stable_from_nothing(world):
    repo, shas, serve, runs, _ = world
    _git(repo, "branch", "-D", "stable")
    serve(shas[2])
    assert instances.promote() == shas[2]
    assert _git(repo, "rev-parse", "stable") == shas[2]


def test_a_failed_refresh_is_not_retried_and_says_what_runs(world, monkeypatch):
    repo, shas, serve, _, _ = world
    serve(shas[1])
    monkeypatch.setattr(instances, "stable_revision", lambda: shas[0])
    attempts = []

    def broken(script, cwd, env=None):
        attempts.append(script.name)
        raise instances.InstanceError("refresh-local-app.sh failed (exit 1)")

    monkeypatch.setattr(instances, "_run", broken)
    with pytest.raises(instances.RefreshFailedError) as failed:
        instances.promote(shas[1])
    message = str(failed.value)
    assert attempts == ["refresh-local-app.sh"]
    # The refresh restored the previous version, and stable never moved.
    assert _git(repo, "rev-parse", "stable") == shas[0]
    assert "\n" not in message and message.startswith(f"{shas[1][:10]} was not promoted")
    assert f"now stable is {shas[0][:10]} and the App runs {shas[0][:10]}, healthy" in message


def test_a_failure_after_stable_moved_is_a_promotion(world, monkeypatch, capsys):
    repo, shas, serve, _, _ = world
    serve(shas[1])
    monkeypatch.setattr(instances, "stable_revision", lambda: shas[1])

    def reopen_failed(script, cwd, env=None):
        # The new worker was healthy and stable moved; then the App did not reopen.
        _git(repo, "update-ref", "refs/heads/stable", shas[1], env["OPENPROGRAM_PROMOTE_FROM"])
        raise instances.InstanceError("refresh-local-app.sh failed (exit 1)")

    monkeypatch.setattr(instances, "_run", reopen_failed)
    assert instances.promote(shas[1]) == shas[1]
    assert f"promoted {shas[1][:10]}, but the refresh then failed" in capsys.readouterr().err


def test_a_session_variable_is_removed_from_the_refresh(tmp_path, monkeypatch):
    script = tmp_path / "show.sh"
    out = tmp_path / "out"
    script.write_text(f'printf "%s|%s" "${{OPENPROGRAM_SESSION_ID-unset}}" "$KEPT" > "{out}"\n')
    monkeypatch.setenv("OPENPROGRAM_SESSION_ID", "s1")
    instances._run(script, tmp_path, {"OPENPROGRAM_SESSION_ID": None, "KEPT": "yes"})
    assert out.read_text() == "unset|yes"


def test_refuses_a_commit_dev_does_not_serve(world):
    repo, shas, serve, runs, _ = world
    serve(shas[1])
    with pytest.raises(instances.NotServedError, match="only the commit the development instance serves"):
        instances.promote(shas[2])
    assert _git(repo, "rev-parse", "stable") == shas[0] and runs == []


def test_refuses_dirty_unhealthy_or_backwards(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[2] + "-dirty")
    with pytest.raises(instances.InstanceError, match="no clean commit"):
        instances.promote()
    serve(shas[2])
    monkeypatch.setattr(instances, "healthy", lambda port: False)
    with pytest.raises(instances.InstanceError, match="not healthy"):
        instances.promote()
    monkeypatch.setattr(instances, "healthy", lambda port: True)
    _git(repo, "branch", "-f", "stable", shas[2])
    serve(shas[1])
    with pytest.raises(instances.InstanceError, match="only fast-forwards"):
        instances.promote()
    assert runs == []


def _busy(monkeypatch, snapshots, quiet=False):
    """``active_work`` answers from ``snapshots``; records the wait it was given."""
    waits = []
    answers = iter(snapshots)
    monkeypatch.setattr(supervisor, "active_work", lambda: next(answers))
    monkeypatch.setattr(supervisor, "_wait_for_quiescence",
                        lambda deadline: waits.append(deadline - time.time()) or quiet)
    monkeypatch.setattr(instances, "_inside_worker", lambda pid: False)
    return waits


def test_promotion_waits_for_stable_to_go_quiet(world, monkeypatch):
    repo, shas, serve, runs, backups = world
    serve(shas[2])
    waits = _busy(monkeypatch, [["a turn in conversation c1"]], quiet=True)
    said = []
    assert instances.promote(on_wait=said.append) == shas[2]
    assert len(waits) == 1 and waits[0] <= instances.STABLE_QUIET_WAIT_SECONDS
    assert "c1" in said[0] and runs and backups


def test_a_stable_instance_that_stays_busy_refuses_and_changes_nothing(world, monkeypatch):
    repo, shas, serve, runs, backups = world
    serve(shas[2])
    work = ["execution e1 (running) in conversation c1", "job j1 in conversation c2"]
    _busy(monkeypatch, [work, work])
    with pytest.raises(instances.StableBusyError) as refused:
        instances.promote()
    assert "e1" in str(refused.value) and "j1" in str(refused.value)
    assert _git(repo, "rev-parse", "stable") == shas[0] and not runs and not backups


def test_work_ending_just_as_the_wait_expires_is_not_a_refusal(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[2])
    _busy(monkeypatch, [["a turn in conversation c1"], []])
    assert instances.promote() == shas[2] and runs


def test_a_refusal_for_another_reason_comes_before_the_wait(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[1])
    waits = _busy(monkeypatch, [["a turn in conversation c1"]])
    with pytest.raises(instances.NotServedError):
        instances.promote(shas[2])
    assert not waits


def test_a_turn_inside_stable_is_told_at_once_instead_of_waiting_on_itself(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[2])
    work = ["a turn in conversation c1"]
    waits = _busy(monkeypatch, [work, work])
    monkeypatch.setattr(instances, "_inside_worker", lambda pid: True)
    with pytest.raises(instances.StableBusyError, match="self_update"):
        instances.promote()
    assert waits[0] <= instances.STABLE_QUIET_WAIT_FROM_WORKER_SECONDS and not runs


def test_the_controller_skips_the_wait(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[2])
    monkeypatch.setattr(supervisor, "active_work", lambda: pytest.fail("the controller already waited"))
    assert instances.promote(shas[2], wait_for_stable=False) == shas[2] and runs


def test_an_unreadable_stable_store_refuses_without_a_traceback(world, monkeypatch):
    repo, shas, serve, runs, _ = world
    serve(shas[2])

    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(supervisor, "active_work", broken)
    with pytest.raises(instances.InstanceError, match="disk gone"):
        instances.promote()
    assert not runs


def test_stable_is_read_even_from_the_dev_profile(world, monkeypatch):
    from openprogram.state.paths import get_active_profile

    repo, shas, serve, *_ = world
    serve(shas[2])
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    seen = []
    monkeypatch.setattr(supervisor, "active_work", lambda: seen.append(get_active_profile()) or [])
    instances.promote()
    assert seen == [None] and get_active_profile() == "dev"


def test_rows_a_dead_worker_left_running_do_not_block_the_repair(world, monkeypatch):
    repo, shas, serve, runs, backups = world
    serve(shas[2])
    monkeypatch.setattr(instances, "_stable_worker_pid", lambda: None)
    monkeypatch.setattr(supervisor, "active_work", lambda: pytest.fail("no worker, nothing to interrupt"))
    assert instances.promote() == shas[2] and runs and backups


def test_a_lock_file_left_by_a_crash_is_not_a_live_worker(monkeypatch):
    from openprogram.operations.worker import lock

    monkeypatch.setattr(lock, "read_holder_pid", lambda: 4242)
    monkeypatch.setattr(lock, "is_held_by", lambda pid: False)
    assert instances._stable_worker_pid() is None
    monkeypatch.setattr(lock, "is_held_by", lambda pid: True)
    assert instances._stable_worker_pid() == 4242


def test_a_dev_deploy_during_the_wait_stops_the_promotion(world, monkeypatch):
    repo, shas, serve, runs, backups = world
    serve(shas[2])
    work = ["a turn in conversation c1"]
    _busy(monkeypatch, [work])
    monkeypatch.setattr(supervisor, "_wait_for_quiescence", lambda deadline: serve(shas[1]) or True)
    with pytest.raises(instances.NotServedError):
        instances.promote()
    assert _git(repo, "rev-parse", "stable") == shas[0] and not runs and not backups


def test_the_backup_is_of_stable_even_from_the_dev_profile(world, monkeypatch):
    import openprogram.operations.backup as backup
    from openprogram.state.paths import get_active_profile

    repo, shas, serve, *_ = world
    serve(shas[2])
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    seen = []
    monkeypatch.setattr(backup, "create_backup", lambda **kw: seen.append(get_active_profile()))
    instances.promote()
    assert seen == [None] and get_active_profile() == "dev"


def test_this_process_descends_from_its_ancestors_only():
    assert instances._inside_worker(os.getppid())
    assert not instances._inside_worker(None)
    assert not instances._inside_worker(2 ** 22 - 1)


def test_credentials_belong_to_the_active_profile(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.models.auth.store import AuthStore

    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    previous = paths.get_active_profile()
    paths.set_active_profile("dev")
    try:
        assert AuthStore().base_dir() == tmp_path / ".openprogram-dev" / "auth"
    finally:
        paths.set_active_profile(previous)


def test_copy_models_shares_keys_but_not_oauth(tmp_path, monkeypatch):
    import json
    from openprogram.models.auth.store import AuthStore
    from openprogram.models.auth.types import Credential, CredentialData

    monkeypatch.setattr(instances.Path, "home", lambda: tmp_path)
    stable = instances.stable_state_dir()
    source = AuthStore(root=stable, carry_over=False)
    for kind, value in (("oauth", "value-o"), ("api_key", "value-k")):
        source.add_credential(Credential(provider_id="acme", kind=kind,
                                         payload=CredentialData(kind=kind, auth_value=value)))
    (stable / "config.json").write_text(json.dumps(
        {"providers": {"acme": {"enabled": True}}, "last_used_model": {"provider": "acme"}, "ui": {"x": 1}}))
    (instances.dev_state_dir()).mkdir(parents=True)
    (instances.dev_state_dir() / "config.json").write_text(json.dumps({"ui": {"y": 2}}))

    result = instances.copy_models()

    assert result == {"copied": ["acme"], "oauth_not_copied": ["acme"]}
    copied = AuthStore(root=instances.dev_state_dir()).stored("acme").credentials
    assert [(c.kind, c.payload.auth_value) for c in copied] == [("api_key", "value-k")]
    assert instances.copy_models()["copied"] == []  # a second copy adds nothing
    config = json.loads((instances.dev_state_dir() / "config.json").read_text())
    assert config == {"ui": {"y": 2}, "providers": {"acme": {"enabled": True}},
                      "last_used_model": {"provider": "acme"}}


def test_only_the_newest_promote_backups_are_kept(tmp_path):
    names = [f"default-pre-promote-2026110{day}-120000.tar.gz" for day in range(1, 9)]
    for name in names:
        (tmp_path / name).write_text("x")
        (tmp_path / (name + ".lock")).write_text("")
    other = tmp_path / "default-20261101-120000.tar.gz"
    other.write_text("x")
    instances.prune_promote_backups(tmp_path, keep=5)
    left = sorted(path.name for path in tmp_path.glob("*.tar.gz"))
    assert left == sorted([other.name, *names[-5:]])
    assert sorted(path.name for path in tmp_path.glob("*.lock")) == sorted(name + ".lock" for name in names[-5:])
