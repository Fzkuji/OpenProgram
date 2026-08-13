"""Unit tests for the mixture_of_agents tool.

All provider access is faked — no real LLM calls, ever.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import openprogram.providers as providers
import openprogram.providers.metadata as provider_metadata
from openprogram.programs.functions.mixture_of_agents import mixture_of_agents as moa


def _model(provider: str, model_id: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, id=model_id)


FOUR_PROVIDER_REGISTRY = [
    _model("claude-code", "claude-haiku-4"),
    _model("claude-code", "claude-opus-4-8"),
    _model("openai-codex", "gpt-5.6-terra"),
    _model("openai-codex", "gpt-5.5"),
    _model("opencode-go", "deepseek-v4-flash"),
    _model("opencode-go", "deepseek-v4-pro"),
    _model("deepseek", "deepseek-v4-flash"),
]


def _patch_registry(monkeypatch, models, unconfigured=()):
    """Fake the registry. ``unconfigured`` names providers that are enabled
    but have no working credential — the real machine's auth store must
    never decide a unit test's outcome."""
    monkeypatch.setattr(providers, "get_models", lambda provider=None: list(models))
    known = {(m.provider, m.id) for m in models}
    monkeypatch.setattr(
        providers, "get_model",
        lambda p, mid: _model(p, mid) if (p, mid) in known else None,
    )
    monkeypatch.setattr(
        provider_metadata, "is_configured", lambda p: p not in set(unconfigured),
    )


# --- default selection ---

def test_pick_defaults_one_per_provider(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    refs, agg = moa._pick_defaults()
    assert refs == [
        "claude-code:claude-opus-4-8",   # opus beats haiku
        "openai-codex:gpt-5.6-terra",    # terra beats plain 5.5
        "opencode-go:deepseek-v4-pro",   # pro beats flash
    ]
    assert agg == "deepseek:deepseek-v4-flash"  # unused fourth provider


def test_pick_defaults_two_providers_reuses_first_as_aggregator(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY[:4])
    refs, agg = moa._pick_defaults()
    assert refs == ["claude-code:claude-opus-4-8", "openai-codex:gpt-5.6-terra"]
    assert agg == refs[0]


def test_pick_defaults_empty_registry(monkeypatch):
    _patch_registry(monkeypatch, [])
    assert moa._pick_defaults() == ([], None)


def test_pick_defaults_skips_providers_without_a_credential(monkeypatch):
    """An enabled-but-uncredentialed provider (models listed, no key stored)
    fails every call at auth. It must not eat a reference slot — the next
    credentialed provider takes it."""
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY,
                    unconfigured=["claude-code"])
    refs, agg = moa._pick_defaults()
    assert refs == [
        "openai-codex:gpt-5.6-terra",
        "opencode-go:deepseek-v4-pro",
        "deepseek:deepseek-v4-flash",
    ]
    assert agg == refs[0]  # no fourth provider left over


# --- check_fn ---

def test_check_fn_needs_two_providers(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    assert moa._tool_check_fn() is True
    _patch_registry(monkeypatch, [m for m in FOUR_PROVIDER_REGISTRY
                                  if m.provider == "claude-code"])
    assert moa._tool_check_fn() is False
    _patch_registry(monkeypatch, [])
    assert moa._tool_check_fn() is False


def test_check_fn_counts_only_credentialed_providers(monkeypatch):
    """Four providers on paper, one credentialed — MoA has nothing to mix."""
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY, unconfigured=[
        "claude-code", "openai-codex", "opencode-go",
    ])
    assert moa._tool_check_fn() is False


# --- execute ---

def _fake_ask(answers: dict[str, tuple[str, bool]], seen: list[str]):
    async def ask(spec, prompt):
        seen.append(spec)
        text, ok = answers.get(spec, ("boom", False))
        return (spec, text, ok)
    return ask


