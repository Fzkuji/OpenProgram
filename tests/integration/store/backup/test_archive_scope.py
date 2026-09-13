"""Backup archive scope and credential manifests."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

from tests.integration.store.backup.command_support import (
    profile as profile,
    _no_running_processes as _no_running_processes,
    _members,
    _archive_bytes,
    _seed_registered_secrets,
)


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
