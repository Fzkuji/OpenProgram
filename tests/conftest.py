"""Shared pytest configuration, fixtures, and markers for the test suite."""

import os
from pathlib import Path
import sys

import pytest

# Ensure the project root is on sys.path for local development
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Network isolation: the suite must not depend on the host's proxy setup.
# A developer shell with HTTP(S)_PROXY / a socks ALL_PROXY / a macOS
# system-level proxy would otherwise route the integration tests' localhost
# requests through the proxy (hanging them) and flip httpx's proxy-mount
# construction. Applied at import time so it precedes every client built
# during collection. Tests that exercise proxy resolution itself
# (tests/test_http_proxy.py) set their own env via monkeypatch.
#
# Live smoke tests (``-m slow``) DO need the host's real network, proxy
# included — run those as ``OPENPROGRAM_TEST_LIVE=1 pytest -m slow`` to
# keep the proxy environment intact.
# ---------------------------------------------------------------------------
if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
    for _var in (
        "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
        "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
        "OPENPROGRAM_PROXY_URL",
    ):
        os.environ.pop(_var, None)

    # Pin urllib's OS-settings fallback (macOS System Preferences / Windows
    # registry) to env-only, both for httpx's already-imported copy and for
    # late imports.
    import urllib.request  # noqa: E402

    urllib.request.getproxies = urllib.request.getproxies_environment
    try:
        import httpx._utils as _httpx_utils  # noqa: E402

        _httpx_utils.getproxies = urllib.request.getproxies_environment
    except Exception:  # pragma: no cover - httpx always present in practice
        pass


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
# Shipped-tool registry safety net
# ---------------------------------------------------------------------------
#
# The tool registry is process-global and populated once, at import, by the
# @function side-effect imports in openprogram.functions.tools. Several tests
# legitimately clear / rebuild / channel-filter the registry to exercise it in
# isolation; if any of them leaks (restores an incomplete snapshot, drops a
# tool, or leaves a channel blacklist behind), every *later* test sees a
# registry that's missing shipped tools. Because pytest's file collection order
# is alphabetical and platform-stable, such a leak can bite on CI (Linux) while
# staying dormant locally (macOS) — e.g. the message_branch tool going missing
# from test_session_config_tools_intent only under the CI ordering.
#
# This autouse fixture snapshots the fully-loaded shipped registry ONCE at
# session start and, before every test, re-inserts any shipped tool a previous
# test leaked away — without touching ad-hoc tools a test adds on purpose. It's
# a belt-and-suspenders guard: individual tests should still clean up after
# themselves, but no single leak can cascade into unrelated failures.

# Capture the pristine shipped registry at conftest import time — i.e. before
# any test (and any test's registry-clearing fixture) has run — so the snapshot
# is guaranteed complete. A lazily-built session fixture could otherwise be
# instantiated inside a test that had already cleared the registry.
def _capture_shipped_registry():
    import openprogram.functions  # noqa: F401  (import side-effect: registers tools)
    from openprogram.functions._runtime import snapshot_registry
    return snapshot_registry()


_SHIPPED_REGISTRY = _capture_shipped_registry()


@pytest.fixture(autouse=True)
def _restore_shipped_tools():
    from openprogram.functions import _runtime as _R

    snap = _SHIPPED_REGISTRY
    # Re-insert any shipped tool that's gone missing, and re-establish its
    # toolset membership / exposure. Leave everything else (ad-hoc tools a
    # test added, unrelated state) untouched so we don't fight intentional
    # per-test setups.
    for name, tool in snap["registry"].items():
        if name not in _R._registry:
            _R._registry[name] = tool
    for name, sets in snap["toolset_membership"].items():
        _R._toolset_membership.setdefault(name, set()).update(sets)
    # Exposure: a shipped tool must not be left flagged internal-only by a
    # leak. Clear the opt-out for shipped tools that shipped as exposed.
    for name in snap["registry"]:
        if name not in snap["unexposed"]:
            _R._unexposed.discard(name)
    # Channel blacklist: a leaked unsafe_in entry would hide a shipped tool
    # from a whole transport. Reset shipped tools' channel sets to what they
    # shipped with (empty for all current shipped tools).
    for name in snap["registry"]:
        shipped = snap["unsafe_in_channel"].get(name)
        if shipped is None:
            _R._unsafe_in_channel.pop(name, None)
        else:
            _R._unsafe_in_channel[name] = set(shipped)
    yield


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


