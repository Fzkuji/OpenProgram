"""Tests for openprogram.auth.cli — the auth command tree."""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.auth.cli import build_parser, dispatch
from openprogram.auth.manager import AuthManager, set_manager_for_testing
from openprogram.auth.profiles import (
    DEFAULT_PROFILE_NAME,
    ProfileManager,
    set_profile_manager_for_testing,
)
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    ApiKeyPayload,
    Credential,
    CredentialPool,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch, capsys):
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    set_manager_for_testing(AuthManager(store=store))
    pm = ProfileManager(root=tmp_path / "profiles")
    set_profile_manager_for_testing(pm)
    # Redirect Codex path so imports don't touch the real file.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "fake_codex"))
    yield store, pm, tmp_path, capsys
    set_store_for_testing(None)
    set_manager_for_testing(None)
    set_profile_manager_for_testing(None)


def _parse(argv):
    """Build the same argparse tree openprogram.cli.main sets up, so
    the test drives the exact code path real invocations take.

    Shape: ``openprogram providers <verb> ...`` — `build_parser` wires
    verbs directly on the `providers` subparser.
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p_providers = sub.add_parser("providers")
    providers_sub = p_providers.add_subparsers(dest="providers_cmd")
    build_parser(providers_sub)
    return parser.parse_args(["providers", *argv])


# ---- list ----------------------------------------------------------------

def test_list_empty_suggests_next_steps(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["list"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "No credential pools yet" in out
    assert "providers discover" in out
    assert "providers login" in out


def test_list_json_emits_array(isolated):
    store, _, _, cap = isolated
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default",
        credentials=[Credential(
            provider_id="openai", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-deadbeef1234"),
            source="cli_paste",
        )],
    ))
    rc = dispatch(_parse(["list", "--json"]))
    assert rc == 0
    body = json.loads(cap.readouterr().out)
    assert len(body) == 1
    assert body[0]["provider_id"] == "openai"
    assert body[0]["credentials"][0]["kind"] == "api_key"
    # Preview is masked — must not contain the full secret.
    assert "sk-deadbeef1234" not in body[0]["credentials"][0]["preview"]


def test_list_respects_profile_filter(isolated):
    store, pm, _, cap = isolated
    pm.create_profile("work")
    for prof, tag in [("default", "personal"), ("work", "work")]:
        store.put_pool(CredentialPool(
            provider_id="openai", profile_id=prof,
            credentials=[Credential(
                provider_id="openai", profile_id=prof, kind="api_key",
                payload=ApiKeyPayload(api_key=f"sk-{tag}-11112222"),
            )],
        ))
    rc = dispatch(_parse(["list", "--profile", "work", "--json"]))
    body = json.loads(cap.readouterr().out)
    assert len(body) == 1
    assert body[0]["profile_id"] == "work"


# ---- discover ------------------------------------------------------------

def test_discover_picks_up_env_var(isolated, monkeypatch):
    _, _, _, cap = isolated
    for v in ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"]:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-found-on-machine")
    rc = dispatch(_parse(["discover", "--json"]))
    assert rc == 0
    body = json.loads(cap.readouterr().out)
    env = [e for e in body if e.get("source_id") == "env:OPENAI_API_KEY"]
    assert env, body
    # Still masked in the discover output.
    assert "sk-found-on-machine" not in env[0]["preview"]


# ---- adopt ---------------------------------------------------------------

def test_adopt_env_var(isolated, monkeypatch):
    store, _, _, cap = isolated
    monkeypatch.setenv("OPENAI_API_KEY", "sk-adopt-me-please-123")
    rc = dispatch(_parse(["adopt", "env:OPENAI_API_KEY"]))
    assert rc == 0
    pool = store.find_pool("openai", "default")
    assert pool is not None
    assert len(pool.credentials) == 1
    assert pool.credentials[0].payload.api_key == "sk-adopt-me-please-123"


def _clear_provider_env(monkeypatch):
    """Wipe every env var discover() looks at so the dev machine's real
    keys don't leak into the test. Keeps only what the test sets."""
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    for v in set(PROVIDER_ENV_VARS.values()):
        monkeypatch.delenv(v, raising=False)
    for v in [
        "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN",
        "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN",
        "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
    ]:
        monkeypatch.delenv(v, raising=False)


