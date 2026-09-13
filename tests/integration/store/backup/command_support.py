"""Fixtures and archive helpers for backup command tests."""
from __future__ import annotations
import io
import json
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
