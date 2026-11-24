"""Shared pytest configuration, fixtures, and markers for the test suite."""

import os
from pathlib import Path
import sys

import pytest

# Ensure the project root is on sys.path for local development
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests that write memory cite chats from an in-memory session store.
pytest_plugins = ["tests.support.memory_chats"]

_HOST_HOME = Path.home()

# A run started from a git hook, or a shell that exported them, carries
# GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE for the real checkout; every ``git``
# a test runs in its own temporary repository would then act on the checkout
# instead (``git init`` there rewrites its config). Cleared before anything
# runs; tests/support/host_guard.py explains the rest of the guard.
from tests.support import host_guard as _host_guard  # noqa: E402

for _var in _host_guard.GIT_LOCATION_VARIABLES:
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _real_repository_and_state_untouched(request):
    """Fail the test that changed the checkout or the person's real state.

    First autouse fixture, so its teardown runs after every other fixture's
    and also catches what they do. The audit hook already refused the
    operation; this names the test even when the code under test swallowed
    the refusal."""
    guard = _HOST_GUARD
    if guard is None:
        yield
        return
    leftover = guard.take()
    guard.current_test = request.node.nodeid
    yield
    found = leftover + guard.take()
    guard.current_test = "<between tests>"
    if found:
        pytest.fail("test isolation broken — reached the real repository or state:\n  "
                    + "\n  ".join(found), pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _real_repository_unchanged_by_this_run():
    """Backstop for child processes the audit hook cannot see: the checkout's
    config, and worktrees registered into it from this run's test home."""
    if _HOST_GUARD is None:
        yield
        return
    before = _host_guard.snapshot(_GIT_COMMON_DIR, Path(_TEST_HOME))
    yield
    problems = _host_guard.compare(before, _host_guard.snapshot(_GIT_COMMON_DIR, Path(_TEST_HOME)))
    problems += _HOST_GUARD.take()
    if problems:
        pytest.fail("test isolation broken during this run:\n  " + "\n  ".join(problems), pytrace=False)


@pytest.fixture(autouse=True)
def _no_system_notifications(monkeypatch):
    """No test pops a real notification or leaves a report's waiting thread;
    yields the real functions for the tests of them."""
    from types import SimpleNamespace

    from openprogram.runtime.conversation.always_on import runtime
    from openprogram.runtime.scheduler import reminders

    real = SimpleNamespace(system_notify=reminders.system_notify,
                           report_fallback=runtime._system_notification_fallback)
    monkeypatch.setattr(reminders, "system_notify", lambda *_args: True)
    monkeypatch.setattr(runtime, "_system_notification_fallback", lambda *_args: None)
    yield real


@pytest.fixture(autouse=True)
def _tests_leave_the_front_application_alone(monkeypatch):
    """No test reads or changes which application is in front of the person running it."""
    from openprogram.host._platform import front

    monkeypatch.setattr(front, "desktop", lambda: None)
    yield


@pytest.fixture(autouse=True)
def _no_system_settings_guide(monkeypatch):
    """No test opens System Settings or the permission drag guide; a test sees
    what would have started, and gets the real starter for the tests of it."""
    from openprogram.host.system_access import guide

    real = guide._begin
    monkeypatch.setattr(guide, "_begin", lambda capabilities: list(capabilities))
    yield real


@pytest.fixture(autouse=True)
def _no_browser_of_its_own(monkeypatch):
    """A test's turn has the browser only when the test gives it an App window, on a
    machine with a browser installed as on one without; yields the real check."""
    from openprogram.host.resources import background_browser

    real = background_browser.available
    monkeypatch.setattr(background_browser, "available", lambda: False)
    yield real


@pytest.fixture(autouse=True)
def _no_provider_keys_from_the_shell(monkeypatch):
    """A provider key variable set in the developer's shell would show up as
    a read-only credential row (``models/auth/store.py`` ``env_rows``); tests
    start without any and set the ones they need."""
    import os as _os
    from openprogram.models.auth.login.login_method_registry import listed_providers, provider_auth

    names = {n for p in listed_providers() for n in (provider_auth(p).env_vars or ())}
    for name in list(_os.environ):
        if name in names or name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _port_and_profile_variables_end_as_they_began():
    """``openprogram web --web-port`` and ``--profile`` set ``OPENPROGRAM_WEB_PORT``
    and ``OPENPROGRAM_PROFILE`` for their own process; a test that runs them
    would hand the port or the profile to every test after it in the same
    process: a worker started there finds the port taken, and a script it
    starts reads another profile's files."""
    import os as _os

    names = ("OPENPROGRAM_WEB_PORT", "OPENPROGRAM_PROFILE")
    before = {name: _os.environ.get(name) for name in names}
    yield
    for name, value in before.items():
        if value is None:
            _os.environ.pop(name, None)
        else:
            _os.environ[name] = value


@pytest.fixture(autouse=True)
def _this_process_writes_main_session_logs():
    """Tests stand where the worker stands: appends land in ``main.jsonl``."""
    from openprogram.state.store.session.files import session_log

    session_log.claim_main_writer()
    yield

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
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "OPENPROGRAM_PROXY_URL",
    ):
        os.environ.pop(_var, None)

    # The Codex adapter speaks the ChatGPT backend's WebSocket before HTTP;
    # the tests' fakes of the backend speak HTTP, and a test never reaches
    # the network. The socket's own tests turn it back on.
    os.environ["OPENPROGRAM_CODEX_WEBSOCKET"] = "0"

    # Pin urllib's OS-settings fallback (macOS System Preferences / Windows
    # registry) to env-only, both for httpx's already-imported copy and for
    # late imports.
    import urllib.request  # noqa: E402

    urllib.request.getproxies = urllib.request.getproxies_environment
    # The managed proxy resolver also reads system routes when the process
    # has only NO_PROXY; keep those platform readers isolated too.
    for _reader in ("getproxies_macosx_sysconf", "getproxies_registry"):
        if hasattr(urllib.request, _reader):
            setattr(urllib.request, _reader, dict)
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
# runs on every index load, rewrites ``index.json`` and moves directories it
# judges shells into ``.quarantine``, so an unisolated run alters real
# conversations.
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

    _TEST_HOME_MANAGER = tempfile.TemporaryDirectory(prefix="openprogram-test-home-")
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
else:
    _TEST_HOME = str(_HOST_HOME)

