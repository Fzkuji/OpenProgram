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


