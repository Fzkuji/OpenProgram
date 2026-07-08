"""Runtime registry population + the mutable-same-object invariant.

Post-Task-3 ``enabled_models._load`` builds ENABLED_MODELS from the
user's enabled models in config (``providers.<p>.models`` spec rows), not
the git-tracked ``providers/<p>/models.json`` catalogue. These tests pin:

  * ``_load`` reads the config source and populates the registry from it;
  * the registry is one MUTABLE dict object — dynamic writers
    (``_register_custom_model_in_registry``, the codex runtime-registration
    helper) do ``ENABLED_MODELS[k] = m`` in place and must land in the same
    dict.
"""
from openprogram.providers.models import get_model

from ._registry_fixture import install_registry


def test_registry_populated_from_config(monkeypatch):
    reg = install_registry(monkeypatch, {
        "openai": {"models": [{"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses"}]},
        "gemini-subscription": {"models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Subscription)"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Cloud Code Assist)",
             "key_prefix": "google-gemini-cli"},
        ]},
    })
    assert get_model("openai", "gpt-4o") is not None
    # gemini double-key both resolve
    assert get_model("gemini-subscription", "gemini-2.5-pro") is not None
    assert get_model("google-gemini-cli", "gemini-2.5-pro") is not None
    assert "openai/gpt-4o" in reg


def test_registry_is_mutable_same_object(monkeypatch):
    # _register_custom_model_in_registry writes ENABLED_MODELS[k]=m in place.
    reg = install_registry(monkeypatch, {
        "openai": {"models": [{"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses"}]},
    })
    before = id(reg)
    reg["__test__/x"] = reg["openai/gpt-4o"]
    assert id(reg) == before


def test_load_reads_config_source(monkeypatch):
    # _load must read read_providers_config; inject a sentinel provider row
    # and assert _load surfaces it as a registry key.
    import openprogram.providers._config_read as cr
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(cr, "read_providers_config",
                        lambda: {"openai": {"models": [{"id": "__probe__",
                                                        "name": "P",
                                                        "api": "openai-completions"}]}})
    assert "openai/__probe__" in mg._load()
