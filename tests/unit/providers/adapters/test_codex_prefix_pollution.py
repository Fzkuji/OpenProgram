"""Regression tests for the model-registry pollution bug where a prefixed
model id (``openai-codex:gpt-5.5``) reaching the codex path minted a ghost
registry row displayed as "GPT-openai Codex:5.5".

Root cause: persisted session meta stores ``runtime.model`` verbatim (the
PREFIXED id), and the restore path feeds it back as a bare ``model=``. The
codex runtime then missed ``get_model`` and dynamically registered the
prefixed id.

Fixes under test:
  1. ``ensure_codex_model_registered`` refuses ``:`` ids (no ghost row).
  2. ``ensure_anthropic_model_registered`` refuses ``:`` ids too.
  3. ``_create_runtime_for_visualizer`` strips a leading ``<provider>:``
     so the prefixed id resolves to the same bare-id runtime.
"""
from __future__ import annotations

from openprogram.providers.enabled_models import ENABLED_MODELS
from openprogram.providers.openai_codex.runtime import (
    ensure_codex_model_registered,
)
from openprogram.providers.anthropic._claude_code_direct_runtime import (
    ensure_anthropic_model_registered,
)


def _codex_keys() -> set[str]:
    return {k for k in ENABLED_MODELS if k.startswith("openai-codex/")}


def test_codex_registrar_refuses_prefixed_id():
    before = _codex_keys()
    # A prefixed id must NOT create a registry row.
    ensure_codex_model_registered("openai-codex:gpt-5.5")
    ensure_codex_model_registered("openai-codex:openai-codex:gpt-5.5")
    after = _codex_keys()
    assert after == before, "prefixed id should not register a ghost row"
    assert "openai-codex/openai-codex:gpt-5.5" not in ENABLED_MODELS


def test_codex_registrar_still_registers_bare_id():
    # Only meaningful when a codex template exists in the static registry.
    template = any(
        m.provider == "openai-codex" and m.api == "openai-codex"
        for m in ENABLED_MODELS.values()
    )
    if not template:
        return  # no codex template to mirror; nothing to assert
    ensure_codex_model_registered("gpt-5.5")
    key = "openai-codex/gpt-5.5"
    assert key in ENABLED_MODELS
    assert ":" not in ENABLED_MODELS[key].id


def test_anthropic_registrar_refuses_prefixed_id():
    before = {k for k in ENABLED_MODELS if k.startswith("anthropic/")}
    out = ensure_anthropic_model_registered("anthropic:claude-fake-9")
    after = {k for k in ENABLED_MODELS if k.startswith("anthropic/")}
    assert after == before
    assert "anthropic/anthropic:claude-fake-9" not in ENABLED_MODELS
    # Returns the id unchanged so the caller's downstream resolve raises
    # a clean "Unknown model" instead of using a ghost.
    assert out == "anthropic:claude-fake-9"


def test_visualizer_strips_provider_prefix(monkeypatch):
    """A prefixed model on the restore path must resolve to the SAME bare id
    the codex runtime would build — no new registration."""
    import openprogram.webui._runtime_management as rm

    captured = {}

    def fake_create_runtime(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openprogram.providers.registry.create_runtime", fake_create_runtime
    )
    # openai-codex is in PROVIDERS → routes through create_runtime(model=...).
    rm._create_runtime_for_visualizer("openai-codex", "openai-codex:gpt-5.5")
    assert captured.get("model") == "gpt-5.5", captured
    # Double prefix (already in old meta.json) collapses too.
    captured.clear()
    rm._create_runtime_for_visualizer(
        "openai-codex", "openai-codex:openai-codex:gpt-5.5"
    )
    assert captured.get("model") == "gpt-5.5", captured
