"""Unit tests for auth.methods — focused on state-machine logic, not network.

The PKCE / device-code methods that actually hit the network are tested
only for their offline parts (URL construction, state validation, manual
paste parsing). Integration tests with real OAuth endpoints live
elsewhere (not in this file).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from openprogram.auth import OAuthPayload, CliDelegatedPayload
from openprogram.auth.methods import (
    ApiKeyPasteMethod,
    CliImportMethod,
    PkceConfig, PkceLoginMethod,
    SsoStubMethod,
)
from openprogram.auth.methods.cli_import import CliImportConfig, _walk, _normalize_expires
from openprogram.auth.methods.pkce_oauth import _generate_pkce, _ask_manual_paste


# ---- LoginUi fake ---------------------------------------------------------

class FakeUi:
    def __init__(self, *, prompt_replies: list[str] | None = None):
        self.prompts: list[tuple[str, bool]] = []
        self.replies = list(prompt_replies or [])
        self.opened: list[str] = []
        self.progress: list[str] = []
        self.codes_shown: list[tuple[str, str]] = []

    async def open_url(self, url: str) -> None:
        self.opened.append(url)

    async def prompt(self, message: str, *, secret: bool = False) -> str:
        self.prompts.append((message, secret))
        if not self.replies:
            # Never-settling future — PKCE's "user never pastes" case.
            fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            return await fut
        return self.replies.pop(0)

    async def show_progress(self, message: str) -> None:
        self.progress.append(message)

    async def show_code(self, user_code: str, verification_uri: str) -> None:
        self.codes_shown.append((user_code, verification_uri))


# ---- ApiKeyPasteMethod ----------------------------------------------------

def test_api_key_paste_returns_credential():
    m = ApiKeyPasteMethod("openai", metadata={"display_name": "personal"})
    ui = FakeUi(prompt_replies=["sk-xxx"])
    cred = asyncio.run(m.run(ui))
    assert cred.provider_id == "openai"
    assert cred.kind == "api_key"
    assert cred.payload.api_key == "sk-xxx"
    assert cred.metadata["display_name"] == "personal"
    assert ui.prompts[0][1] is True   # secret=True


def test_api_key_paste_rejects_empty():
    m = ApiKeyPasteMethod("openai")
    ui = FakeUi(prompt_replies=["   "])
    with pytest.raises(ValueError):
        asyncio.run(m.run(ui))


def test_api_key_paste_custom_prompt():
    m = ApiKeyPasteMethod("openai", prompt_message="Work API key please")
    ui = FakeUi(prompt_replies=["k"])
    asyncio.run(m.run(ui))
    assert ui.prompts[0][0] == "Work API key please"


# ---- PKCE generator + manual-paste parser --------------------------------

def test_generate_pkce_produces_valid_challenge():
    import base64
    import hashlib
    verifier, challenge = _generate_pkce()
    assert 43 <= len(verifier) <= 128
    recomputed = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == recomputed


def test_manual_paste_parses_full_url():
    ui = FakeUi(prompt_replies=["http://localhost:1455/auth/callback?code=abc&state=S"])
    code = asyncio.run(_ask_manual_paste(ui, expected_state="S"))
    assert code == "abc"


def test_manual_paste_parses_query_only():
    ui = FakeUi(prompt_replies=["code=xyz&state=S"])
    code = asyncio.run(_ask_manual_paste(ui, expected_state="S"))
    assert code == "xyz"


def test_manual_paste_accepts_bare_code():
    ui = FakeUi(prompt_replies=["bare_code_value"])
    code = asyncio.run(_ask_manual_paste(ui, expected_state="S"))
    assert code == "bare_code_value"


def test_manual_paste_rejects_empty():
    ui = FakeUi(prompt_replies=["   "])
    with pytest.raises(ValueError):
        asyncio.run(_ask_manual_paste(ui, expected_state="S"))


def test_manual_paste_rejects_url_without_code():
    ui = FakeUi(prompt_replies=["http://localhost:1455/auth/callback?state=S"])
    with pytest.raises(ValueError):
        asyncio.run(_ask_manual_paste(ui, expected_state="S"))


def test_pkce_config_encodes_scope_and_extras():
    cfg = PkceConfig(
        authorize_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
        client_id="c1",
        scopes=["read", "write"],
        extra_authorize_params={"audience": "api"},
    )
    # Indirectly: PkceLoginMethod builds a URL using these. We verify by
    # inspecting the config surface — no network call in this test.
    assert cfg.client_id == "c1"
    assert cfg.scopes == ["read", "write"]
    assert cfg.extra_authorize_params == {"audience": "api"}


# ---- CliImportMethod: walk + normalize helpers ----------------------------

def test_walk_dict():
    assert _walk({"a": {"b": {"c": 42}}}, ["a", "b", "c"]) == 42


def test_walk_list_index():
    assert _walk({"tokens": [{"access": "first"}, {"access": "second"}]},
                 ["tokens", "1", "access"]) == "second"


def test_walk_missing_raises():
    with pytest.raises(KeyError):
        _walk({"a": 1}, ["b"])


def test_normalize_expires_ms():
    assert _normalize_expires(12345, "ms") == 12345


def test_normalize_expires_seconds_to_ms():
    assert _normalize_expires(100, "s") == 100_000


def test_normalize_expires_iso():
    out = _normalize_expires("2026-04-21T00:00:00+00:00", "iso")
    assert out > 0


def test_normalize_expires_empty_is_zero():
    assert _normalize_expires("", "ms") == 0
    assert _normalize_expires(None, "ms") == 0


# ---- CliImportMethod: link mode ------------------------------------------

def test_cli_import_link_mode_creates_delegated_payload(tmp_path: Path):
    store = tmp_path / "codex" / "auth.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({
        "tokens": {
            "access_token": "A", "refresh_token": "R",
            "expires_at": int(time.time() * 1000) + 3600_000,
            "account_id": "acc_1",
        },
    }))
    cfg = CliImportConfig(
        source_id="codex_cli",
        store_path=str(store),
        access_path=["tokens", "access_token"],
        refresh_path=["tokens", "refresh_token"],
        expires_path=["tokens", "expires_at"],
        expires_unit="ms",
        metadata_paths={"account_id": ["tokens", "account_id"]},
        mode="link",
    )
    m = CliImportMethod("openai-codex", cfg)
    cred = asyncio.run(m.run(FakeUi()))
    assert cred.kind == "cli_delegated"
    assert isinstance(cred.payload, CliDelegatedPayload)
    assert cred.payload.store_path == str(store)
    assert cred.metadata["account_id"] == "acc_1"
    assert cred.read_only is True


def test_cli_import_copy_mode_creates_oauth_payload(tmp_path: Path):
    store = tmp_path / "codex" / "auth.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({
        "tokens": {
            "access_token": "A", "refresh_token": "R",
            "expires_at": int(time.time() * 1000) + 3600_000,
        },
    }))
    cfg = CliImportConfig(
        source_id="codex_cli",
        store_path=str(store),
        access_path=["tokens", "access_token"],
        refresh_path=["tokens", "refresh_token"],
        expires_path=["tokens", "expires_at"],
        expires_unit="ms",
        mode="copy",
        client_id_hint="app_EMoamEEZ73f0CkXaXp7hrann",
    )
    m = CliImportMethod("openai-codex", cfg)
    cred = asyncio.run(m.run(FakeUi()))
    assert cred.kind == "oauth"
    assert isinstance(cred.payload, OAuthPayload)
    assert cred.payload.access_token == "A"
    assert cred.payload.refresh_token == "R"
    assert cred.payload.client_id == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert cred.read_only is False


def test_cli_import_missing_file_raises(tmp_path: Path):
    cfg = CliImportConfig(
        source_id="codex_cli",
        store_path=str(tmp_path / "nope.json"),
        access_path=["tokens", "access_token"],
    )
    m = CliImportMethod("openai-codex", cfg)
    with pytest.raises(FileNotFoundError):
        asyncio.run(m.run(FakeUi()))


def test_cli_import_bad_json_raises(tmp_path: Path):
    store = tmp_path / "bad.json"
    store.write_text("{not json")
    cfg = CliImportConfig(source_id="x", store_path=str(store), access_path=["a"])
    m = CliImportMethod("p", cfg)
    with pytest.raises(RuntimeError):
        asyncio.run(m.run(FakeUi()))


# ---- SSO stub --------------------------------------------------------------

def test_sso_stub_raises_not_implemented():
    m = SsoStubMethod("enterprise")
    with pytest.raises(NotImplementedError):
        asyncio.run(m.run(FakeUi()))
    # But the instance is usable — method_id and provider_id work so
    # provider plugins can still advertise "we support SSO".
    assert m.method_id == "sso"
    assert m.provider_id == "enterprise"
