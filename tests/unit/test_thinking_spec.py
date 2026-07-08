"""Test thinking_spec — loading thinking config and translating levels.

Since Task 6 the config lives under provider.json's ``thinking`` key
(folded from the old standalone thinking.json). These tests exercise the
primary in-tree path; the legacy standalone fallback + DeprecationWarning
is covered by ``test_legacy_standalone_fallback``.
"""
import json
import warnings

import pytest

from openprogram.providers import thinking_spec
from openprogram.providers import cache_spec
from openprogram.providers.thinking_spec import (
    derive_thinking_levels,
    get_default_effort,
    get_model_variant,
    get_thinking_spec,
    invalidate_cache,
    translate_reasoning,
)


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def test_load_anthropic():
    spec = get_thinking_spec("anthropic")
    assert spec["wire_format"] == "effort_string"
    assert "effort_map" in spec
    assert spec["default_effort"] == "high"


def test_load_google():
    spec = get_thinking_spec("google")
    assert spec["wire_format"] == "budget_tokens"
    assert "budget_map" in spec


def test_load_github_copilot_no_thinking():
    spec = get_thinking_spec("github-copilot")
    assert spec["wire_format"] == "none"


def test_load_nonexistent_provider_uses_fallback():
    spec = get_thinking_spec("nonexistent-provider")
    assert spec.get("_fallback") is True
    assert spec["wire_format"] == "effort_string"
    assert "low" in spec["effort_map"]
    assert "medium" in spec["effort_map"]
    assert "high" in spec["effort_map"]


def test_hyphen_to_underscore():
    spec = get_thinking_spec("openai-codex")
    assert spec["wire_format"] == "effort_string"
    assert spec["effort_map"]["xhigh"] == "xhigh"


def test_translate_anthropic_effort():
    assert translate_reasoning("anthropic", "claude-opus-4-8", "high") == "high"
    assert translate_reasoning("anthropic", "claude-opus-4-8", "minimal") == "low"
    assert translate_reasoning("anthropic", "claude-opus-4-8", "max") == "max"


def test_translate_google_budget():
    val = translate_reasoning("google", "gemini-3-pro", "high")
    assert val == 24576
    assert isinstance(val, int)


def test_translate_openai_codex_direct_pass():
    assert translate_reasoning("openai-codex", "gpt-5.5", "xhigh") == "xhigh"
    assert translate_reasoning("openai-codex", "gpt-5.5", "max") == "xhigh"


def test_translate_openai_completions_clamp():
    assert translate_reasoning("openai-completions", "o3", "xhigh") == "high"
    assert translate_reasoning("openai-completions", "o3", "max") == "high"


def test_translate_no_thinking_provider():
    assert translate_reasoning("github-copilot", "copilot-chat", "high") is None


def test_model_variant():
    # opus-4-7 override no longer carries "variant" after auto-update
    assert get_model_variant("anthropic", "claude-opus-4-8") is None


def test_derive_levels_reasoning_true():
    levels = derive_thinking_levels("anthropic", "claude-opus-4-8", True)
    assert "high" in levels
    assert "max" in levels
    assert len(levels) == 5  # low,medium,high,xhigh,max (from API caps)


def test_derive_levels_reasoning_false():
    levels = derive_thinking_levels("anthropic", "claude-opus-4-8", False)
    assert levels == []


def test_derive_levels_no_thinking_provider():
    levels = derive_thinking_levels("github-copilot", "copilot", True)
    assert levels == []


def test_derive_levels_model_override():
    levels = derive_thinking_levels("anthropic", "claude-opus-4-7", True)
    # opus-4-7 has effort_map override from API caps: low,medium,high,xhigh,max
    assert len(levels) == 5
    assert "xhigh" in levels


def test_default_effort():
    assert get_default_effort("anthropic") == "high"
    assert get_default_effort("openai-codex") == "xhigh"
    assert get_default_effort("google") == "medium"
    assert get_default_effort("github-copilot") is None


def test_reads_from_provider_json_no_deprecation():
    """In-tree providers read the folded provider.json['thinking'] block —
    the legacy standalone fallback must NOT fire (no DeprecationWarning)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert get_thinking_spec("anthropic")["wire_format"] == "effort_string"
        assert cache_spec.get_cache_spec("anthropic")["mode"] == "explicit"


def test_legacy_standalone_fallback(tmp_path, monkeypatch):
    """An out-of-tree provider dir with only a standalone thinking.json still
    loads, and hitting the fallback emits a DeprecationWarning (kept one
    version for community dirs not yet folded into provider.json)."""
    pdir = tmp_path / "community_x"
    pdir.mkdir()
    (pdir / "thinking.json").write_text(
        json.dumps({"wire_format": "effort_string", "effort_map": {"low": "low"}})
    )
    (pdir / "cache.json").write_text(json.dumps({"mode": "auto"}))
    monkeypatch.setattr(thinking_spec, "_PROVIDERS_DIR", tmp_path)
    invalidate_cache()
    cache_spec.invalidate_cache()
    with pytest.warns(DeprecationWarning):
        assert get_thinking_spec("community-x")["effort_map"] == {"low": "low"}
    with pytest.warns(DeprecationWarning):
        assert cache_spec.get_cache_spec("community-x")["mode"] == "auto"