# Every process of the run (each xdist worker imports this file) refuses
# changes to the checkout and, unless the run opted into the real home, to
# ~/.openprogram*. See tests/support/host_guard.py.
_REPO_TOPS, _GIT_COMMON_DIR = _host_guard.repository_of(ROOT)
_HOST_GUARD = _host_guard.Guard(
    _REPO_TOPS, _GIT_COMMON_DIR, _HOST_HOME, Path(_TEST_HOME),
    guard_state=os.environ.get("OPENPROGRAM_TEST_REAL_HOME") != "1",
)
_host_guard.install(_HOST_GUARD)


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
        from openprogram.runtime.conversation.turn_runtime import _load_agent_profile
        from openprogram.runtime.conversation.model_tools import resolve_model

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
    from openprogram.programs._host.runtime import snapshot_registry

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
    from openprogram.programs._host import runtime as _R

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
def _keep_builtin_provider_flag_honest():
    """Let ``register_builtins`` run again when its registration was lost.

    ``register_builtins`` records that it ran in a module flag. A test that
    swaps ``api_registry._registry`` for an empty dict and then (directly or
    through a lazy import) triggers the built-in registration fills the
    swapped dict and sets the flag; once monkeypatch restores the real
    registry, the flag still says "registered" and every later caller in
    that worker process sees no built-in providers. Runs after monkeypatch
    teardown, so it sees the real registry.
    """
    yield
    from openprogram.models.providers.catalog import api_registry, register

    if register._registered and "openai-codex" not in api_registry._registry:
        register._registered = False


@pytest.fixture(autouse=True)
def _reset_process_sandbox_policy():
    """Un-pin a process-wide sandbox policy a test left installed.

    ``install_policy_snapshot`` writes a module global on purpose: a cron
    worker and an ``Agent method`` subprocess each run one job and
    must not be widened afterwards. Under pytest every test shares that
    one process, so a test that runs the real ``_run_prompt_job`` pins a
    workspace-write policy for the whole rest of the session. Later tests
    then run their commands under a real bwrap/Seatbelt sandbox they never
    asked for — which is how CI failed on Linux while macOS stayed green.
    """
    from openprogram.host import sandbox

    yield
    sandbox._process_policy_override = sandbox._NO_PROCESS_POLICY


