"""Embedding contract: the DAG-context function-calling core must work as a
plain library inside somebody else's stack.

The scenario under test is importing OpenProgram from a source-development
environment: the host app brings its own LLM client, picks
its own directory for session state, and never starts the webui / TUI / CLI.

Three properties are pinned here, each of which has broken before:

  1. importing the public surface must not drag in a server framework
     (fastapi / uvicorn / textual) — those are optional extras, absent in a
     library install, so an eager import would be an ImportError at the door;
  2. persistence must be reachable through an explicit directory, never via
     ``openprogram.state.paths`` — an embedded host must not get a ``~/.openprogram``
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
HEAVY_MODULES = ("fastapi", "uvicorn", "textual", "openprogram.interfaces.server")
# The tool loop that runs an Agent method; the missing-subsystem test hides it.
BLOCKED = "openprogram.runtime.agent.loop"


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
        "from openprogram import Agent, Runtime, decision, Session\n"
        "loaded = [m for m in "
        f"{HEAVY_MODULES!r}"
        " if m in sys.modules]\n"
        "assert not loaded, loaded\n"
        "assert callable(Agent)\n"
        "assert callable(decision.make)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"embedded import failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "ok" in proc.stdout


def test_top_level_names_are_exported():
    """The four embedding entry points resolve off the package root."""
    import openprogram

    from openprogram import Runtime, decision, Session
    from openprogram import Agent

    assert Agent is openprogram.Agent
    assert Runtime is openprogram.Runtime
    assert Session is openprogram.Session
    assert decision is openprogram.decision
    for name in ("Agent", "Runtime", "decision", "Session"):
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
    import openprogram.state.paths as paths

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "embedded run reached openprogram.state.paths — state directory must be "
            "passed explicitly, not resolved from ~/.openprogram"
        )

    monkeypatch.setattr(paths, "get_state_dir", _forbidden)
    monkeypatch.setattr(paths, "get_sessions_dir", _forbidden)


def test_function_executes_with_host_supplied_call(no_implicit_state_dir):
    """An @Agent runs off a host-provided call fn, no store, no paths."""
    from openprogram import Runtime
    from openprogram import Agent

    runtime = Runtime(call=echo_call, model="test")

    class SummarizeAgent(Agent):
        method_options = {
            "summarize": {"name": "summarize", "tool": True},
        }

        def summarize(self, text, runtime=None):
            """Echo the text back."""
            return runtime.exec(text)

    summarize = SummarizeAgent().summarize

    assert "hello embedded" in summarize("hello embedded", runtime=runtime)


def test_execution_persists_to_explicit_directory(tmp_path, no_implicit_state_dir):
    """Session state lands in the caller's directory and reads back as a DAG."""
    from openprogram import Runtime
    from openprogram import Agent
    from openprogram.state.store import SessionNodeWriter, SessionStore, session_scope

    store = SessionStore(tmp_path / "host_sessions")
    store.create_session("embedded", agent_id="main")
    shim = SessionNodeWriter(store, "embedded")

    runtime = Runtime(call=echo_call, model="test")

    class SummarizeAgent(Agent):
        method_options = {
            "summarize": {"name": "summarize", "tool": True},
        }

        def summarize(self, text, runtime=None):
            """Echo the text back."""
            return runtime.exec(text)

    summarize = SummarizeAgent().summarize

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
    assert (tmp_path / "host_sessions" / "default" / "chats" / "embedded").is_dir()


def test_runs_without_any_store(no_implicit_state_dir):
    """No store installed → executes normally, persists nothing."""
    from openprogram import Runtime
    from openprogram import Agent
    from openprogram.state.store import _store

    assert _store.get() is None

    runtime = Runtime(call=echo_call, model="test")

    class PlainAgent(Agent):
        method_options = {
            "plain": {"name": "plain", "tool": True},
        }

        def plain(self, text, runtime=None):
            """Echo the text back."""
            return runtime.exec(text)

    plain = PlainAgent().plain

    assert "no persistence" in plain("no persistence", runtime=runtime)


