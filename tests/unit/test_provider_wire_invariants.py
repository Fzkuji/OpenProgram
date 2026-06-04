"""Provider-pipeline invariants — enforced across EVERY provider.

The MiniMax saga was one instance of a class: a provider whose wire api
(anthropic-messages) was mishandled somewhere in the credential / fetch /
api-stamp / key pipeline because that behaviour was hand-written per
provider and drifted from the truth. The fix made everything DERIVE from
the provider's wire api (``providers._default_api_for``). These tests are
the guard that keeps it that way — they iterate the whole registry, so a
new provider or a future hand-edit that re-introduces drift fails here
instead of silently breaking one provider's chat.
"""
from __future__ import annotations

from openprogram.providers import get_providers
from openprogram.providers.models_generated import MODELS
from openprogram.webui._model_catalog import providers as cat
from openprogram.webui._model_catalog.credentials import _kind_for, _provider_api


def _static_apis():
    out: dict[str, set[str]] = {}
    for m in MODELS.values():
        out.setdefault(m.provider, set()).add(m.api)
    return out


def test_single_api_provider_never_drifts():
    """For every provider whose static models declare ONE wire api,
    ``_default_api_for`` must return exactly that — so a fetched/custom
    row routes the same as the static catalogue and can't drift."""
    bad = []
    for pid, apis in _static_apis().items():
        if len(apis) == 1:
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
    for pid in get_providers():
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
    for pid in get_providers():
        if _provider_api(pid) != cat._default_api_for(pid):
            bad.append((pid, _provider_api(pid), cat._default_api_for(pid)))
    assert not bad, f"credential/chat wire mismatch: {bad}"


def test_override_table_stays_empty():
    """Everything derives; the manual override table must stay empty so
    no one re-introduces a per-provider entry that can drift. Add one
    only to correct a models_generated mislabel — and update this test
    with the justification if you ever do."""
    assert cat._PROVIDER_DEFAULT_API == {}, (
        "per-provider api overrides re-introduced: "
        f"{cat._PROVIDER_DEFAULT_API}"
    )
