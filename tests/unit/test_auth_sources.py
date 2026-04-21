"""Unit tests for auth.sources — external credential adoption.

Every source gets:
  * happy-path import returns a well-formed Credential
  * missing-file / missing-env returns []
  * corrupt-file returns []
  * removal_steps() yields at least one step with the right metadata

Integration with the AuthStore is exercised elsewhere; these tests keep
to the source-level contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.auth.sources import (
    ClaudeCodeSource,
    CodexCliSource,
    EnvApiKeySource,
    GhCliSource,
    QwenCliSource,
)
from openprogram.auth.sources.gh_cli import _parse_hosts_yml


# ---- EnvApiKeySource ------------------------------------------------------

def test_env_source_returns_credential_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TEST_KEY", "sk-xyz")
    src = EnvApiKeySource(provider_id="openai", env_var="OPEN_TEST_KEY")
    creds = src.try_import(tmp_path)
    assert len(creds) == 1
    c = creds[0]
    assert c.provider_id == "openai"
    assert c.kind == "api_key"
    assert c.payload.api_key == "sk-xyz"
    assert c.metadata["env_var"] == "OPEN_TEST_KEY"
    assert c.read_only is True


def test_env_source_strips_bearer_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TEST_KEY", "Bearer  sk-zzz")
    src = EnvApiKeySource(provider_id="openai", env_var="OPEN_TEST_KEY")
    creds = src.try_import(tmp_path)
    assert creds[0].payload.api_key == "sk-zzz"


def test_env_source_returns_empty_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_TEST_KEY_MISSING", raising=False)
    src = EnvApiKeySource(provider_id="openai", env_var="OPEN_TEST_KEY_MISSING")
    assert src.try_import(tmp_path) == []


def test_env_source_returns_empty_when_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TEST_KEY", "   ")
    src = EnvApiKeySource(provider_id="openai", env_var="OPEN_TEST_KEY")
    assert src.try_import(tmp_path) == []


def test_env_source_removal_step_is_instructional(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_TEST_KEY", "k")
    src = EnvApiKeySource(provider_id="openai", env_var="OPEN_TEST_KEY")
    [cred] = src.try_import(tmp_path)
    [step] = src.removal_steps(cred)
    assert step.executable is False
    assert step.kind == "env"
    assert step.target == "OPEN_TEST_KEY"


def test_env_source_id_encodes_var_name():
    src = EnvApiKeySource(provider_id="openai", env_var="FOO_KEY")
    assert src.source_id == "env:FOO_KEY"


# ---- CodexCliSource -------------------------------------------------------

def test_codex_source_imports_from_file(tmp_path: Path):
    path = tmp_path / "codex" / "auth.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "tokens": {
            "access_token": "A", "refresh_token": "R", "account_id": "acc_1",
        }
    }))
    src = CodexCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path)
    assert cred.kind == "cli_delegated"
    assert cred.payload.store_path == str(path)
    assert cred.payload.access_key_path == ["tokens", "access_token"]
    assert cred.payload.expires_key_path == []
    assert cred.metadata["account_id"] == "acc_1"
    assert cred.read_only is True


def test_codex_source_missing_file_returns_empty(tmp_path):
    src = CodexCliSource(override_path=str(tmp_path / "nope.json"))
    assert src.try_import(tmp_path) == []


def test_codex_source_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json")
    src = CodexCliSource(override_path=str(path))
    assert src.try_import(tmp_path) == []


def test_codex_source_empty_tokens_returns_empty(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"tokens": {}}))
    src = CodexCliSource(override_path=str(path))
    assert src.try_import(tmp_path) == []


def test_codex_source_removal_does_not_delete_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": "A", "refresh_token": "R"}}))
    src = CodexCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path)
    steps = src.removal_steps(cred)
    assert len(steps) == 1
    assert steps[0].executable is False
    assert steps[0].kind == "external_cli"
    assert "codex logout" in steps[0].description


# ---- ClaudeCodeSource -----------------------------------------------------

def test_claude_source_imports_oauth_shape(tmp_path: Path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "ACC",
            "refreshToken": "REF",
            "expiresAt": 1712345678901,
            "scopes": ["user:inference"],
            "subscriptionType": "pro",
        }
    }))
    src = ClaudeCodeSource(override_path=str(path))
    [cred] = src.try_import(tmp_path)
    assert cred.kind == "cli_delegated"
    assert cred.payload.access_key_path == ["claudeAiOauth", "accessToken"]
    assert cred.payload.expires_key_path == ["claudeAiOauth", "expiresAt"]
    assert cred.metadata["subscription_type"] == "pro"
    assert cred.metadata["scopes"] == ["user:inference"]


def test_claude_source_missing_token_returns_empty(tmp_path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {}}))
    src = ClaudeCodeSource(override_path=str(path))
    assert src.try_import(tmp_path) == []


def test_claude_source_missing_file_returns_empty(tmp_path):
    src = ClaudeCodeSource(override_path=str(tmp_path / "nope.json"))
    assert src.try_import(tmp_path) == []


# ---- QwenCliSource --------------------------------------------------------

def test_qwen_source_imports_fields(tmp_path: Path):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({
        "access_token": "A", "refresh_token": "R",
        "expiry_date": 1712345678901,
        "resource_url": "portal.qwen.ai",
        "token_type": "Bearer",
    }))
    src = QwenCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path)
    assert cred.kind == "cli_delegated"
    assert cred.payload.expires_key_path == ["expiry_date"]
    assert cred.metadata["resource_url"] == "portal.qwen.ai"
    assert cred.read_only is True


def test_qwen_source_corrupt_returns_empty(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text("oops")
    src = QwenCliSource(override_path=str(path))
    assert src.try_import(tmp_path) == []


# ---- GhCliSource ----------------------------------------------------------

GH_HOSTS_YML = """\
github.com:
    user: alice
    oauth_token: gho_abc123
    git_protocol: ssh
    users:
        alice:
            oauth_token: gho_abc123
