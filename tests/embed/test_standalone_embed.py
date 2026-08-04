"""Embedding contract: the DAG-context function-calling core must work as a
plain library inside somebody else's stack.

The scenario under test is `pip install openprogram` followed by nothing but
``from openprogram import ...``: the host app brings its own LLM client, picks
its own directory for session state, and never starts the webui / TUI / CLI.

Three properties are pinned here, each of which has broken before:

  1. importing the public surface must not drag in a server framework
     (fastapi / uvicorn / textual) — those are optional extras, absent in a
     library install, so an eager import would be an ImportError at the door;
  2. persistence must be reachable through an explicit directory, never via
     ``openprogram.paths`` — an embedded host must not get a ``~/.openprogram``
     written behind its back. Enforced by patching the paths accessors to
     raise, so any implicit call fails loudly instead of silently creating it;
  3. no-store mode must still execute — ``_store`` unset means "run, persist
     nothing", which is what a host that keeps its own trace wants.
"""

from __future__ import annotations

import sys

import pytest


# The optional-extra modules a library install will not have. Asserting on
# absence (rather than installing import-raising stubs) keeps the check honest:
# a stub can be defeated by a cached sys.modules entry, but a missing key
# cannot.
HEAVY_MODULES = ("fastapi", "uvicorn", "textual", "openprogram.webui")


def _heavy_already_loaded() -> set[str]:
    return {m for m in HEAVY_MODULES if m in sys.modules}


def test_public_surface_imports_without_heavy_modules():
    """``from openprogram import ...`` pulls in no server/TUI dependency.

    Runs in a subprocess: the pytest session itself imports the webui in other
    test files, so sys.modules in-process cannot answer this question.
    """
    import subprocess

    code = (
        "import sys\n"
        "from openprogram import agentic_function, Runtime, decision, Session\n"
        "loaded = [m for m in "
        f"{HEAVY_MODULES!r}"
        " if m in sys.modules]\n"
        "assert not loaded, loaded\n"
        "assert callable(agentic_function)\n"
        "assert callable(decision.make)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"embedded import failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "ok" in proc.stdout


def test_top_level_names_are_exported():
    """The four embedding entry points resolve off the package root."""
    import openprogram

    from openprogram import agentic_function, Runtime, decision, Session

    assert agentic_function is openprogram.agentic_function
    assert Runtime is openprogram.Runtime
    assert Session is openprogram.Session
    assert decision is openprogram.decision
    for name in ("agentic_function", "Runtime", "decision", "Session"):
        assert name in openprogram.__all__


# The host's own LLM client — mirrors tests/conftest.echo_call, which is the
# `call=` contract an embedder implements against their SDK of choice.
def echo_call(content, model="test", response_format=None):
    for block in reversed(content):
        if block["type"] == "text":
            return block["text"]
    return ""


@pytest.fixture
def no_implicit_state_dir(monkeypatch):
    """Make every implicit ``~/.openprogram`` lookup fail loudly.

    An embedded run that touches these has leaked host state into the user's
    home directory; the raise turns that from a silent side effect into a
    test failure.
    """
    import openprogram.paths as paths

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "embedded run reached openprogram.paths — state directory must be "
            "passed explicitly, not resolved from ~/.openprogram"
        )

    monkeypatch.setattr(paths, "get_state_dir", _forbidden)
    monkeypatch.setattr(paths, "get_sessions_dir", _forbidden)


def test_function_executes_with_host_supplied_call(no_implicit_state_dir):
    """An @agentic_function runs off a host-provided call fn, no store, no paths."""
    from openprogram import agentic_function, Runtime

    runtime = Runtime(call=echo_call, model="test")

    @agentic_function
    def summarize(text, runtime=None):
        """Echo the text back."""
        return runtime.exec(text)

    assert "hello embedded" in summarize("hello embedded", runtime=runtime)


