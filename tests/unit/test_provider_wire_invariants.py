"""Provider-pipeline invariants — enforced across every REGISTRY row.

The MiniMax saga was one instance of a class: a provider whose wire api
(anthropic-messages) was mishandled somewhere in the credential / fetch /
api-stamp / key pipeline because that behaviour was hand-written per
provider and drifted from the truth. The fix made everything DERIVE from
the provider's wire api (``providers._default_api_for``). These tests are
the guard that keeps it that way — they iterate the whole registry, so a
row that re-introduces drift fails here instead of silently breaking one
provider's chat.

Post-Task-3 the runtime registry holds ONLY the user's enabled models
(config spec rows), so there is no ambient 750-model catalogue to iterate.
A module fixture injects a config covering the full wire matrix
(openai-completions / anthropic-messages / google-generative-ai /
openai-responses, plus a headers/compat row and a key_prefix dual-key
row) and rebuilds ``MODEL_REGISTRY`` from it — the invariants then run over
every one of those rows exactly as they did over the static catalogue.
"""
from __future__ import annotations

import pytest

import openprogram.providers._config_read as cr
import openprogram.providers.models as pm
import openprogram.providers.models_generated as mg
from openprogram.webui._model_catalog import providers as cat
from openprogram.webui._model_catalog.credentials import _kind_for, _provider_api


# A config covering every wire in the matrix, one enabled row per provider,
# plus a headers/compat row (github-copilot) and a key_prefix dual-key row
# (gemini-subscription mirroring the google-gemini-cli second key).
_WIRE_MATRIX_CFG = {
    "deepseek": {"models": [                          # openai-completions
        {"id": "deepseek-chat", "name": "DeepSeek Chat"},
    ]},
    "openai": {"models": [                            # openai-responses
        {"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses"},
    ]},
    "anthropic": {"models": [                         # anthropic-messages
        {"id": "claude-opus-4-8", "name": "Opus", "api": "anthropic-messages"},
    ]},
    "google": {"models": [                            # google-generative-ai
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
    ]},
    "github-copilot": {"models": [                    # headers + compat
        {"id": "gpt-5.2-codex", "name": "GPT-5.2-Codex", "api": "openai-responses",
         "headers": {"Copilot-Integration-Id": "vscode-chat"},
         "compat": {"supports_store": True}},
    ]},
    "gemini-subscription": {"models": [               # key_prefix dual-key
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Subscription)"},
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Cloud Code Assist)",
         "key_prefix": "google-gemini-cli"},
    ]},
    "minimax": {"models": [                           # anthropic-wire third-party
        {"id": "MiniMax-M2", "name": "MiniMax M2"},
    ]},
}


@pytest.fixture(autouse=True)
def _wire_matrix_registry(monkeypatch):
    """Inject the wire-matrix config and rebuild MODEL_REGISTRY from it, so
    the invariants run over a controlled, wire-complete set of rows instead
    of the (now enabled-only) runtime registry. Patched at every binding so
    ``get_providers()`` and the test-module import both see the same dict."""
    monkeypatch.setattr(cr, "read_providers_config", lambda: _WIRE_MATRIX_CFG)
    reg = mg._load()
    assert reg, "fixture must build a non-empty registry"
    monkeypatch.setattr(mg, "MODEL_REGISTRY", reg)
    monkeypatch.setattr(pm, "MODEL_REGISTRY", reg)
    return reg


def _static_apis(reg):
    out: dict[str, set[str]] = {}
    for m in reg.values():
        out.setdefault(m.provider, set()).add(m.api)
    return out


def test_single_api_provider_never_drifts(_wire_matrix_registry):
    """For every provider whose registry models declare ONE wire api,
    ``_default_api_for`` must return exactly that — so a fetched/custom
    row routes the same as the enabled row and can't drift."""
    bad = []
    for pid, apis in _static_apis(_wire_matrix_registry).items():
        # Only meaningful where the derivation is unambiguous: a provider
        # whose provider.json declares MULTIPLE wire endpoints (e.g.
        # github-copilot) has no single derived api by design — skip it,
        # exactly as the full-catalogue version skipped multi-api providers.
        if len(apis) == 1 and len(cat._static_apis_for(pid)) == 1:
            real = next(iter(apis))
            got = cat._default_api_for(pid)
            if got != real:
                bad.append((pid, real, got))
    assert not bad, f"api drift: {bad}"


def test_anthropic_wire_providers_get_anthropic_kind():
    """Any provider that resolves to the Anthropic wire must use an
    Anthropic credential probe (not the OpenAI Bearer one, which 404s on
    an /anthropic host and falsely rejects the key)."""
    bad = []
    for pid in pm.get_providers():
        if cat._default_api_for(pid) == "anthropic-messages":
            if _kind_for(pid) not in ("anthropic_compat", "anthropic_native"):
                bad.append((pid, _kind_for(pid)))
    assert not bad, f"anthropic providers with wrong kind: {bad}"


def test_credential_and_chat_agree_on_wire():
    """The credential layer (``_provider_api``) and the chat layer
    (``_default_api_for``) must classify every provider identically — a
    disagreement is exactly the bug where auth probes one endpoint while
    chat streams to another."""
    bad = []
    for pid in pm.get_providers():
        if _provider_api(pid) != cat._default_api_for(pid):
            bad.append((pid, _provider_api(pid), cat._default_api_for(pid)))
    assert not bad, f"credential/chat wire mismatch: {bad}"


def test_override_table_stays_empty():
    """Everything derives; the manual override table must stay empty so
    no one re-introduces a per-provider entry that can drift. Add one
    only to correct a provider.json mislabel — and update this test
    with the justification if you ever do."""
    assert cat._PROVIDER_DEFAULT_API == {}, (
        "per-provider api overrides re-introduced: "
        f"{cat._PROVIDER_DEFAULT_API}"
    )
