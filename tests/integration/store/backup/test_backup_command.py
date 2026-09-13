"""Round-trip, scope, and safety tests for `openprogram backup`."""

from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest


@pytest.fixture()
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated fake state dir, with paths rerouted to it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)

    import openprogram.paths as paths

    monkeypatch.setattr(paths, "_migration_checked", True)
    monkeypatch.setattr(paths, "_root_mode_checked", set())

    state = home / ".openprogram"
    (state / "memory" / "topics").mkdir(parents=True)
    (state / "memory" / "core.md").write_text("remembered", encoding="utf-8")
    (state / "memory" / "topics" / "a.md").write_text("topic a", encoding="utf-8")
    (state / "sessions").mkdir()
    (state / "sessions" / "s1.json").write_text('{"id": "s1"}', encoding="utf-8")
    (state / "config.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (state / "config.json").chmod(0o600)
    (state / "bindings.json").write_text('{"discord": []}', encoding="utf-8")
    (state / "programs_meta.json").write_text('{"favorites": []}', encoding="utf-8")
    (state / "functions_meta.json").write_text("{}", encoding="utf-8")
    (state / "channels").mkdir()
    (state / "channels" / "discord.json").write_text("{}", encoding="utf-8")

    # Out-of-scope noise that must never land in an archive.
    (state / "cache" / "blobs").mkdir(parents=True)
    (state / "cache" / "blobs" / "big.bin").write_bytes(b"x" * 1024)
    (state / "logs").mkdir()
    (state / "logs" / "worker.log").write_text("noise", encoding="utf-8")
    (state / "trash").mkdir()
    (state / "trash" / "deleted.txt").write_text("gone", encoding="utf-8")
    (state / "worker.lock").write_text("", encoding="utf-8")
    (state / "worker.pid").write_text("1234", encoding="utf-8")
    (state / "worker.port").write_text("18100", encoding="utf-8")
    (state / "channels.log").write_text("noise", encoding="utf-8")
    (state / "auth" / "anthropic").mkdir(parents=True)
    (state / "auth" / "anthropic" / "default.json").write_text(
        '{"key": "secret"}', encoding="utf-8"
    )
    (state / "auth" / "anthropic" / "default.json").chmod(0o600)
    (state / "mcp_tokens").mkdir()
    (state / "mcp_tokens" / "t.json").write_text(
        '{"token": "secret"}', encoding="utf-8"
    )
    (state / "mcp_tokens" / "t.json").chmod(0o600)
    (state / "skills").mkdir()
    (state / "skills" / "node_modules").mkdir()
    (state / "skills" / "node_modules" / "junk.js").write_text("//", encoding="utf-8")
    (state / "skills" / "real.md").write_text("# skill", encoding="utf-8")
    return state


@pytest.fixture(autouse=True)
def _no_running_processes(monkeypatch: pytest.MonkeyPatch):
    """Default: nothing is running, so restore is allowed."""
    from openprogram.cli.commands import backup

    monkeypatch.setattr(backup, "_running_processes", lambda: [])


def _members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as tar:
        return tar.getnames()


def _archive_bytes(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as tar:
        result = {}
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            assert handle is not None
            result[member.name] = handle.read()
        return result


def _tar_with_files(path: Path, files: dict[str, bytes]) -> tarfile.TarFile:
    _write_restorable_archive(path, files)
    return tarfile.open(path, "r:gz")


def _write_restorable_archive(path: Path, files: dict[str, bytes]) -> Path:
    """Build an archive `restore_archive` accepts: members plus a manifest."""
    from openprogram.cli.commands.backup import _MANIFEST_NAME

    with tarfile.open(path, "w:gz") as tar:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(payload))
        manifest = json.dumps(
            {"format_version": 1, "credential_opt_in": True}
        ).encode()
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(manifest)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(manifest))
    return path