github.enterprise.example:
    user: bob
    oauth_token: gho_zzz999
    git_protocol: https
"""


def test_gh_source_parses_two_hosts(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path))
    creds = src.try_import(tmp_path)
    assert len(creds) == 2
    by_host = {c.metadata["host"]: c for c in creds}
    assert by_host["github.com"].payload.api_key == "gho_abc123"
    assert by_host["github.com"].profile_id == "default"
    assert by_host["github.enterprise.example"].payload.api_key == "gho_zzz999"
    assert by_host["github.enterprise.example"].profile_id == "github.enterprise.example"


def test_gh_source_filter_by_host(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path), hosts=["github.com"])
    [cred] = src.try_import(tmp_path)
    assert cred.metadata["host"] == "github.com"


def test_gh_source_missing_file(tmp_path):
    src = GhCliSource(override_path=str(tmp_path / "nope.yml"))
    assert src.try_import(tmp_path) == []


def test_gh_source_removal_has_both_instructional_and_executable(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path, )[:1]
    steps = src.removal_steps(cred)
    assert len(steps) == 2
    kinds = {s.kind for s in steps}
    assert kinds == {"external_cli", "file"}
    assert any(s.executable for s in steps)
    assert any(not s.executable for s in steps)


def test_parse_hosts_yml_quoted_value():
    text = "github.com:\n    user: 'alice'\n    oauth_token: \"gho_X\"\n"
    parsed = _parse_hosts_yml(text)
    assert parsed["github.com"]["user"] == "alice"
    assert parsed["github.com"]["oauth_token"] == "gho_X"


def test_parse_hosts_yml_ignores_comments_and_blank():
    text = """\
# top-level comment
github.com:
    # inline comment
    oauth_token: gho_x
    user: alice

"""
    parsed = _parse_hosts_yml(text)
    assert parsed == {"github.com": {"oauth_token": "gho_x", "user": "alice"}}
