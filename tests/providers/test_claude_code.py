"""Tests for ClaudeCodeRuntime — model resolution + effort plumbing.

These tests spawn the real Claude Code CLI (subscription mode) and verify
the runtime actually loads the model we request, not a CLI default or a
routing helper. They are marked `slow` because each model probe takes
~3-10s and requires an interactive Claude Code subscription.

Run just these:
    pytest tests/providers/test_claude_code.py -m slow

Skip them:
    pytest -m "not slow"
"""
from __future__ import annotations

import shutil
import time

import pytest

from openprogram.legacy_providers.claude_code import ClaudeCodeRuntime
from openprogram.legacy_providers.claude_models import load_claude_models


pytestmark = pytest.mark.slow

_CLI = shutil.which("claude")
_skip_no_cli = pytest.mark.skipif(_CLI is None, reason="claude CLI not installed")


def _usable_models() -> list[dict]:
    """Registry entries we can actually exercise (skip those needing extra_usage
    since the subscription may not have it enabled)."""
    data = load_claude_models()
    return [m for m in data["models"] if not m["requires_extra_usage"]]


@_skip_no_cli
@pytest.mark.parametrize("entry", _usable_models(), ids=lambda m: m["id"])
def test_model_actually_loads(entry: dict):
    """Spawn CLI with --model <id> and verify the turn runs on that exact model.

    Checks:
      * reply is non-empty and doesn't contain "API Error"
      * `_resolved_model_id` (picked from `modelUsage` by matching `system.model`)
        equals the requested id — proves we're not getting a silent alias or
        the haiku routing helper
      * `_context_window_tokens` matches what the registry claims
    """
    rt = ClaudeCodeRuntime(model=entry["id"], timeout=120)
    try:
        reply = rt._call(
            content=[{"type": "text", "text": "reply with exactly: ok"}],
            model=entry["id"],
        )
        assert reply, f"empty reply from {entry['id']!r}"
        assert "API Error" not in reply, f"backend error for {entry['id']!r}: {reply[:200]!r}"
        assert rt._resolved_model_id == entry["id"], (
            f"requested {entry['id']!r} but CLI resolved to {rt._resolved_model_id!r}"
        )
        assert rt._context_window_tokens == entry["context_window"], (
            f"{entry['id']!r}: registry says ctx={entry['context_window']} but "
            f"CLI reported {rt._context_window_tokens}"
        )
    finally:
        rt.close()


@_skip_no_cli
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", "auto"])
def test_effort_levels_do_not_break_chat(effort: str):
    """Each effort level must produce a valid reply on a baseline model.

    Uses Sonnet 200K (never requires extra_usage). We don't assert on
    reasoning depth — just that the `--settings defaultEffortLevel` plumbing
    doesn't corrupt the turn or cause the CLI to reject the flag.
    """
    rt = ClaudeCodeRuntime(model="claude-sonnet-4-6", timeout=120)
    rt._thinking_effort = effort
    try:
        reply = rt._call(
            content=[{"type": "text", "text": "reply with exactly: ok"}],
            model="claude-sonnet-4-6",
        )
        assert reply, f"empty reply at effort={effort!r}"
        assert "API Error" not in reply, f"backend error at effort={effort!r}: {reply[:200]!r}"
        assert rt._resolved_model_id == "claude-sonnet-4-6", (
            f"effort={effort!r} resolved to {rt._resolved_model_id!r} "
            f"(expected claude-sonnet-4-6)"
        )
    finally:
        rt.close()


@_skip_no_cli
def test_list_models_matches_registry():
    """Quick sanity: list_models() returns exactly what's in the JSON."""
    rt = ClaudeCodeRuntime()
    try:
        ids = rt.list_models()
    finally:
        rt.close()
    data = load_claude_models()
    assert ids == [m["id"] for m in data["models"]]
