"""Shared pytest configuration, fixtures, and markers for the test suite."""

import os
from pathlib import Path
import sys

import pytest

# Ensure the project root is on sys.path for local development
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_HOST_HOME = Path.home()

# ---------------------------------------------------------------------------
# Network isolation: the suite must not depend on the host's proxy setup.
# A developer shell with HTTP(S)_PROXY / a socks ALL_PROXY / a macOS
# system-level proxy would otherwise route the integration tests' localhost
# requests through the proxy (hanging them) and flip httpx's proxy-mount
# construction. Applied at import time so it precedes every client built
# during collection. Tests that exercise proxy resolution itself
# (tests/component/security/test_http_proxy.py) set their own env via monkeypatch.
#
# Live smoke tests DO need the host's real network, proxy included — run those
# as ``OPENPROGRAM_TEST_LIVE=1 pytest -m live tests/live`` to
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
# State isolation: the suite must never touch the developer's real
# ``~/.openprogram``.
#
# ``get_state_dir()`` resolves off ``Path.home()``, so a test that builds a
# SessionStore, writes config, or starts a worker lands in the live data
# directory. That is not merely untidy: ``SessionStore._startup_cleanup``
# runs on every index load and ``shutil.rmtree``s any session it judges an
# empty shell, so an unisolated run can DELETE real conversations.
#
# Redirecting HOME here — at import time, before any test module resolves a
# path — puts every default-profile lookup under a per-run temp dir.
# Individual tests that want their own scratch state still pass ``tmp_path``;
# this is the floor, not a replacement.
#
# Opt out with OPENPROGRAM_TEST_REAL_HOME=1 when a test genuinely needs the
# developer's own state (nothing in-tree does today).
# ---------------------------------------------------------------------------
if os.environ.get("OPENPROGRAM_TEST_REAL_HOME") != "1":
    import tempfile  # noqa: E402

    _TEST_HOME_MANAGER = tempfile.TemporaryDirectory(
        prefix="openprogram-test-home-"
    )
    _TEST_HOME = _TEST_HOME_MANAGER.name
    os.environ["HOME"] = _TEST_HOME
    os.environ["USERPROFILE"] = _TEST_HOME
    Path(_TEST_HOME).mkdir(parents=True, exist_ok=True)

    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        cache_root = None
        if sys.platform == "darwin":
            cache_root = _HOST_HOME / "Library/Caches/ms-playwright"
        elif sys.platform.startswith("linux"):
            cache_root = _HOST_HOME / ".cache/ms-playwright"
        if cache_root is not None and cache_root.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache_root)


# ---------------------------------------------------------------------------
# Environment probes for conditional skips (keep CI green without masking
# real failures — see docs/design or the providers/integration tests).
# ---------------------------------------------------------------------------

def _has_default_provider() -> bool:
    """True if a real model call can actually be made here.

    Resolves a model the way the agent does, rather than asking
    ``detect_provider()`` whether a CLI binary exists. The two disagree:
    with the codex CLI on PATH but no enabled models in the (isolated)
    state dir, detection says yes while the agent raises "No model is
    configured" — so the skip marker let provider-dependent tests run
    straight into that error.

    False in a bare CI checkout, and under the temp HOME this suite runs
    in unless the developer's config was copied there.
    """
    try:
        from openprogram.agent.dispatcher import _load_agent_profile
        from openprogram.agent.internals._model_tools import resolve_model
        return resolve_model(_load_agent_profile("main") or {}) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shipped-tool registry safety net
# ---------------------------------------------------------------------------
#
# The tool registry is process-global and populated once, at import, by the
# @function side-effect imports in openprogram.programs.tools. Several tests
# legitimately clear / rebuild / channel-filter the registry to exercise it in
# isolation; if any of them leaks (restores an incomplete snapshot, drops a
# tool, or leaves a channel blacklist behind), every *later* test sees a
# registry that's missing shipped tools. Because pytest's file collection order
# is alphabetical and platform-stable, such a leak can bite on CI (Linux) while
# staying dormant locally (macOS) — e.g. the send_message tool going missing
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
    import openprogram.programs  # noqa: F401  (import side-effect: registers tools)
    from openprogram.programs._runtime import snapshot_registry
    return snapshot_registry()


_SHIPPED_REGISTRY = _capture_shipped_registry()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Treat WinError 1314 as a missing test-host capability.

    File symlinks require Developer Mode or a privileged account on Windows.
    Product tests that exercise symlink handling should still run whenever the
    host permits creation, without making that OS setting a suite prerequisite.
    Patch only during the test call: pytest itself creates best-effort temp-dir
    symlinks while setting up ``tmp_path`` and already handles that failure.
    """
    if sys.platform != "win32":
        yield
        return

    original = Path.symlink_to

    def symlink_to(self, target, target_is_directory=False):
        try:
            return original(
                self,
                target,
                target_is_directory=target_is_directory,
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symbolic links")
            raise

    Path.symlink_to = symlink_to
    try:
        yield
    finally:
        Path.symlink_to = original


@pytest.fixture(autouse=True)
def _restore_shipped_tools():
    from openprogram.programs import _runtime as _R

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


@pytest.fixture(autouse=True)
def _reset_process_sandbox_policy():
    """Un-pin a process-wide sandbox policy a test left installed.

    ``install_policy_snapshot`` writes a module global on purpose: a cron
    worker and an ``@agentic_function`` subprocess each run one job and
    must not be widened afterwards. Under pytest every test shares that
    one process, so a test that runs the real ``_run_prompt_job`` pins a
    workspace-write policy for the whole rest of the session. Later tests
    then run their commands under a real bwrap/Seatbelt sandbox they never
    asked for — which is how CI failed on Linux while macOS stayed green.
    """
    from openprogram import sandbox

    yield
    sandbox._process_policy_override = sandbox._NO_PROCESS_POLICY


@pytest.fixture(autouse=True)
def _reset_spawn_fanout():
    """Clear the per-turn spawn fan-out counter between tests.

    It is keyed (session_id, turn_id), which is unique per turn in a
    real run but reused by tests that all spawn from "p1"/"a1" — without
    this, the ninth such test in a session would be refused by a counter
    the previous eight filled.
    """
    from openprogram.programs.tools.agents.agent.agent.agent import _fanout_used
    _fanout_used.clear()
    yield
    _fanout_used.clear()


@pytest.fixture(autouse=True)
def _drop_tmp_rooted_session_store():
    """Forget a process-wide SessionStore a test rooted under its tmp_path.

    ``default_store()`` caches one instance for the process. A test that
    redirects ``get_state_dir`` and then reaches any code calling
    ``default_db()`` leaves that cached store pointing at a tmp directory
    which pytest deletes, so every later test in the same worker shares a
    store rooted outside the test home. Dropping it here rebuilds it from
    whatever the next test's state dir is.
    """
    yield
    from openprogram.store.session import session_store

    cached = getattr(session_store, "_default_store", None)
    if cached is None:
        return
    if "openprogram-test-home" not in str(getattr(cached, "root_path", "")):
        session_store._default_store = None
