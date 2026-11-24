"""The development instance builds from `dev`; the official App is only read."""
import plistlib
import subprocess

import pytest

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
    home = tmp_path / "home"
    monkeypatch.setattr(instances.Path, "home", lambda: home)
    monkeypatch.setenv("OPENPROGRAM_SOURCE_REPO", str(repo))
    monkeypatch.setattr(instances, "healthy", lambda port: port == instances.DEV_PORT)
    runs, checked_out = [], []
    monkeypatch.setattr(instances, "_managed_worktree",
                        lambda repo, path, rev: checked_out.append((path, rev)) or path)
    monkeypatch.setattr(instances, "_run", lambda script, cwd: runs.append((script.name, cwd)))

    def serve(sha):
        target = instances.dev_runtime_dir()
        target.mkdir(parents=True, exist_ok=True)
        (target / "source-revision").write_text(sha + "\n")

    return repo, shas, serve, runs, checked_out


def test_deploy_builds_the_dev_tip_in_its_own_worktree(world):
    _, shas, _, runs, checked_out = world
    assert instances.deploy_dev() == shas[2]
    source = instances.dev_state_dir() / "source"
    assert checked_out == [(source, shas[2])]
    assert runs == [("refresh-dev-instance.sh", source)]


def test_deploy_takes_a_named_commit_and_refuses_an_unknown_one(world):
    _, shas, _, runs, _ = world
    assert instances.deploy_dev(shas[0]) == shas[0]
    with pytest.raises(instances.InstanceError, match="unknown ref"):
        instances.deploy_dev("no-such-ref")
    assert len(runs) == 1


def test_status_lists_the_dev_commits_not_deployed_yet(world):
    _, shas, serve, _, _ = world
    serve(shas[0])
    value = instances.status()
    assert value["dev"]["deployed_revision"] == shas[0]
    assert value["dev"]["branch_revision"] == shas[2]
    assert [item["sha"] for item in value["undeployed"]] == [shas[2], shas[1]]
    assert value["dev"]["healthy"] is True
    serve(shas[2])
    assert instances.status()["undeployed"] == []


def test_status_reads_the_official_app_without_a_branch(world, monkeypatch, tmp_path):
    app = tmp_path / "OpenProgram.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": "1.4.0"}, stream)
    monkeypatch.setattr(instances, "APP_PATH", app)
    stable = instances.status()["stable"]
    assert stable == {"app": str(app), "version": "1.4.0", "port": instances.STABLE_PORT, "healthy": False}
    monkeypatch.setattr(instances, "APP_PATH", tmp_path / "missing.app")
    assert instances.status()["stable"]["version"] is None


def test_the_source_checkout_is_recorded_in_the_development_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(instances.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OPENPROGRAM_SOURCE_REPO", raising=False)
    with pytest.raises(instances.InstanceError, match="no source checkout is known"):
        instances.source_repo()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    instances.dev_state_dir().mkdir(parents=True)
    (instances.dev_state_dir() / instances.SOURCE_REPO_FILE).write_text(f"{repo}\n")
    assert instances.source_repo() == repo
    assert not instances.stable_state_dir().exists()


def test_the_deploy_script_records_the_checkout_beside_the_development_runtime():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[3] / "scripts/local-instances/refresh-dev-instance.sh").read_text()
    assert '> "$state_dir/source-repo"' in script
    assert "$HOME/.openprogram/" not in script


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
    copied = AuthStore(root=instances.dev_state_dir()).get("acme").credentials
    assert [(c.kind, c.payload.auth_value) for c in copied] == [("api_key", "value-k")]
    assert instances.copy_models()["copied"] == []  # a second copy adds nothing
    config = json.loads((instances.dev_state_dir() / "config.json").read_text())
    assert config == {"ui": {"y": 2}, "providers": {"acme": {"enabled": True}},
                      "last_used_model": {"provider": "acme"}}