def test_execution_persists_to_explicit_directory(tmp_path, no_implicit_state_dir):
    """Session state lands in the caller's directory and reads back as a DAG."""
    from openprogram import agentic_function, Runtime
    from openprogram.store import GraphStoreShim, SessionStore, session_scope

    store = SessionStore(tmp_path / "host_sessions")
    store.create_session("embedded", agent_id="main")
    shim = GraphStoreShim(store, "embedded")

    runtime = Runtime(call=echo_call, model="test")

    @agentic_function
    def summarize(text, runtime=None):
        """Echo the text back."""
        return runtime.exec(text)

    with session_scope(store, "embedded"):
        summarize("persist me", runtime=runtime)

    graph = shim.load()
    code_nodes = [n for n in graph if n.is_code() and n.name == "summarize"]
    llm_nodes = [n for n in graph if n.is_llm()]
    assert len(code_nodes) == 1, "the function call is missing from the DAG"
    assert len(llm_nodes) == 1, "the LLM call is missing from the DAG"
    # The llm node hangs off the function frame — this edge is what makes the
    # trace a call tree rather than a flat log.
    assert llm_nodes[0].caller == code_nodes[0].id

    # Written where the host asked, and nowhere else.
    assert (tmp_path / "host_sessions").exists()
    assert (tmp_path / "host_sessions" / "embedded").is_dir()


def test_runs_without_any_store(no_implicit_state_dir):
    """No store installed → executes normally, persists nothing."""
    from openprogram import agentic_function, Runtime
    from openprogram.store import _store

    assert _store.get() is None

    runtime = Runtime(call=echo_call, model="test")

    @agentic_function
    def plain(text, runtime=None):
        """Echo the text back."""
        return runtime.exec(text)

    assert "no persistence" in plain("no persistence", runtime=runtime)


def test_executes_with_webui_absent(no_implicit_state_dir):
    """The exec loop must not need ``openprogram.webui`` to be importable.

    webui is an optional extra (it pulls fastapi/uvicorn), so in a library
    install the module is simply not there. The core reaches cancellation and
    session routing through its own injection seams; the webui claims them on
    import, and its absence leaves the headless defaults in place.
    """
    import builtins

    from openprogram import agentic_function, Runtime

    runtime = Runtime(call=echo_call, model="test")

    @agentic_function
    def summarize(text, runtime=None):
        """Echo the text back."""
        return runtime.exec(text)

    real_import = builtins.__import__
    blocked = ("openprogram.webui", "fastapi", "uvicorn", "textual")

    def without_webui(name, *args, **kwargs):
        if name in blocked or name.startswith("openprogram.webui."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    cached = {m: sys.modules.pop(m) for m in list(sys.modules)
              if m == "openprogram.webui" or m.startswith("openprogram.webui.")}
    builtins.__import__ = without_webui
    try:
        assert "headless" in summarize("headless run", runtime=runtime)
        # No frontend is attached, so there is nobody to answer a question.
        assert runtime.can_ask() is False
    finally:
        builtins.__import__ = real_import
        sys.modules.update(cached)


def test_missing_subsystem_fails_fast(no_implicit_state_dir):
    """A missing optional subsystem raises straight away, without retrying.

    ImportError means a module is absent; no amount of backoff will conjure
    it. Before this was special-cased the default budget burned six attempts
    (~50s of sleeping) before surfacing the same error.
    """
    import builtins
    import time

    from openprogram import agentic_function, Runtime

    runtime = Runtime(call=echo_call, model="test")

    @agentic_function
    def summarize(text, runtime=None):
        """Echo the text back."""
        return runtime.exec(text)

    real_import = builtins.__import__

    def without_agent(name, *args, **kwargs):
        if name == "openprogram.agent" or name.startswith("openprogram.agent."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    cached = {m: sys.modules.pop(m) for m in list(sys.modules)
              if m == "openprogram.agent" or m.startswith("openprogram.agent.")}
    builtins.__import__ = without_agent
    started = time.monotonic()
    try:
        with pytest.raises(ImportError):
            summarize("needs the agent subsystem", runtime=runtime)
    finally:
        builtins.__import__ = real_import
        sys.modules.update(cached)
    # Well under one retry's backoff — proves no retry loop ran.
    assert time.monotonic() - started < 5.0


def test_spec_is_openai_tool_shaped(no_implicit_state_dir):
    """``fn.spec`` converts to the tools format a host's own loop expects."""
    from openprogram import agentic_function
    from openprogram.agentic_programming.tool_format import to_openai_tool

    @agentic_function
    def lookup(city: str, runtime=None):
        """Look up the weather for a city."""
        return runtime.exec(city)

    tool = to_openai_tool(lookup)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "lookup"
    assert tool["function"]["description"]
    params = tool["function"]["parameters"]
    assert params["type"] == "object"
    assert "city" in params["properties"]
    # Runtime-injected params are not LLM-controllable and must not leak.
    assert "runtime" not in params["properties"]