def test_adopt_all_batches_everything(isolated, monkeypatch):
    store, _, _, cap = isolated
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-batch-one-11112222")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-batch-two-33334444")
    rc = dispatch(_parse(["adopt", "--all"]))
    assert rc == 0, cap.readouterr()
    assert store.find_pool("openai", "default") is not None
    assert store.find_pool("groq", "default") is not None


def test_adopt_all_is_idempotent(isolated, monkeypatch):
    store, _, _, cap = isolated
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-idempotent-test-55")
    dispatch(_parse(["adopt", "--all"]))
    cap.readouterr()
    # Run again — should silently skip.
    rc = dispatch(_parse(["adopt", "--all"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "Adopted 0" in out  # zero new, others skipped


def test_adopt_bare_without_args_errors(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["adopt"]))
    assert rc == 2
    assert "--all" in cap.readouterr().err


def test_adopt_unknown_source(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["adopt", "made_up_source"]))
    assert rc == 1
    err = cap.readouterr().err
    assert "Unknown source" in err


def test_adopt_routes_to_non_default_profile(isolated, monkeypatch):
    store, pm, _, cap = isolated
    pm.create_profile("work")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-adopt-to-work-1")
    rc = dispatch(_parse(["adopt", "env:OPENAI_API_KEY", "--profile", "work"]))
    assert rc == 0
    assert store.find_pool("openai", "work") is not None
    assert store.find_pool("openai", "default") is None


# ---- logout --------------------------------------------------------------

def test_logout_removes_pool(isolated, monkeypatch):
    store, _, _, cap = isolated
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default",
        credentials=[Credential(
            provider_id="openai", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-goodbye-12345"),
        )],
    ))
    # --yes skips confirmation.
    rc = dispatch(_parse(["logout", "openai", "--yes"]))
    assert rc == 0
    assert store.find_pool("openai", "default") is None
    out = cap.readouterr().out
    assert "Removed 1 credential" in out


def test_logout_no_pool_is_noop(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["logout", "nothing-here", "--yes"]))
    assert rc == 0
    assert "nothing to remove" in cap.readouterr().out


# ---- status --------------------------------------------------------------

def test_status_reports_missing_credential(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["status", "openai"]))
    assert rc == 1
    out = cap.readouterr().out
    assert "No credential configured" in out
    assert "openprogram providers login openai" in out


