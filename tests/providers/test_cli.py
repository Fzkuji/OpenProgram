"""Tests for CLI-based providers (Codex, Claude Code, Gemini CLI)."""

import base64
import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, mock_open

from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

# Note: subprocess-based OpenAICodexRuntime tests were removed when the
# runtime switched to HTTP direct (chatgpt.com/backend-api or api.openai.com).
# Re-add targeted httpx-mocked tests when the HTTP shape stabilizes.
def test_visualizer_codex_runtime_enables_search(monkeypatch):
    """Visualizer Codex keeps default auto-session and enables native web search."""
    from openprogram.webui import server

    captured = {}

    def fake_create_runtime(provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("openprogram.legacy_providers.create_runtime", fake_create_runtime)

    server._create_runtime_for_visualizer("openai-codex")

    assert captured["provider"] == "openai-codex"
    assert "session_id" not in captured["kwargs"]
    assert captured["kwargs"]["search"] is True



# ClaudeCodeRuntime unsupported-modality coverage now lives in
# tests/unit/test_claude_code_runtime.py (runs against the real CliRunner
# with a fake ``claude`` binary, not ``subprocess.Popen`` mocks).


# ══════════════════════════════════════════════════════════════
# Provider lazy import tests
# ══════════════════════════════════════════════════════════════

# GeminiCLIRuntime subprocess tests removed — the runtime is now
# HTTP-direct against cloudcode-pa.googleapis.com via OAuth (same
# ~/.gemini/oauth_creds.json the CLI writes). Auth helpers are covered
# by tests/unit/test_google_gemini_cli_runtime_auth.py. Block-filtering
# semantics for the new runtime live inside the shared provider stream
# path, which is covered by providers/_shared tests.
