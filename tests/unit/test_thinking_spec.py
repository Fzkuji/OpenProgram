"""Test thinking_spec — loading thinking.json and translating levels."""
import pytest

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
    assert translate_reasoning("openai-codex", "gpt-5.5", "max") == "max"


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