def test_status_reports_valid_credential(isolated):
    store, _, _, cap = isolated
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default",
        credentials=[Credential(
            provider_id="openai", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-working-key-555"),
            source="cli_paste",
        )],
    ))
    rc = dispatch(_parse(["status", "openai"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "Kind:     api_key" in out
    # Status line's preview must still be masked.
    assert "sk-working-key-555" not in out


# ---- login (api_key with mocked getpass) ---------------------------------

def test_login_paste_api_key(isolated, monkeypatch):
    store, _, _, cap = isolated
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-pasted-from-cli")
    # --method skips the interactive method selection.
    rc = dispatch(_parse(["login", "openai", "--method", "api_key"]))
    assert rc == 0
    pool = store.find_pool("openai", "default")
    assert pool and pool.credentials[0].payload.api_key == "sk-pasted-from-cli"


def test_login_paste_empty_fails(isolated, monkeypatch):
    _, _, _, cap = isolated
    monkeypatch.setattr("getpass.getpass", lambda prompt: "   ")
    rc = dispatch(_parse(["login", "openai", "--method", "api_key"]))
    assert rc == 1
    assert "empty API key" in cap.readouterr().err


def test_login_unknown_method(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["login", "openai", "--method", "telepathy"]))
    assert rc == 1
    assert "not available" in cap.readouterr().err


# ---- profile -------------------------------------------------------------

def test_profile_list(isolated):
    _, pm, _, cap = isolated
    pm.create_profile("work")
    rc = dispatch(_parse(["profiles", "list"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "work" in out
    assert DEFAULT_PROFILE_NAME in out


def test_profile_create_and_delete(isolated):
    _, _, _, cap = isolated
    assert dispatch(_parse(["profiles", "create", "scratch"])) == 0
    assert "Created profile scratch" in cap.readouterr().out
    assert dispatch(_parse(["profiles", "delete", "scratch", "--yes"])) == 0
    assert "Deleted profile scratch" in cap.readouterr().out


def test_profile_create_duplicate(isolated):
    _, _, _, cap = isolated
    dispatch(_parse(["profiles", "create", "dup"]))
    cap.readouterr()
    rc = dispatch(_parse(["profiles", "create", "dup"]))
    assert rc == 1


# ---- codex login via import ---------------------------------------------

def test_login_import_codex_when_file_exists(isolated, monkeypatch):
    store, _, tmp, cap = isolated
    # Seed a fake ~/.codex/auth.json via CODEX_HOME (set in the fixture).
    codex_dir = tmp / "fake_codex"
    codex_dir.mkdir()
    import base64, json as _json, time as _time
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(_json.dumps({
        "exp": int(_time.time()) + 3600,
        "https://api.openai.com/auth": {"chatgpt_account_id": "acc_xyz"},
    }).encode()).rstrip(b"=").decode()
    jwt = f"{header}.{body}.sig"
    (codex_dir / "auth.json").write_text(_json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": jwt, "refresh_token": "R-abc",
            "account_id": "acc_xyz",
        },
    }))

    rc = dispatch(_parse(["login", "openai-codex", "--method", "import_from_cli"]))
    assert rc == 0
    pool = store.find_pool("openai-codex", "default")
    assert pool is not None
    cred = pool.credentials[0]
    assert cred.kind == "oauth"
    assert cred.payload.refresh_token == "R-abc"
    assert cred.metadata["account_id"] == "acc_xyz"


def test_login_import_codex_apikey_refuses(isolated, monkeypatch):
    """Codex CLI in apikey mode stores a bare OPENAI_API_KEY — not OAuth.
    That shape can't drive the Codex runtime (no chatgpt_account_id, no
    JWT). Matching OpenClaw's behavior, the import adapter refuses to
    accept it rather than silently routing it elsewhere: the user asked
    to import Codex credentials, and this isn't one. They need to run
    `codex login` (without --api-key) to get OAuth tokens."""
    store, _, tmp, cap = isolated
    codex_dir = tmp / "fake_codex"
    codex_dir.mkdir()
    import json as _json
    (codex_dir / "auth.json").write_text(_json.dumps({
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-proj-from-codex-apikey",
    }))
    rc = dispatch(_parse(["login", "openai-codex", "--method", "import_from_cli"]))
    assert rc == 1
    # Nothing written to either pool.
    assert store.find_pool("openai-codex", "default") is None
    assert store.find_pool("openai", "default") is None


def test_login_import_codex_when_file_missing(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["login", "openai-codex", "--method", "import_from_cli"]))
    assert rc == 1
    assert "codex login" in cap.readouterr().err


# ---- aliases -------------------------------------------------------------

def test_aliases_list_shows_canonical(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["aliases"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "codex" in out
    assert "openai-codex" in out
    assert "claude" in out and "anthropic" in out


def test_aliases_json_is_parseable(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["aliases", "--json"]))
    assert rc == 0
    body = json.loads(cap.readouterr().out)
    assert body["codex"] == "openai-codex"


def test_login_resolves_alias(isolated, monkeypatch):
    store, _, _, cap = isolated
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-from-alias-login")
    # Use the alias 'codex' rather than the canonical id.
    rc = dispatch(_parse(["login", "codex", "--method", "api_key"]))
    assert rc == 0
    # The pool must be stored under the canonical id, not the alias.
    assert store.find_pool("codex", "default") is None
    pool = store.find_pool("openai-codex", "default")
    assert pool is not None
    assert pool.credentials[0].payload.api_key == "sk-from-alias-login"


def test_status_resolves_alias(isolated):
    store, _, _, cap = isolated
    store.put_pool(CredentialPool(
        provider_id="anthropic", profile_id="default",
        credentials=[Credential(
            provider_id="anthropic", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-ant-abc12345678"),
            source="cli_paste",
        )],
    ))
    # `claude` → `anthropic`
    rc = dispatch(_parse(["status", "claude"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "anthropic" in out


# ---- doctor --------------------------------------------------------------

def test_doctor_empty_store_warns_no_pools(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["doctor"]))
    # No ERRORs, just WARN → exit 0.
    assert rc == 0
    out = cap.readouterr().out
    assert "no_pools" in out


def test_doctor_json_has_findings_list(isolated):
    _, _, _, cap = isolated
    rc = dispatch(_parse(["doctor", "--json"]))
    assert rc == 0
    body = json.loads(cap.readouterr().out)
    assert body["pools_checked"] == 0
    assert any(f["code"] == "no_pools" for f in body["findings"])


def test_doctor_flags_expired_oauth_without_refresh(isolated):
    store, _, _, cap = isolated
    # Provider with no refresh registered → expired oauth must be ERROR.
    from openprogram.auth.types import OAuthPayload
    store.put_pool(CredentialPool(
        provider_id="random-oauth-provider", profile_id="default",
        credentials=[Credential(
            provider_id="random-oauth-provider", profile_id="default",
            kind="oauth",
            payload=OAuthPayload(
                access_token="tok-expired", refresh_token="r-abc",
                expires_at_ms=1,  # epoch — definitely expired
            ),
            source="cli_paste",
        )],
    ))
    rc = dispatch(_parse(["doctor", "--json"]))
    body = json.loads(cap.readouterr().out)
    assert rc == 1, body
    codes = {f["code"] for f in body["findings"]}
    assert "expired_no_refresh" in codes


def test_doctor_flags_missing_source_file(isolated, tmp_path):
    store, _, _, cap = isolated
    # Pretend we imported from a file that no longer exists.
    ghost = tmp_path / "gone.json"
    store.put_pool(CredentialPool(
        provider_id="openai-codex", profile_id="default",
        credentials=[Credential(
            provider_id="openai-codex", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-orphaned-123456"),
            source="codex_cli_import",
            metadata={"source_path": str(ghost), "imported_from": "codex_cli"},
        )],
    ))
    rc = dispatch(_parse(["doctor", "--json"]))
    # Missing source file is only a WARN → exit 0.
    assert rc == 0
    body = json.loads(cap.readouterr().out)
    codes = {f["code"] for f in body["findings"]}
    assert "missing_source_file" in codes


def test_doctor_healthy_api_key_pool_passes(isolated):
    store, _, _, cap = isolated
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default",
        credentials=[Credential(
            provider_id="openai", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="sk-healthy-key-12345"),
            source="cli_paste",
        )],
    ))
    rc = dispatch(_parse(["doctor"]))
    assert rc == 0
    out = cap.readouterr().out
    assert "✗ ERROR" not in out


# ---- setup (wizard) ------------------------------------------------------

def test_setup_wizard_aborts_cleanly_on_no(isolated, monkeypatch):
    _, _, _, cap = isolated
    # User answers 'n' to the opening "Start?" prompt.
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = dispatch(_parse(["setup"]))
    assert rc == 0
    assert "Cancelled" in cap.readouterr().out


def test_setup_wizard_adopts_detected_env_var(isolated, monkeypatch):
    store, _, _, cap = isolated
    for v in ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"]:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wizard-adopted-12345")
    # Answer: start? y, adopt all? y, then decline every popular-login offer.
    answers = iter(["y", "y"] + ["n"] * 10)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    rc = dispatch(_parse(["setup"]))
    assert rc == 0, cap.readouterr()
    pool = store.find_pool("openai", "default")
    assert pool is not None
    assert pool.credentials[0].payload.api_key == "sk-wizard-adopted-12345"
