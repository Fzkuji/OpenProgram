"""claude-code direct subscription-OAuth runtime (no Meridian).

The claude-code provider now connects straight to api.anthropic.com with
a subscription OAuth token, mirroring openai-codex. These tests pin:

  * the registry routes claude-code to the direct runtime (not the
    Meridian-backed _max_proxy_runtime);
  * model aliases fold onto current anthropic catalog ids;
  * the resolver pulls a token out of a cli_delegated credential by
    re-reading the external CLI's file (the codex/claude-code pattern).
"""
from __future__ import annotations

import json

import pytest


def test_registry_routes_claude_code_to_direct_runtime():
    import openprogram.providers.registry as r
    cls, mod, _default = r.PROVIDERS["claude-code"]
    assert cls == "ClaudeCodeRuntime"
    assert mod == "openprogram.providers.anthropic._claude_code_direct_runtime"
    assert "_max_proxy_runtime" not in mod  # Meridian path retired from default


@pytest.mark.parametrize(
    "given,expected",
    [
        # Bare family aliases expand to a current default.
        ("claude-opus-4", "claude-opus-4-6"),
        ("claude-sonnet-4", "claude-sonnet-4-6"),
        ("claude-haiku-4", "claude-haiku-4-5"),
        # Specific ids pass through verbatim — TRUSTED, not folded onto the
        # (lagging) local catalog. This is what lets 4.7 / 4.8 / fable work.
        ("claude-opus-4-8", "claude-opus-4-8"),
        ("claude-opus-4-7[1m]", "claude-opus-4-7[1m]"),
        ("claude-fable-5", "claude-fable-5"),
        ("claude-opus-4-5-20251101", "claude-opus-4-5-20251101"),
        # Unknown / garbage → sonnet default.
        ("whatever", "claude-sonnet-4-6"),
    ],
)
def test_normalize_model_folds_onto_catalog(given, expected):
    from openprogram.providers.anthropic._claude_code_direct_runtime import _normalize_model
    assert _normalize_model(given) == expected


def test_runtime_targets_anthropic_namespace(monkeypatch):
    """The direct runtime drives the anthropic:<id> wire, not claude-code/<id>."""
    from openprogram.providers.anthropic import _claude_code_direct_runtime as m

    captured = {}

    def _fake_init(self, model, api_key=None, max_retries=2, **kw):
        captured["model"] = model
        captured["api_key"] = api_key

    monkeypatch.setattr(m.Runtime, "__init__", _fake_init)
    m.ClaudeCodeRuntime(api_key="sk-ant-oat-FAKE", model="claude-opus-4")
    assert captured["model"] == "anthropic:claude-opus-4-6"
    assert captured["api_key"] == "sk-ant-oat-FAKE"


def test_resolver_reads_cli_delegated_token(tmp_path):
    """A cli_delegated credential resolves by re-reading the CLI's file."""
    from openprogram.auth.types import Credential, CliDelegatedPayload
    from openprogram.auth.resolver import _extract_token

    cred_file = tmp_path / ".credentials.json"
    cred_file.write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "sk-ant-oat-LIVE", "refreshToken": "rt"}}
    ), encoding="utf-8")

    cred = Credential(
        provider_id="anthropic",
        profile_id="default",
        kind="cli_delegated",
        payload=CliDelegatedPayload(
            store_path=str(cred_file),
            access_key_path=["claudeAiOauth", "accessToken"],
            refresh_key_path=["claudeAiOauth", "refreshToken"],
        ),
        source="claude_code",
    )
    assert _extract_token(cred) == "sk-ant-oat-LIVE"


def test_resolver_cli_delegated_missing_file_is_none(tmp_path):
    from openprogram.auth.types import Credential, CliDelegatedPayload
    from openprogram.auth.resolver import _extract_token

    cred = Credential(
        provider_id="anthropic",
        profile_id="default",
        kind="cli_delegated",
        payload=CliDelegatedPayload(
            store_path=str(tmp_path / "nope.json"),
            access_key_path=["claudeAiOauth", "accessToken"],
        ),
        source="claude_code",
    )
    assert _extract_token(cred) is None