def _start_restore_paused_after_first_publish(
    state: Path, archive: Path, marker: Path
) -> subprocess.Popen:
    code = (
        "import sys,time; from pathlib import Path; "
        "from openprogram.cli.commands import backup as b; "
        "state,archive,marker=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]); "
        "real=b._publish_restored; count=[0]; "
        "exec(\"def publish(target,payload,*,root):\\n count[0]+=1\\n real(target,payload,root=root)\\n if count[0]==1:\\n  marker.write_text('paused')\\n  while True: time.sleep(1)\"); "
        "b._publish_restored=publish; b.restore_archive(archive,state)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(state), str(archive), str(marker)]
    )
    deadline = time.time() + 10
    while not marker.exists() and process.poll() is None and time.time() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    return process


def _seed_registered_secrets(profile: Path) -> dict[str, bytes]:
    secrets = {
        "config_api_keys": b"config-secret-101",
        "auth_store": b"auth-secret-202",
        "profile_auth_store": b"profile-auth-secret-303",
        "profile_env": b"profile-env-secret-404",
        "channel_credentials": b"channel-secret-505",
        "mcp_env": b"mcp-env-secret-606",
        "mcp_header": b"mcp-header-secret-707",
        "mcp_bearer": b"mcp-bearer-secret-808",
        "mcp_oauth": b"mcp-oauth-secret-909",
        "mcp_tokens": b"mcp-token-secret-010",
        "web_runtime_token": b"web-runtime-secret-111",
        "pairing_code": b"PAIRCODE222",
    }
    (profile / "config.json").write_text(
        json.dumps(
            {
                "theme": "dark",
                "api_keys": {"OPENAI_API_KEY": secrets["config_api_keys"].decode()},
            }
        ),
        encoding="utf-8",
    )
    (profile / "auth" / "openai").mkdir(parents=True, exist_ok=True)
    (profile / "auth" / "openai" / "default.json").write_text(
        json.dumps({"credentials": [{"api_key": secrets["auth_store"].decode()}]}),
        encoding="utf-8",
    )
    account = profile / "profiles" / "work"
    (account / "auth" / "openai").mkdir(parents=True)
    (account / "account.json").write_text('{"name":"work"}', encoding="utf-8")
    (account / "auth" / "openai" / "default.json").write_text(
        json.dumps(
            {"credentials": [{"api_key": secrets["profile_auth_store"].decode()}]}
        ),
        encoding="utf-8",
    )
    (account / ".env").write_bytes(b"API_KEY=" + secrets["profile_env"] + b"\n")
    channel = profile / "channels" / "slack" / "accounts" / "default"
    channel.mkdir(parents=True)
    (channel / "account.json").write_text('{"name":"default"}', encoding="utf-8")
    (channel / "credentials.json").write_text(
        json.dumps(
            {
                "bot_token": secrets["channel_credentials"].decode(),
            }
        ),
        encoding="utf-8",
    )
    (channel / "access.json").write_text(
        json.dumps(
            {
                "policy": "pairing",
                "allowlist": {"approved": {"display": "Alice"}},
                "pending": {"waiting": {"code": secrets["pairing_code"].decode()}},
            }
        ),
        encoding="utf-8",
    )
    (profile / "mcp_servers.json").write_text(
        json.dumps(
            {
                "roots": [{"uri": "file:///workspace"}],
                "servers": {
                    "local": {
                        "type": "local",
                        "env": {"TOKEN": secrets["mcp_env"].decode()},
                    },
                    "remote": {
                        "type": "http",
                        "headers": {"X-Key": secrets["mcp_header"].decode()},
                        "auth": {
                            "kind": "bearer",
                            "token": secrets["mcp_bearer"].decode(),
                        },
                    },
                    "oauth": {
                        "type": "http",
                        "auth": {
                            "kind": "oauth",
                            "client_id": "public-id",
                            "client_secret": secrets["mcp_oauth"].decode(),
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (profile / "mcp_tokens" / "github.json").write_text(
        json.dumps(
            {
                "tokens": {"access_token": secrets["mcp_tokens"].decode()},
            }
        ),
        encoding="utf-8",
    )
    (profile / "web").mkdir()
    (profile / "web" / "token").write_bytes(secrets["web_runtime_token"])
    for private_path in (
        profile / "config.json",
        profile / "auth" / "openai" / "default.json",
        account / "auth" / "openai" / "default.json",
        account / ".env",
        channel / "credentials.json",
        channel / "access.json",
        profile / "mcp_servers.json",
        profile / "mcp_tokens" / "github.json",
        profile / "web" / "token",
    ):
        private_path.chmod(0o600)
    return secrets


def test_create_captures_scope_and_excludes_noise(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    archive = create_backup()
    names = _members(archive)

    assert "memory/core.md" in names
    assert "memory/topics/a.md" in names
    assert "sessions/s1.json" in names
    assert "config.json" in names
    assert "bindings.json" in names
    assert "programs_meta.json" in names
    assert "functions_meta.json" in names
    assert "channels/discord.json" in names
    assert "skills/real.md" in names

    # Excluded by scope or by name/suffix rules.
    for unwanted in (
        "cache",
        "logs",
        "trash",
        "worker.lock",
        "worker.pid",
        "worker.port",
        "channels.log",
    ):
        assert not any(n == unwanted or n.startswith(unwanted + "/") for n in names), (
            f"{unwanted} leaked into archive"
        )
    assert not any("node_modules" in n for n in names)


def test_credentials_excluded_by_default_and_opt_in_works(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    default_names = _members(create_backup())
    assert not any(n.startswith("auth") for n in default_names)
    assert not any(n.startswith("mcp_tokens") for n in default_names)

    opt_in_names = _members(create_backup(include_credentials=True))
    assert "auth/anthropic/default.json" in opt_in_names
    assert "mcp_tokens/t.json" in opt_in_names


def test_default_backup_contains_no_registered_raw_secret(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    secrets = _seed_registered_secrets(profile)
    archived = _archive_bytes(create_backup())
    payload = b"\n".join(archived.values())

    assert all(secret not in payload for secret in secrets.values())
    assert json.loads(archived["config.json"]) == {"theme": "dark"}
    mcp = json.loads(archived["mcp_servers.json"])
    assert mcp["roots"] == [{"uri": "file:///workspace"}]
    assert mcp["servers"]["local"] == {"type": "local"}
    assert mcp["servers"]["remote"] == {"type": "http", "auth": {"kind": "bearer"}}
    assert mcp["servers"]["oauth"]["auth"] == {
        "kind": "oauth",
        "client_id": "public-id",
    }
    access = json.loads(archived["channels/slack/accounts/default/access.json"])
    assert access == {
        "policy": "pairing",
        "allowlist": {"approved": {"display": "Alice"}},
    }
    assert "channels/slack/accounts/default/credentials.json" not in archived
    assert "profiles/work/.env" not in archived
    assert "profiles/work/auth/openai/default.json" not in archived

    manifest = json.loads(archived["backup-manifest.json"])
    assert manifest["format_version"] == 1
    assert manifest["credentials_included"] is False
    assert manifest["included_secret_kinds"] == []
    assert set(manifest["excluded_secret_kinds"]) == {
        "auth_store",
        "profile_auth_store",
        "profile_env",
        "channel_credentials",
        "mcp_tokens",
    }
    assert set(manifest["redacted_secret_kinds"]) == {
        "config_api_keys",
        "mcp_server_secrets",
        "channel_pairing_codes",
    }
    assert set(manifest["credential_policy"]["never_backed_up_secret_kinds"]) == {
        "channel_pairing_codes",
        "web_runtime_token",
    }


def test_opt_in_backup_contains_exactly_allowed_persistent_secrets(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    secrets = _seed_registered_secrets(profile)
    archived = _archive_bytes(create_backup(include_credentials=True))
    payload = b"\n".join(archived.values())

    expected = {
        secrets[name]
        for name in (
            "config_api_keys",
            "auth_store",
            "profile_auth_store",
            "profile_env",
            "channel_credentials",
            "mcp_env",
            "mcp_header",
            "mcp_bearer",
            "mcp_oauth",
            "mcp_tokens",
        )
    }
    assert {secret for secret in secrets.values() if secret in payload} == expected
    assert secrets["web_runtime_token"] not in payload
    assert secrets["pairing_code"] not in payload
    manifest = json.loads(archived["backup-manifest.json"])
    assert manifest["credentials_included"] is True
    assert set(manifest["included_secret_kinds"]) == {
        "config_api_keys",
        "auth_store",
        "profile_auth_store",
        "profile_env",
        "channel_credentials",
        "mcp_server_secrets",
        "mcp_tokens",
    }


@pytest.mark.parametrize("include_credentials", [False, True])
def test_profile_allowlist_and_secret_writer_temps_never_leak(
    profile: Path,
    include_credentials: bool,
) -> None:
    from openprogram.cli.commands.backup import create_backup

    _seed_registered_secrets(profile)
    account = profile / "profiles" / "work"
    (account / "metadata.json").write_text('{"display_name":"Work"}')
    leaks = {
        account / "home" / ".codex" / "auth.json": b"nested-home-secret",
        account / ".env.tmp": b"dotenv-temp-secret",
        account / "auth" / "openai" / "default.json.tmp": b"auth-temp-secret",
        profile
        / "channels"
        / "slack"
        / "accounts"
        / "default"
        / "credentials.json.tmp": b"channel-temp-secret",
        profile
        / "channels"
        / "slack"
        / "accounts"
        / "default"
        / "access-random.json.tmp": b"pairing-temp-secret",
        profile / "auth" / "openai" / "orphan.json.tmp": b"root-auth-temp-secret",
        profile / "mcp_tokens" / "github.json.tmp": b"mcp-token-temp-secret",
    }
    for path, payload in leaks.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    archived = _archive_bytes(create_backup(include_credentials=include_credentials))
    combined = b"\n".join(archived.values())

    assert all(secret not in combined for secret in leaks.values())
    assert "profiles/work/metadata.json" in archived
    assert not any(name.startswith("profiles/work/home/") for name in archived)
    assert not any(name.endswith(".tmp") for name in archived)
    manifest = json.loads(archived["backup-manifest.json"])
    if include_credentials:
        assert set(manifest["included_secret_kinds"]) == {
            "config_api_keys",
            "auth_store",
            "profile_auth_store",
            "profile_env",
            "channel_credentials",
            "mcp_server_secrets",
            "mcp_tokens",
        }
    else:
        assert set(manifest["excluded_secret_kinds"]) == {
            "auth_store",
            "profile_auth_store",
            "profile_env",
            "channel_credentials",
            "mcp_tokens",
        }


def test_manifest_is_empty_when_no_inventory_member_exists(profile: Path) -> None:
    from openprogram.cli.commands.backup import create_backup

    (profile / "config.json").unlink()
    (profile / "auth" / "anthropic" / "default.json").unlink()
    (profile / "mcp_tokens" / "t.json").unlink()
    manifest = json.loads(_archive_bytes(create_backup())["backup-manifest.json"])

    assert manifest["included_secret_kinds"] == []
    assert manifest["redacted_secret_kinds"] == []
    assert manifest["excluded_secret_kinds"] == []
    assert set(manifest["credential_policy"]["never_backed_up_secret_kinds"]) == {
        "channel_pairing_codes",
        "web_runtime_token",
    }


def test_manifest_reports_only_present_secret_fields(profile: Path) -> None:
    from openprogram.cli.commands.backup import create_backup

    (profile / "config.json").write_text(
        '{"theme":"dark","api_keys":{"OPENAI_API_KEY":"present"}}',
        encoding="utf-8",
    )
    (profile / "auth" / "anthropic" / "default.json").unlink()
    (profile / "mcp_tokens" / "t.json").unlink()
    default = json.loads(_archive_bytes(create_backup())["backup-manifest.json"])
    opted_in = json.loads(
        _archive_bytes(create_backup(include_credentials=True))["backup-manifest.json"]
    )

    assert default["redacted_secret_kinds"] == ["config_api_keys"]
    assert default["excluded_secret_kinds"] == []
    assert opted_in["included_secret_kinds"] == ["config_api_keys"]
    assert opted_in["redacted_secret_kinds"] == []


def test_manifest_marks_malformed_mixed_secret_as_actually_excluded(
    profile: Path,
) -> None:
    from openprogram.cli.commands.backup import create_backup

    (profile / "config.json").write_bytes(b'{"api_keys":"unknown-secret"')
    (profile / "auth" / "anthropic" / "default.json").unlink()
    (profile / "mcp_tokens" / "t.json").unlink()
    archived = _archive_bytes(create_backup())
    manifest = json.loads(archived["backup-manifest.json"])

    assert "config.json" not in archived
    assert manifest["redacted_secret_kinds"] == []
    assert manifest["excluded_secret_kinds"] == ["config_api_keys"]


def test_default_restore_preserves_local_secrets(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    _seed_registered_secrets(profile)
    archive = create_backup()
    local_config_secret = "local-config-secret"
    local_mcp_secret = "local-mcp-secret"
    local_pairing_code = "LOCALPAIR"
    whole_file_updates = {
        profile / "auth" / "openai" / "default.json": b"local-auth-secret",
        profile / "profiles" / "work" / "auth" / "openai" / "default.json": (
            b"local-profile-auth-secret"
        ),
        profile / "profiles" / "work" / ".env": b"API_KEY=local-env-secret\n",
        profile
        / "channels"
        / "slack"
        / "accounts"
        / "default"
        / "credentials.json": b'{"bot_token":"local-channel-secret"}',
        profile / "mcp_tokens" / "github.json": b'{"token":"local-mcp-token"}',
    }
    for path, content in whole_file_updates.items():
        path.write_bytes(content)
    (profile / "config.json").write_text(
        json.dumps(
            {
                "theme": "light",
                "api_keys": {"OPENAI_API_KEY": local_config_secret},
            }
        ),
        encoding="utf-8",
    )
    mcp_path = profile / "mcp_servers.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["servers"]["local"]["env"] = {"TOKEN": local_mcp_secret}
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    access_path = (
        profile / "channels" / "slack" / "accounts" / "default" / "access.json"
    )
    access = json.loads(access_path.read_text(encoding="utf-8"))
    access["pending"] = {"local": {"code": local_pairing_code}}
    access_path.write_text(json.dumps(access), encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    restored_config = json.loads((profile / "config.json").read_text(encoding="utf-8"))
    assert restored_config == {
        "theme": "dark",
        "api_keys": {"OPENAI_API_KEY": local_config_secret},
    }
    restored_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert restored_mcp["servers"]["local"]["env"] == {"TOKEN": local_mcp_secret}
    restored_access = json.loads(access_path.read_text(encoding="utf-8"))
    assert restored_access["pending"] == {"local": {"code": local_pairing_code}}
    for path, content in whole_file_updates.items():
        assert path.read_bytes() == content


def test_opt_in_restore_replaces_persistent_secrets(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    secrets = _seed_registered_secrets(profile)
    archive = create_backup(include_credentials=True)
    auth_path = profile / "auth" / "openai" / "default.json"
    env_path = profile / "profiles" / "work" / ".env"
    auth_path.write_text('{"credentials":[]}', encoding="utf-8")
    env_path.write_text("API_KEY=changed\n", encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    assert secrets["auth_store"] in auth_path.read_bytes()
    assert secrets["profile_env"] in env_path.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
def test_restore_inventory_files_are_atomically_published_owner_only(
    profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.auth import credentials as credential_files
    from openprogram.cli.commands.backup import restore_archive

    config = profile / "config.json"
    config.write_text(
        '{"theme":"local","api_keys":{"OPENAI_API_KEY":"local-secret"}}',
        encoding="utf-8",
    )
    config.chmod(0o600)
    auth = profile / "auth" / "openai" / "default.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_bytes(b'{"credentials":"old-auth"}')
    auth.chmod(0o600)
    archive = tmp_path / "restore.tar.gz"
    mcp_token = profile / "mcp_tokens" / "restored.json"
    observed: dict[str, tuple[int, bytes | None]] = {}
    real_replace = os.replace

    def inspect_replace(source, destination) -> None:
        destination = Path(destination)
        if destination in {config, auth, mcp_token}:
            observed[destination.name] = (
                stat.S_IMODE(Path(source).stat().st_mode),
                destination.read_bytes() if destination.exists() else None,
            )
        real_replace(source, destination)

    monkeypatch.setattr(credential_files.os, "replace", inspect_replace)
    _write_restorable_archive(
        archive,
        {
            "config.json": b'{"theme":"archived"}',
            "auth/openai/default.json": b'{"credentials":"archived-auth"}',
            "mcp_tokens/restored.json": b'{"token":"archived-mcp-token"}',
        },
    )
    restore_archive(archive, profile)

    assert json.loads(config.read_text()) == {
        "theme": "archived",
        "api_keys": {"OPENAI_API_KEY": "local-secret"},
    }
    assert auth.read_bytes() == b'{"credentials":"archived-auth"}'
    assert mcp_token.read_bytes() == b'{"token":"archived-mcp-token"}'
    assert observed == {
        "config.json": (
            0o600,
            b'{"theme":"local","api_keys":{"OPENAI_API_KEY":"local-secret"}}',
        ),
        "default.json": (0o600, b'{"credentials":"old-auth"}'),
        "restored.json": (0o600, None),
    }
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert stat.S_IMODE(auth.stat().st_mode) == 0o600
        assert stat.S_IMODE(mcp_token.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_restore_inventory_failure_preserves_old_file_and_cleans_temp(
    profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from openprogram.auth import credentials as credential_files
    from openprogram.cli.commands.backup import restore_archive

    target = profile / "auth" / "openai" / "default.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"credentials":"old-auth"}')
    target.chmod(0o600)
    archive = tmp_path / "restore-failure.tar.gz"
    real_fdopen = os.fdopen

    if failure == "write":

        class FailingWrite:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def write(self, _payload):
                raise OSError("write failed")

            def __getattr__(self, name):
                return getattr(self.handle, name)

        monkeypatch.setattr(
            credential_files.os,
            "fdopen",
            lambda fd, mode: FailingWrite(real_fdopen(fd, mode)),
        )
    elif failure == "fsync":
        monkeypatch.setattr(
            credential_files.os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")),
        )
    else:
        monkeypatch.setattr(
            credential_files.os,
            "replace",
            lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
        )

    _write_restorable_archive(
        archive,
        {"auth/openai/default.json": b'{"credentials":"archived-auth"}'},
    )
    from openprogram.cli.commands.backup import RestoreRollbackCompletedError

    with pytest.raises(RestoreRollbackCompletedError) as exc:
        restore_archive(archive, profile)

    assert isinstance(exc.value.__cause__, credential_files.PrivateAtomicWriteError)
    assert exc.value.__cause__.code == failure
    assert exc.value.__cause__.committed is False
    assert target.read_bytes() == b'{"credentials":"old-auth"}'
    assert list(target.parent.glob(".default.json.*.tmp")) == []


def test_backup_cli_warning_and_manifest_report_same_scope(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_create

    _seed_registered_secrets(profile)
    assert _cmd_backup_create(include_credentials=True) == 0
    output = capsys.readouterr().out
    assert "plaintext credentials" in output
    assert "Web runtime tokens and pending pairing codes are never included" in output


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract; Windows uses ACLs")
def test_archive_is_owner_only_and_named_for_profile(profile: Path):
    from openprogram.cli.commands.backup import create_backup

    archive = create_backup()
    mode = stat.S_IMODE(archive.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert archive.parent == profile / "backups"
    assert archive.name.startswith("default-")
    assert archive.name.endswith(".tar.gz")


def test_create_and_restore_round_trip(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_create, _cmd_backup_restore

    assert _cmd_backup_create() == 0
    out = capsys.readouterr().out
    assert "size:" in out and "content:" in out
    assert "credentials excluded" in out

    archive = next((profile / "backups").glob("default-*.tar.gz"))

    # Mutate state, then restore it away.
    (profile / "memory" / "core.md").write_text("clobbered", encoding="utf-8")
    (profile / "sessions" / "s1.json").unlink()

    assert _cmd_backup_restore(archive.name, yes=True) == 0
    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "remembered"
    assert (profile / "sessions" / "s1.json").exists()


def test_restore_snapshots_current_state_first(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    archive = create_backup()
    (profile / "memory" / "core.md").write_text("about to be lost", encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    safety = list((profile / "backups").glob("default-pre-restore-*.tar.gz"))
    assert len(safety) == 1, "restore must snapshot current state first"
    with tarfile.open(safety[0], "r:gz") as tar:
        member = tar.extractfile("memory/core.md")
        assert member is not None
        assert member.read().decode() == "about to be lost"


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_create_backup_is_busy_during_restore_publication(
    profile: Path, tmp_path: Path
) -> None:
    from openprogram.cli.commands.backup import (
        RestoreBusyError,
        create_backup,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    before = set((profile / "backups").glob("*.tar.gz")) if (profile / "backups").exists() else set()
    try:
        with pytest.raises(RestoreBusyError):
            create_backup()
        assert set((profile / "backups").glob("*.tar.gz")) == before
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_backup_create_cli_reports_busy_without_archive(
    profile: Path, tmp_path: Path, capsys
) -> None:
    from openprogram.cli.commands.backup import (
        _cmd_backup_create,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    try:
        assert _cmd_backup_create() == 1
        assert "another restore is already in progress" in capsys.readouterr().err
        assert not list((profile / "backups").glob("*.tar.gz"))
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock and SIGKILL semantics")
def test_busy_restore_cli_does_not_create_safety_snapshot(
    profile: Path, tmp_path: Path
) -> None:
    from openprogram.cli.commands.backup import (
        _cmd_backup_restore,
        recover_interrupted_restore,
    )

    archive = _write_restorable_archive(
        tmp_path / "incoming.tar.gz",
        {
            "memory/core.md": b"new-memory",
            "sessions/s1.json": b'{"generation": "new"}',
        },
    )
    marker = tmp_path / "restore-paused"
    process = _start_restore_paused_after_first_publish(profile, archive, marker)
    try:
        assert _cmd_backup_restore(str(archive), yes=True) == 1
        assert not list((profile / "backups").glob("*pre-restore*.tar.gz"))
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        recover_interrupted_restore(profile)


def test_restore_refuses_while_worker_running(profile: Path, monkeypatch, capsys):
    from openprogram.cli.commands import backup

    archive = backup.create_backup()
    monkeypatch.setattr(backup, "_running_processes", lambda: ["worker (PID 42)"])

    assert backup._cmd_backup_restore(archive.name, yes=True) == 1
    err = capsys.readouterr().err
    assert "refusing to restore" in err
    assert "openprogram stop" in err
    # And no safety backup was written, because we never got that far.
    assert not list((profile / "backups").glob("*pre-restore*"))


def test_dry_run_changes_nothing(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    archive = create_backup()
    (profile / "memory" / "core.md").write_text("untouched", encoding="utf-8")
    before = sorted(p.name for p in (profile / "backups").iterdir())

    assert _cmd_backup_restore(archive.name, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "overwrite" in out
    assert "memory" in out

    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "untouched"
    assert sorted(p.name for p in (profile / "backups").iterdir()) == before


def test_restore_declined_at_prompt_aborts(profile: Path, monkeypatch):
    from openprogram.cli.commands import backup

    archive = backup.create_backup()
    (profile / "memory" / "core.md").write_text("kept", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _="": "n")

    assert backup._cmd_backup_restore(archive.name) == 1
    assert (profile / "memory" / "core.md").read_text(encoding="utf-8") == "kept"


def test_list_shows_size_and_contents(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_list, create_backup

    create_backup()
    assert _cmd_backup_list() == 0
    out = capsys.readouterr().out
    assert "default-" in out
    assert "memory" in out
    assert "KB" in out or "B" in out


def test_list_is_empty_without_backups(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_list

    assert _cmd_backup_list() == 0
    assert "No backups" in capsys.readouterr().out


def test_prune_keeps_newest_n(profile: Path):
    import os
    import time

    from openprogram.cli.commands.backup import _cmd_backup_prune, create_backup

    made = []
    for i in range(4):
        path = create_backup(label=f"n{i}")
        # Distinct mtimes so ordering is deterministic without sleeping.
        os.utime(path, (time.time() + i, time.time() + i))
        made.append(path)

    assert _cmd_backup_prune(keep=2) == 0
    left = sorted(p.name for p in (profile / "backups").glob("*.tar.gz"))
    assert len(left) == 2
    assert made[-1].name in left and made[-2].name in left


def test_prune_rejects_zero(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_prune

    assert _cmd_backup_prune(keep=0) == 1
    assert "at least 1" in capsys.readouterr().err


def test_interrupted_create_leaves_no_visible_archive(profile: Path, monkeypatch):
    """A create that dies mid-write must not leave something `list` shows."""
    import tarfile as _tarfile

    from openprogram.cli.commands import backup

    real_add = _tarfile.TarFile.add

    def explode(self, name, *args, **kwargs):
        real_add(self, name, *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(_tarfile.TarFile, "add", explode)
    with pytest.raises(OSError):
        backup.create_backup()

    assert list((profile / "backups").glob("*.tar.gz")) == []
    assert list((profile / "backups").glob("*.partial")) == []


def test_restore_unknown_name_errors(profile: Path, capsys):
    from openprogram.cli.commands.backup import _cmd_backup_restore

    assert _cmd_backup_restore("nope.tar.gz", yes=True) == 1
    assert "no such backup" in capsys.readouterr().err


def test_named_profile_is_isolated(profile: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.cli.commands.backup import backups_dir, create_backup

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "alpha")
    alt = Path.home() / ".openprogram-alpha"
    (alt / "memory").mkdir(parents=True)
    (alt / "memory" / "core.md").write_text("alpha memory", encoding="utf-8")

    archive = create_backup()
    assert archive.parent == alt / "backups"
    assert archive.name.startswith("alpha-")
    assert backups_dir() == alt / "backups"
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("memory/core.md")
        assert member is not None
        assert member.read().decode() == "alpha memory"


def test_cli_registers_backup_verbs():
    from openprogram.cli import build_parser

    parser = build_parser()
    for verb in ("create", "list", "restore", "prune"):
        args = parser.parse_args(
            ["backup", verb] + (["x"] if verb == "restore" else [])
        )
        assert args.command == "backup"
        assert args.backup_verb == verb

    args = parser.parse_args(["backup", "create", "--include-credentials"])
    assert args.include_credentials is True
    args = parser.parse_args(["backup", "prune", "--keep", "3"])
    assert args.keep == 3
    args = parser.parse_args(["backup", "restore", "x", "--dry-run"])
    assert args.dry_run is True