def test_execute_uses_registry_defaults(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    seen: list[str] = []
    monkeypatch.setattr(moa, "_ask_one", _fake_ask(
        {
            "claude-code:claude-opus-4-8": ("A1", True),
            "openai-codex:gpt-5.6-terra": ("A2", True),
            "opencode-go:deepseek-v4-pro": ("A3", True),
        }, seen))

    agg_calls: list[tuple[str, list]] = []

    async def fake_aggregate(spec, prompt, answers):
        agg_calls.append((spec, answers))
        return "SYNTH"

    monkeypatch.setattr(moa, "_aggregate", fake_aggregate)

    out = asyncio.run(moa.execute(user_prompt="q"))
    assert len(seen) == 3 and len({s.split(":")[0] for s in seen}) == 3
    assert agg_calls[0][0] == "deepseek:deepseek-v4-flash"
    assert "SYNTH" in out


def test_execute_explicit_references_pass_through(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    seen: list[str] = []
    monkeypatch.setattr(moa, "_ask_one", _fake_ask(
        {
            "claude-code:claude-haiku-4": ("A1", True),
            "openai-codex:gpt-5.5": ("A2", True),
        }, seen))

    async def fake_aggregate(spec, prompt, answers):
        return f"AGG:{spec}"

    monkeypatch.setattr(moa, "_aggregate", fake_aggregate)

    out = asyncio.run(moa.execute(
        user_prompt="q",
        references=["claude-code:claude-haiku-4", "openai-codex:gpt-5.5",
                    "claude-code:claude-haiku-4"],  # duplicate is dropped
        aggregator="openai-codex:gpt-5.6-terra",
    ))
    assert seen == ["claude-code:claude-haiku-4", "openai-codex:gpt-5.5"]
    assert "AGG:openai-codex:gpt-5.6-terra" in out


def test_execute_unknown_reference_lists_registry(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    out = asyncio.run(moa.execute(
        user_prompt="q", references=["nope:model-x"]))
    assert "unknown model spec" in out
    assert "nope:model-x" in out
    assert "claude-code:claude-opus-4-8" in out  # registry listed


def test_execute_single_success_skips_aggregator(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    seen: list[str] = []
    monkeypatch.setattr(moa, "_ask_one", _fake_ask(
        {"claude-code:claude-opus-4-8": ("ONLY", True)}, seen))

    async def fail_aggregate(*a, **kw):
        raise AssertionError("aggregator must not be called")

    monkeypatch.setattr(moa, "_aggregate", fail_aggregate)

    out = asyncio.run(moa.execute(user_prompt="q"))
    assert "skipped aggregator" in out
    assert "ONLY" in out
    # The other two died — say so, or the caller can't tell a deliberate
    # single-model answer from a lineup that collapsed.
    assert "**Skipped**:" in out
    assert "openai-codex:gpt-5.6-terra (boom)" in out


def test_execute_all_failed_reports_registry(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    monkeypatch.setattr(moa, "_ask_one", _fake_ask({}, []))
    out = asyncio.run(moa.execute(user_prompt="q"))
    assert "too few successful references" in out
    assert "claude-code:claude-opus-4-8" in out


def test_execute_empty_registry(monkeypatch):
    _patch_registry(monkeypatch, [])
    out = asyncio.run(moa.execute(user_prompt="q"))
    assert "registry is empty" in out


# --- retry ---

def test_call_model_retries_once(monkeypatch):
    _patch_registry(monkeypatch, FOUR_PROVIDER_REGISTRY)
    monkeypatch.setattr(moa, "RETRY_BACKOFF_SECONDS", 0)
    calls = {"n": 0}

    async def flaky_complete(model, ctx, opts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return SimpleNamespace(content=[{"type": "text", "text": "OK"}])

    monkeypatch.setattr(providers, "complete_simple", flaky_complete)

    text, ok = asyncio.run(moa._call_model(
        "claude-code:claude-opus-4-8", "", "q", 0.6,
        label="moa:proposer", attempts=2))
    assert ok is True and text == "OK" and calls["n"] == 2