def test_executes_with_webui_absent(no_implicit_state_dir):
    """The exec loop must not need ``openprogram.interfaces.server`` to be importable.

    webui is an optional extra (it pulls fastapi/uvicorn), so in a library
    install the module is simply not there. The core reaches cancellation and
    session routing through its own injection seams; the webui claims them on
    import, and its absence leaves the headless defaults in place.
    """
    import builtins

    from openprogram import Runtime
    from openprogram import Agent

    runtime = Runtime(call=echo_call, model="test")

    class SummarizeAgent(Agent):
        method_options = {
            "summarize": {"name": "summarize", "tool": True},
        }

        def summarize(self, text, runtime=None):
            """Echo the text back."""
            return runtime.exec(text)

    summarize = SummarizeAgent().summarize

    real_import = builtins.__import__
    blocked = ("openprogram.interfaces.server", "fastapi", "uvicorn", "textual")

    def without_webui(name, *args, **kwargs):
        if name in blocked or name.startswith("openprogram.interfaces.server."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    cached = {
        m: sys.modules.pop(m)
        for m in list(sys.modules)
        if m == "openprogram.interfaces.server" or m.startswith("openprogram.interfaces.server.")
    }
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

    from openprogram import Runtime
    from openprogram import Agent

    runtime = Runtime(call=echo_call, model="test")

    class SummarizeAgent(Agent):
        method_options = {
            "summarize": {"name": "summarize", "tool": True},
        }

        def summarize(self, text, runtime=None):
            """Echo the text back."""
            return runtime.exec(text)

    summarize = SummarizeAgent().summarize

    real_import = builtins.__import__

    def without_agent(name, *args, **kwargs):
        if name == BLOCKED or name.startswith(BLOCKED + "."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    cached = {
        m: sys.modules.pop(m)
        for m in list(sys.modules)
        if m == BLOCKED or m.startswith(BLOCKED + ".")
    }
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
    from openprogram import Agent
    from openprogram.runtime.agent.tool_format import to_openai_tool

    class LookupAgent(Agent):
        method_options = {
            "lookup": {"name": "lookup", "tool": True},
        }

        def lookup(self, city: str, runtime=None):
            """Look up the weather for a city."""
            return runtime.exec(city)

    lookup = LookupAgent().lookup

    tool = to_openai_tool(lookup)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "lookup"
    assert tool["function"]["description"]
    params = tool["function"]["parameters"]
    assert params["type"] == "object"
    assert "city" in params["properties"]
    # Runtime-injected params are not LLM-controllable and must not leak.
    assert "runtime" not in params["properties"]


def _request_seen_by(runtime=None):
    """Run one Agent method and return the turn request its body ran under."""
    from openprogram import Agent
    from openprogram.runtime.agent.turn_request import current_turn_request

    seen = []

    class WhoAgent(Agent):
        method_options = {"who": {"name": "who", "tool": True}}

        def who(self, text, runtime=None):
            """Record who the run belongs to."""
            seen.append(current_turn_request.get())
            return text

    WhoAgent().who("x", **({} if runtime is None else {"runtime": runtime}))
    return seen[0]


def test_embedded_run_is_owned_by_the_process(no_implicit_state_dir):
    """A host-supplied ``call=`` runs as an owner no profile knows about."""
    from openprogram import Runtime
    from openprogram.runtime.permissions.authority import process_owner_principal_id
    from openprogram.state.authority import has_capability, normalize_authority

    first = _request_seen_by(Runtime(call=echo_call, model="test"))
    second = _request_seen_by(Runtime(call=echo_call, model="test"))

    authority = normalize_authority(first)
    assert authority["authority_tier"] == "owner" and authority["speaker_kind"] == "owner"
    assert has_capability(first, "process.exec")
    assert first.principal_id == second.principal_id == process_owner_principal_id()


def test_runtime_passed_by_position_is_the_hosts(no_implicit_state_dir):
    """The host's runtime counts however it is handed to the method."""
    from openprogram import Agent, Runtime
    from openprogram.runtime.agent.turn_request import current_turn_request
    from openprogram.runtime.permissions.authority import process_owner_principal_id

    seen = []

    class WhoAgent(Agent):
        method_options = {"who": {"name": "who", "tool": True},
                          "later": {"name": "later", "tool": True}}

        def who(self, text, runtime=None):
            """Record who the run belongs to."""
            seen.append((current_turn_request.get().principal_id, runtime))
            return runtime.exec(text)

        async def later(self, text, runtime=None):
            """The same, awaited."""
            seen.append((current_turn_request.get().principal_id, runtime))
            return text

    import asyncio

    runtime = Runtime(call=echo_call, model="test")
    assert "by position" in WhoAgent().who("by position", runtime)
    asyncio.run(WhoAgent().later("x", runtime))

    assert seen == [(process_owner_principal_id(), runtime)] * 2


def test_runtime_created_inside_the_method_is_the_hosts(no_implicit_state_dir):
    """A host that builds its client inside the method opens no profile either."""
    from openprogram import Agent, Runtime
    from openprogram.runtime.agent.turn_request import current_turn_request
    from openprogram.runtime.permissions.authority import process_owner_principal_id

    seen = []

    class InsideAgent(Agent):
        method_options = {"bare": {"name": "bare", "tool": True},
                          "declared": {"name": "declared", "tool": True}}

        def bare(self, text):
            """No runtime parameter at all."""
            reply = Runtime(call=echo_call, model="test").exec(text)
            seen.append(current_turn_request.get().principal_id)
            return reply

        def declared(self, text, runtime=None):
            """A runtime parameter the host leaves alone."""
            reply = Runtime(call=echo_call, model="test").exec(text)
            seen.append(current_turn_request.get().principal_id)
            return reply

    assert "inside" in InsideAgent().bare("inside")
    assert "inside" in InsideAgent().declared("inside")
    assert seen == [process_owner_principal_id()] * 2


def _profile_runtime():
    """A runtime on a provider model, as a profile builds one, with no network."""
    from openprogram import Runtime
    from openprogram.models.providers.types import Model

    runtime = Runtime(model="default")
    runtime.api_model = Model(id="fake", name="fake", api="completion",
                              provider="callable", base_url="")
    return runtime


async def _profile_stream(model, context, options=None):
    import time

    from openprogram.models.providers.types import (
        AssistantMessage, EventDone, EventStart, EventTextEnd, EventTextStart, TextContent,
    )

    def message(text):
        return AssistantMessage(content=[TextContent(text=text)], api="completion",
                                provider="callable", model="fake", stop_reason="stop",
                                timestamp=int(time.time() * 1000))

    yield EventStart(partial=message(""))
    yield EventTextStart(content_index=0, partial=message(""))
    yield EventTextEnd(content_index=0, content="reply", partial=message("reply"))
    yield EventDone(reason="stop", message=message("reply"))


def test_run_on_the_profiles_model_is_the_profile_owners(tmp_path, monkeypatch):
    """The profile's owner is taken up when the run uses the profile's model."""
    from openprogram import Agent
    from openprogram.runtime.agent.turn_request import current_turn_request
    from openprogram.runtime.permissions.authority import (
        owner_principal_id, process_owner_principal_id,
    )

    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr("openprogram.runtime.agent.runtime.factory.create_runtime",
                        lambda *args, **kwargs: _profile_runtime())
    seen = []

    class ProfileAgent(Agent):
        method_options = {"ask": {"name": "ask", "tool": True}}

        def ask(self, text, runtime=None):
            """Ask the profile's default model."""
            request = current_turn_request.get()
            seen.append((request.principal_id, (state / "owner.json").exists()))
            reply = runtime.exec(text, stream_fn=_profile_stream)
            seen.append((request.principal_id, (state / "owner.json").exists()))
            return reply

    assert ProfileAgent().ask("hello") == "reply"

    # Nothing of the profile is opened before the run asks for its model.
    assert seen[0] == (process_owner_principal_id(), False)
    assert seen[1] == (owner_principal_id(), True)
    assert owner_principal_id() != process_owner_principal_id()
    # The embedded identity never becomes the profile's.
    embedded = _request_seen_by(_host_runtime())
    assert embedded.principal_id == process_owner_principal_id()


def test_profile_runtime_handed_to_the_run_is_the_profile_owners(tmp_path, monkeypatch):
    """A caller that passes a profile runtime runs as the profile owner at once."""
    from openprogram.runtime.permissions.authority import owner_principal_id

    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)

    request = _request_seen_by(_profile_runtime())

    assert request.principal_id == owner_principal_id()
    assert request.authority_tier == "owner"


def _host_runtime():
    from openprogram import Runtime

    return Runtime(call=echo_call, model="test")