@pytest.fixture(autouse=True)
def _reset_browser_operation_forwarding():
    """Stop handing browser operations to a parent a test's in-process child body installed.

    An agentic subprocess routes its Page operations to the worker; under
    pytest that child body runs in the shared process, and later tests would
    see their reports vanish into the dead sink.
    """
    yield
    from openprogram.programs.applications.browser import pages

    pages.forward_operations_to_parent(None)


@pytest.fixture(autouse=True)
def _forget_learned_provider_limits():
    """Clear what the process learned about providers and conversations.

    A provider's byte cap (body_cap), a model's prefill refusal (prefill),
    a conversation's compaction failures
    (compaction.hold) and a conversation's credential (its binding) are
    kept for the process's life on purpose. Under pytest every test shares
    that process, so one test's refusals would hold another's compaction
    or fit its requests.
    """
    yield
    from openprogram.models.auth.selection import forget_bindings
    from openprogram.models.providers.utils.failures import body_cap, prefill
    from openprogram.runtime.context.compaction.hold import default_hold

    body_cap.forget()
    prefill.forget()
    default_hold.clear()
    forget_bindings()


@pytest.fixture(autouse=True)
def _quota_reads_stay_in_their_test(monkeypatch):
    """No test reads a provider's quota endpoint on a background thread, and
    the quota one test observed is not held for the next."""
    from openprogram.models.usage import quota, quota_fetchers

    monkeypatch.setattr(quota_fetchers, "_spawn", lambda work: None)
    yield
    quota.default_store.reset()
    quota.default_store.set_listener(None)
    quota.forget_accounts()
    quota_fetchers.reset_policy()


@pytest.fixture(autouse=True)
def _screen_reads_as_unlocked(monkeypatch):
    """Keep the machine's lock state out of tests of the macOS desktop backend.

    The backend stops application reads and steps while the Mac's screen is
    locked. A run started before a break, or over SSH on a locked Mac,
    would otherwise fail every test that steps through the real backend.
    """
    from openprogram.programs.tools.computer_use.computer_use.desktop import macos

    monkeypatch.setattr(macos, "_screen_locked", lambda: False)


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
def _subprocess_entry_stays_in_its_test():
    """Tests call a function subprocess's entry point in this process."""
    yield
    from openprogram.runtime.agent import questions
    from openprogram.runtime.execution import worker_calls
    from openprogram.runtime.execution.store import run_tables

    worker_calls._calls = None
    run_tables._process_only_reads = False
    questions._send_live_question_to_parent = None


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
    from openprogram.state.store.session import session_store

    cached = session_store.shared._default_store
    if cached is None:
        return
    if "openprogram-test-home" not in str(getattr(cached, "root_path", "")):
        session_store.shared._default_store = None


@pytest.fixture(autouse=True)
def _forget_worktree_binding():
    """A tool that switches the turn to a worktree leaves the binding set on
    purpose, for the rest of that turn. A test calling such a tool directly
    has no turn to end it, so it would reach whatever test runs next here."""
    yield
    from openprogram.state.worktree.context import clear_worktree

    clear_worktree()


# The home every test in this worker shares unless it points its state
# elsewhere (resolved after the redirect above).
_SHARED_HOME = Path.home().resolve()


@pytest.fixture(autouse=True)
def _own_execution_store(tmp_path_factory, monkeypatch):
    """Each test records executions in its own database.

    The execution store otherwise lives in the worker's shared test home, so
    an execution one test leaves unfinished for session "p1" makes a later
    test on the same worker see "p1" as busy: its spawn is not admitted and
    its idle notice never goes out. Which tests share a worker depends on
    xdist's scheduling, so such a failure shows up on one platform only.

    A test that already points the state directory at its own folder keeps
    its database there; only the shared home is replaced. The replacement
    sits outside ``tmp_path`` so tests that list their own temporary
    directory do not see it.
    """
    from openprogram.state import paths

    folder = tmp_path_factory.mktemp("executions")

    def execution_db_path() -> Path:
        state = paths.get_state_dir()
        if Path(state).resolve().is_relative_to(_SHARED_HOME):
            return folder / "executions.db"
        return Path(state) / "executions.db"

    monkeypatch.setattr(paths, "get_run_state_path", execution_db_path)
