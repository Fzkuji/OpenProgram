"""Shared pytest configuration, fixtures, and markers for the test suite."""

from pathlib import Path
import sys

import pytest

# Ensure the project root is on sys.path for local development
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Shared mock call functions (used across multiple test files)
# ---------------------------------------------------------------------------

def echo_call(content, model="test", response_format=None):
    """Mock LLM that echoes the last text block."""
    for block in reversed(content):
        if block["type"] == "text":
            return block["text"]
    return ""


def sync_echo(content, model="test", response_format=None):
    """Sync echo — identical to echo_call, named for clarity in async tests."""
    return echo_call(content, model, response_format)


async def async_echo(content, model="test", response_format=None):
    """Async echo — returns last text block."""
    return echo_call(content, model, response_format)


def noop_call(content, model="test", response_format=None):
    """Mock LLM that always returns 'ok'."""
    return "ok"


# ---------------------------------------------------------------------------
# Environment probes for conditional skips (keep CI green without masking
# real failures — see docs/design or the providers/integration tests).
# ---------------------------------------------------------------------------

def _has_default_provider() -> bool:
    """True if a usable default LLM provider resolves (CLI or AuthStore key).

    False in a bare CI checkout (no codex/gemini CLI, no API key). Tests that
    genuinely need a configured model use ``no_provider`` to skip there
    instead of failing with "No LLM provider / model configured".
    """
    try:
        from openprogram.providers.registry import resolve_default_provider
        resolve_default_provider()
        return True
    except Exception:
        return False


# A reusable skip marker: applied to tests that can only run with a real
# provider / model configured.
no_provider = pytest.mark.skipif(
    not _has_default_provider(),
    reason="no LLM provider/model configured (bare CI) — test needs one",
)


# Integration tests that need a LIVE external service (a working MCP
# subprocess, the Claude QR-login backend, …) — these fail in a bare CI
# checkout and even locally without the service. Opt in by setting
# OPENPROGRAM_LIVE_TESTS=1; otherwise they skip instead of failing.
import os as _os
requires_live_service = pytest.mark.skipif(
    _os.environ.get("OPENPROGRAM_LIVE_TESTS") != "1",
    reason="needs a live external service (set OPENPROGRAM_LIVE_TESTS=1 to run)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def echo_runtime():
    """A Runtime that echoes the last text block back."""
    from openprogram.agentic_programming.runtime import Runtime
    return Runtime(call=echo_call, model="test")


@pytest.fixture
def noop_runtime():
    """A Runtime that always returns 'ok'."""
    from openprogram.agentic_programming.runtime import Runtime
    return Runtime(call=noop_call, model="test")


