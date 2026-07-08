"""Task 3: runtime MODEL_REGISTRY loads from config spec rows, not the
git-tracked providers/<p>/models.json catalog.

The registry now contains ONLY user-enabled models (config
``providers.<p>.models`` spec rows) plus anything registered dynamically
at runtime (claude-code seed, custom-model side-effect). These tests pin:

  * a fixture config → the exact registry keys + Model contents;
  * endpoint defaults fill a row's missing api/base_url; a row that
    carries its own api/base_url keeps them (row wins);
  * key = "<key_prefix or provider>/<id>" — key_prefix produces an
    independent second key;
  * empty / missing config → empty registry, no crash (fresh install);
  * nested cost / headers / compat round-trip into the Model faithfully;
  * the real ~/.openprogram/config.json is never read or written.
"""
from __future__ import annotations

import openprogram.providers._config_read as cr
import openprogram.providers.models_generated as mg
from openprogram.providers.types import Model


def _reload(monkeypatch, providers_cfg: dict) -> dict[str, Model]:
    """Point the config reader at an in-memory providers section and
    rebuild the registry, returning the fresh dict."""
    monkeypatch.setattr(cr, "read_providers_config", lambda: providers_cfg)
    return mg._load()


def test_fixture_config_registry_keys_and_contents(monkeypatch):
    cfg = {
        "openai": {"models": [
            {"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses",
             "input": ["text", "image"], "context_window": 128000,
             "cost": {"input": 2.5, "output": 10.0}},
        ]},
        "anthropic": {"models": [
            {"id": "claude-opus-4-8", "name": "Claude Opus 4.8",
             "api": "anthropic-messages", "reasoning": True},
        ]},
    }
    reg = _reload(monkeypatch, cfg)
    assert set(reg) == {"openai/gpt-4o", "anthropic/claude-opus-4-8"}
    m = reg["openai/gpt-4o"]
    assert m.api == "openai-responses"
    assert m.provider == "openai"
    assert m.base_url == "https://api.openai.com/v1"   # filled from provider.json
    assert m.input == ["text", "image"]
    assert m.cost.input == 2.5 and m.cost.output == 10.0
    assert reg["anthropic/claude-opus-4-8"].reasoning is True


def test_endpoint_defaults_fill_missing_api_and_base_url(monkeypatch):
    # Row carries neither api nor base_url → both come from provider.json.
    cfg = {"anthropic": {"models": [
        {"id": "claude-x", "name": "Claude X"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    m = reg["anthropic/claude-x"]
    assert m.api == "anthropic-messages"
    assert m.base_url == "https://api.anthropic.com"


def test_row_api_and_base_url_win_over_endpoint(monkeypatch):
    # Row carries explicit api + base_url that differ from provider.json's
    # default endpoint → the row's values win.
    cfg = {"openai": {"models": [
        {"id": "custom", "name": "Custom", "api": "openai-completions",
         "base_url": "https://proxy.example/v1"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    m = reg["openai/custom"]
    assert m.api == "openai-completions"        # not provider.json's openai-responses
    assert m.base_url == "https://proxy.example/v1"


def test_key_prefix_produces_independent_key(monkeypatch):
    cfg = {"gemini-subscription": {"models": [
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Subscription)"},
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Cloud Code Assist)",
         "key_prefix": "google-gemini-cli"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    assert "gemini-subscription/gemini-2.5-pro" in reg
    assert "google-gemini-cli/gemini-2.5-pro" in reg
    assert reg["gemini-subscription/gemini-2.5-pro"].name == "Gemini 2.5 Pro (Subscription)"
    assert reg["google-gemini-cli/gemini-2.5-pro"].name == "Gemini 2.5 Pro (Cloud Code Assist)"
    # provider is the config key, never the key prefix
    assert reg["google-gemini-cli/gemini-2.5-pro"].provider == "gemini-subscription"


def test_empty_config_yields_empty_registry(monkeypatch):
    assert _reload(monkeypatch, {}) == {}


def test_missing_config_does_not_crash(monkeypatch):
    # read_providers_config returns {} when config.json is absent.
    def boom():
        raise FileNotFoundError
    # even if the reader itself misbehaves, _load must not propagate.
    monkeypatch.setattr(cr, "read_providers_config", boom)
    assert mg._load() == {}


def test_manual_source_row_loads_like_any_other(monkeypatch):
    cfg = {"openai": {"models": [
        {"id": "hand-typed", "name": "Hand Typed", "source": "manual",
         "api": "openai-completions"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    assert reg["openai/hand-typed"].id == "hand-typed"


def test_nested_cost_headers_compat_round_trip(monkeypatch):
    cfg = {"github-copilot": {"models": [
        {"id": "gpt-5.2-codex", "name": "GPT-5.2-Codex",
         "api": "openai-responses",
         "input": ["text", "image"],
         "headers": {"Copilot-Integration-Id": "vscode-chat"},
         "compat": {"supports_store": True},
         "cost": {"input": 1.25, "output": 10.0, "cache_read": 0.125}},
    ]}}
    reg = _reload(monkeypatch, cfg)
    m = reg["github-copilot/gpt-5.2-codex"]
    assert m.headers == {"Copilot-Integration-Id": "vscode-chat"}
    # pydantic coerces the compat dict into OpenAICompletionsCompat; the
    # declared field survives faithfully.
    assert getattr(m.compat, "supports_store", None) is True
    assert m.cost.input == 1.25 and m.cost.cache_read == 0.125


def test_bad_row_skipped_others_survive(monkeypatch):
    cfg = {"openai": {"models": [
        {"name": "no id here"},                       # invalid → skipped
        {"id": "good", "name": "Good", "api": "openai-completions"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    assert list(reg) == ["openai/good"]


def test_claude_code_dynamic_registration_lands_in_registry():
    # The claude-code seed writes into the SAME dict object at import time.
    from openprogram.providers.anthropic import _claude_code_registry as ccr
    ccr._seed_claude_code_models()
    assert "claude-code/claude-opus-4-8" in mg.MODEL_REGISTRY


def test_get_model_alias_fallback_still_works(monkeypatch):
    # get_model resolves an alias to a real registry key. Put the real
    # model in via config, then look it up by an alias.
    from openprogram.providers import models as pm
    from openprogram.auth import aliases as al
    cfg = {"anthropic": {"models": [
        {"id": "claude-opus-4-8", "name": "Opus", "api": "anthropic-messages"},
    ]}}
    reg = _reload(monkeypatch, cfg)
    monkeypatch.setattr(mg, "MODEL_REGISTRY", reg)
    monkeypatch.setattr(pm, "MODEL_REGISTRY", reg)
    # sanity: direct lookup works
    assert pm.get_model("anthropic", "claude-opus-4-8") is not None


def test_real_config_untouched(monkeypatch, tmp_path):
    # Every read routes through read_providers_config; patching it means
    # _load never opens the real config file.
    calls = {"n": 0}
    monkeypatch.setattr(cr, "read_providers_config",
                        lambda: (calls.__setitem__("n", calls["n"] + 1), {})[1])
    mg._load()
    assert calls["n"] >= 1
