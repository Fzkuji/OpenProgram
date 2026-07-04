"""Coverage for the @tool decorator + runtime layer.

Verifies the parts that govern how every future tool will behave:
schema generation, sync/async wrap, error wrap, char cap + persist,
approval gate evaluation, cache, cancel/on_update injection, and
registry filtering.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import pytest

from openprogram.functions import _runtime as R
from openprogram.functions._runtime import (
    DEFAULT_HEAD_RATIO,
    DEFAULT_MAX_RESULT_CHARS,
    MIN_KEEP_CHARS,
    ToolReturn,
    _build_parameters_schema,
    _cap_result_text,
    _evaluate_approval,
    _parse_docstring,
    all_tools,
    filter_for,
    get,
    register,
    reset_registry,
    restore_registry,
    snapshot_registry,
    function,
    tool_requires_approval,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a fresh registry so @tool registrations don't leak.

    Snapshot the real (fully-populated) registry first, wipe it for the
    test's own registrations, then RESTORE it afterwards. A bare
    reset_registry() in teardown would leave the shared process-wide
    registry empty — and because the real tool modules are already imported
    (cached in sys.modules) their @function decorators won't re-fire — so
    every later-running test (e.g. test_session_config_tools_intent) would
    see an empty registry. Collection order is filesystem-dependent, so that
    only bit on Linux CI, not macOS. Restore keeps the isolation local."""
    saved = snapshot_registry()
    R._cache.clear()
    reset_registry()
    yield
    restore_registry(saved)
    R._cache.clear()


def _run(coro):
    """Run a coroutine to completion in a fresh event loop.

    Each test gets an isolated loop — using ``asyncio.get_event_loop``
    is deprecated in 3.10+ when no loop is running, and hits
    "Event loop is closed" once a previous test's loop got cleaned up.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Docstring parsing
# ---------------------------------------------------------------------------

def test_parse_docstring_description_and_args() -> None:
    doc = """Run a shell command.

    Returns combined stdout/stderr/exit_code.

    Args:
        command: Shell command to execute.
        timeout: Max seconds before kill.
    """
    desc, args = _parse_docstring(doc)
    assert desc == "Run a shell command."
    assert args["command"] == "Shell command to execute."
    assert args["timeout"] == "Max seconds before kill."


def test_parse_docstring_no_args_section() -> None:
    desc, args = _parse_docstring("Just a sentence.")
    assert desc == "Just a sentence."
    assert args == {}


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

def test_schema_basic_types() -> None:
    def fn(name: str, count: int = 5, enabled: bool = True) -> str:
        """Demo.

        Args:
            name: The name.
            count: How many.
            enabled: Whether on.
        """
        return ""
    schema = _build_parameters_schema(fn)
    assert schema["type"] == "object"
    assert schema["properties"]["name"] == {"type": "string", "description": "The name."}
    assert schema["properties"]["count"] == {"type": "integer", "description": "How many."}
    assert schema["properties"]["enabled"] == {"type": "boolean", "description": "Whether on."}
    assert schema["required"] == ["name"]


def test_schema_optional_strips_none() -> None:
    def fn(x: Optional[int] = None) -> str:
        return ""
    schema = _build_parameters_schema(fn)
    assert schema["properties"]["x"] == {"type": "integer"}
    assert "required" not in schema or "x" not in schema["required"]


def test_schema_skips_framework_kwargs() -> None:
    def fn(query: str, *, on_update=None, cancel=None) -> str:
        return ""
    schema = _build_parameters_schema(fn)
    assert set(schema["properties"].keys()) == {"query"}


# ---------------------------------------------------------------------------
# Result truncation
# ---------------------------------------------------------------------------

def test_cap_short_text_unchanged() -> None:
    assert _cap_result_text("hi", 100) == "hi"


def test_cap_long_text_head_tail() -> None:
    text = "A" * 500 + "B" * 500
    capped = _cap_result_text(text, max_chars=200, head_ratio=0.5)
    # MIN_KEEP_CHARS = 2000 enforces a floor; we asked for 200 but get 2000+
    assert len(capped) >= 2000
    assert "elided" in capped


def test_cap_respects_min_floor() -> None:
    text = "X" * 10_000
    capped = _cap_result_text(text, max_chars=100)
    assert len(capped) >= MIN_KEEP_CHARS
    assert "elided" in capped
    assert capped.startswith("X")
    assert capped.endswith("X")


# ---------------------------------------------------------------------------
# Decorator: schema + name + description
# ---------------------------------------------------------------------------

def test_decorator_extracts_name_description_schema() -> None:
    @function
    def echo(message: str, repeat: int = 1) -> str:
        """Repeat `message` `repeat` times.

        Args:
            message: The text to echo.
            repeat: Number of repetitions.
        """
        return message * repeat

    assert echo.name == "echo"
    assert "Repeat" in echo.description
    assert echo.parameters["properties"]["message"]["description"] == "The text to echo."
    assert "message" in echo.parameters["required"]
    assert get("echo") is echo


def test_decorator_with_overrides() -> None:
    @function(name="custom", description="overridden", toolset=["core"])
    def fn(x: int) -> str:
        return str(x)
    assert fn.name == "custom"
    assert fn.description == "overridden"
    # Toolset filter sees it
    assert fn in filter_for(toolset="core")


# ---------------------------------------------------------------------------
# Sync vs async + error wrap
# ---------------------------------------------------------------------------

def test_sync_function_invoked_correctly() -> None:
    @function
    def add(a: int, b: int) -> str:
        """Add two ints."""
        return str(a + b)

    result = _run(add.execute("call_1", {"a": 2, "b": 3}, None, None))
    assert result.content[0].text == "5"


def test_async_function_invoked_correctly() -> None:
    @function
    async def slow_add(a: int, b: int) -> str:
        """Add two ints, async."""
        await asyncio.sleep(0)
        return str(a + b)

    result = _run(slow_add.execute("call_1", {"a": 7, "b": 8}, None, None))
    assert result.content[0].text == "15"


def test_exception_caught_and_wrapped() -> None:
    @function
    def bad(x: int) -> str:
        """Fails."""
        raise RuntimeError("boom")

    result = _run(bad.execute("call_1", {"x": 1}, None, None))
    assert result.details and result.details.get("is_error")
    assert "boom" in result.content[0].text


# ---------------------------------------------------------------------------
# Char cap + persist-to-disk
# ---------------------------------------------------------------------------

def test_long_result_truncates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(R, "_tool_results_dir", lambda: tmp_path)

    @function(max_result_chars=200, persist_full=False)
    def big() -> str:
        """Huge."""
        return "Z" * 50_000

    result = _run(big.execute("c1", {}, None, None))
    text = result.content[0].text
    assert len(text) >= MIN_KEEP_CHARS
    assert "elided" in text


def test_persist_full_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(R, "_tool_results_dir", lambda: tmp_path)

    @function(max_result_chars=100, persist_full=True)
    def big() -> str:
        """Persist everything."""
        return "Q" * 50_000

    result = _run(big.execute("c123", {}, None, None))
    text = result.content[0].text
    assert "saved at" in text
    persisted = tmp_path / "c123.txt"
    assert persisted.exists()
    assert persisted.read_text() == "Q" * 50_000


# ---------------------------------------------------------------------------
# ToolReturn structured output
# ---------------------------------------------------------------------------

def test_tool_return_struct() -> None:
    @function
    def info() -> ToolReturn:
        """Returns mixed content."""
        return ToolReturn(text="hello", json_data={"x": 1})

    result = _run(info.execute("c1", {}, None, None))
    assert result.content[0].text == "hello"
    assert result.details["json"] == {"x": 1}


def test_tool_return_error_flag() -> None:
    @function
    def failing() -> ToolReturn:
        """Marks itself as error without raising."""
        return ToolReturn(text="oops", is_error=True)

    result = _run(failing.execute("c1", {}, None, None))
    assert result.details["is_error"] is True


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------

def test_approval_static_true() -> None:
    @function(requires_approval=True)
    def dangerous() -> str:
        """Always asks."""
        return "ok"

    needs, reason = tool_requires_approval(dangerous, {})
    assert needs is True
    assert reason is None


def test_approval_callable_returns_string_reason() -> None:
    def gate(command: str) -> Optional[str]:
        if "rm" in command:
            return f"Destructive: {command}"
        return None

    @function(requires_approval=gate)
    def shell(command: str) -> str:
        """Run cmd."""
        return ""

    needs, reason = tool_requires_approval(shell, {"command": "ls"})
    assert needs is False
    needs, reason = tool_requires_approval(shell, {"command": "rm -rf /"})
    assert needs is True
    assert "Destructive" in reason


def test_approval_callable_exception_defaults_to_require() -> None:
    def angry_gate(**_):
        raise ValueError("oops")

    @function(requires_approval=angry_gate)
    def stuff() -> str:
        return ""

    needs, reason = tool_requires_approval(stuff, {})
    assert needs is True


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_hits() -> None:
    counter = {"n": 0}

    @function(cache=True, cache_ttl=60)
    def expensive(x: int) -> str:
        """Counts calls."""
        counter["n"] += 1
        return str(x * 2)

    _run(expensive.execute("c1", {"x": 5}, None, None))
    _run(expensive.execute("c2", {"x": 5}, None, None))
    _run(expensive.execute("c3", {"x": 6}, None, None))
    # 5 hit, 5 hit again (cache), 6 fresh = 2 actual invocations
    assert counter["n"] == 2


def test_cache_skips_errors() -> None:
    counter = {"n": 0}

    @function(cache=True, cache_ttl=60)
    def maybe_fails(x: int) -> str:
        """Fails on x=1."""
        counter["n"] += 1
        if x == 1:
            raise RuntimeError("nope")
        return "ok"

    _run(maybe_fails.execute("c1", {"x": 1}, None, None))
    _run(maybe_fails.execute("c2", {"x": 1}, None, None))
    # Both calls invoke fn (errors not cached)
    assert counter["n"] == 2


# ---------------------------------------------------------------------------
# Cancel + on_update injection
# ---------------------------------------------------------------------------

def test_on_update_callback_received() -> None:
    seen = []

    @function
    def chatty(msg: str, *, on_update=None) -> str:
        """Emits progress."""
        on_update(f"working on {msg}")
        return "done"

    _run(chatty.execute(
        "c1", {"msg": "hi"}, None, lambda t: seen.append(t)))
    assert seen == ["working on hi"]


def test_cancel_event_threaded_in() -> None:
    @function
    def watcher(*, cancel=None) -> str:
        """Reads cancel flag."""
        return f"set={cancel.is_set()}" if cancel else "no_cancel"

    ev = asyncio.Event()
    ev.set()
    result = _run(watcher.execute("c1", {}, ev, None))
    assert result.content[0].text == "set=True"


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_timeout_kills_long_tool() -> None:
    @function(timeout=0.05)
    async def slow() -> str:
        """Hangs."""
        await asyncio.sleep(5)
        return "never"

    result = _run(slow.execute("c1", {}, None, None))
    assert result.details and result.details.get("timeout")


# ---------------------------------------------------------------------------
# Registry + toolset + unsafe_in
# ---------------------------------------------------------------------------

def test_registry_filter_by_toolset_and_source() -> None:
    @function(toolset=["core"])
    def safe() -> str:
        """OK in any channel."""
        return ""

    @function(toolset=["core"], unsafe_in=["wechat"])
    def bash_like() -> str:
        """Hidden in wechat."""
        return ""

    core = filter_for(toolset="core")
    assert {t.name for t in core} == {"safe", "bash_like"}

    in_wechat = filter_for(toolset="core", source="wechat")
    assert {t.name for t in in_wechat} == {"safe"}


def test_registry_filter_by_explicit_names() -> None:
    @function
    def a() -> str:
        return ""

    @function
    def b() -> str:
        return ""

    @function
    def c() -> str:
        return ""

    picked = filter_for(names=["a", "c", "missing"])
    assert {t.name for t in picked} == {"a", "c"}




# ---------------------------------------------------------------------------
# Layer 1 — available_if (Claude Code "conditional import" equivalent)
# ---------------------------------------------------------------------------

def test_available_if_false_skips_registration() -> None:
    """A function decorated with ``available_if=lambda: False`` is
    never registered. ``get(name)`` returns None for the rest of the
    process, mirroring Claude Code's ``feature(...) ? require(...) : []``
    pattern."""
    @function(name="ant_only", available_if=lambda: False)
    def ant_only() -> str:
        return "x"
    assert get("ant_only") is None


def test_available_if_true_registers_normally() -> None:
    @function(name="generally_available", available_if=lambda: True)
    def fn() -> str:
        """Always on."""
        return "x"
    assert get("generally_available") is not None


def test_available_if_exception_treats_as_false() -> None:
    """If the predicate raises, we fail closed — skip registration so
    a misconfigured feature doesn't expose its tool by accident."""
    @function(name="broken_gate", available_if=lambda: 1 / 0)
    def fn() -> str:
        return "x"
    assert get("broken_gate") is None


# ---------------------------------------------------------------------------
# Layer 6 — defer + tool_search (Claude Code "shouldDefer" equivalent)
# ---------------------------------------------------------------------------

def test_defer_sidecar_set() -> None:
    @function(name="rare", defer=True)
    def rare() -> str:
        """A deferred tool."""
        return "x"
    t = get("rare")
    assert t is not None
    assert getattr(t, "_defer") is True


def test_split_partitions_provider_vs_catalog() -> None:
    from openprogram.functions._runtime import (
        split_tools_for_dispatch, install_loaded_deferred,
    )

    @function(name="common")
    def common() -> str:
        """Always shipped with full schema."""
        return "x"

    @function(name="rare2", defer=True)
    def rare() -> str:
        """Only loaded on demand."""
        return "x"

    install_loaded_deferred()
    provider, catalog = split_tools_for_dispatch([get("common"), get("rare2")])
    assert [t.name for t in provider] == ["common"]
    assert catalog == [("rare2", "Only loaded on demand.")]


def test_tool_search_promotes_deferred_into_provider_list() -> None:
    from openprogram.functions._runtime import (
        split_tools_for_dispatch, install_loaded_deferred,
        tool_search,
    )

    @function(name="lazy_tool", defer=True)
    def lazy() -> str:
        """Loaded only when asked."""
        return "x"

    install_loaded_deferred()
    result = _run(tool_search.execute(
        "c1", {"select": "select:lazy_tool"}, None, None
    ))
    assert "Loaded 1 deferred tool" in result.content[0].text

    provider, catalog = split_tools_for_dispatch([get("lazy_tool")])
    assert [t.name for t in provider] == ["lazy_tool"]
    assert catalog == []


def test_tool_search_handles_unknown_names() -> None:
    from openprogram.functions._runtime import (
        install_loaded_deferred, tool_search,
    )
    install_loaded_deferred()
    result = _run(tool_search.execute(
        "c1", {"select": "select:no_such_tool"}, None, None
    ))
    text = result.content[0].text
    assert "Loaded 0" in text
    assert "no_such_tool" in text


def test_deferred_catalog_text_format() -> None:
    """The catalog text must match the format the LLM has seen in
    training (Claude Code's wording) so it recognises the pattern."""
    from openprogram.functions._runtime import deferred_catalog_text
    block = deferred_catalog_text([("CronCreate", "Create a cron job"),
                                    ("WebFetch",   "Fetch a URL")])
    assert "deferred tools" in block
    assert "ToolSearch" in block
    assert "select:" in block
    assert "CronCreate" in block
    assert "WebFetch" in block

    # Empty input → empty string (so callers can unconditionally concat).
    assert deferred_catalog_text([]) == ""


# ---------------------------------------------------------------------------
# @agentic_function bridge — shared registry
# ---------------------------------------------------------------------------

def test_agentic_function_registers_into_shared_registry() -> None:
    """An @agentic_function should produce an AgentTool entry in
    ``openprogram.functions._runtime._registry`` so the dispatcher
    treats it identically to @function-decorated tools (toolset
    membership, 6 gating layers, deferred loading)."""
    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(
        as_tool=True,
        toolset=["core"],
        description="Test agentic function registered as a tool.",
    )
    def my_agentic_tool(question: str) -> str:
        """One-line description for the LLM."""
        return f"answered: {question}"

    t = get("my_agentic_tool")
    assert t is not None, "agentic_function should appear in _registry"
    assert t.description.startswith("Test agentic"), \
        "description override should win over docstring"
    # Sidecar attrs forwarded from agentic kwargs
    assert getattr(t, "_check_fn", None) is None
    assert getattr(t, "_defer", False) is False
    # Toolset membership picked up by Hermes-style filter
    assert t in filter_for(toolset="core")


def test_agentic_function_as_tool_false_skips_registration() -> None:
    """``as_tool=False`` keeps the agentic semantics (DAG, inner agent
    loop) but does NOT expose the function to the LLM."""
    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(as_tool=False, name="private_helper")
    def private_helper(x: str) -> str:
        """Should NOT appear in tool registry."""
        return x

    assert get("private_helper") is None


def test_agentic_function_register_globally_false() -> None:
    """``register_globally=False`` skips the shared AgentTool registry
    but still attaches the wrapper to the instance (so Python-direct
    invoke still works). Mirror of @function's ``register_globally`` kwarg."""
    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(register_globally=False, name="off_grid")
    def off_grid(x: str) -> str:
        """Should not appear in shared _registry."""
        return f"local-{x}"

    # Not in the shared registry — dispatcher can't find it
    assert get("off_grid") is None
    # …but still callable as Python and the sidecar AgentTool exists
    # (in case some local caller wants to drive it manually)
    assert off_grid._agent_tool is not None
    assert off_grid._agent_tool.name == "off_grid"


def test_agentic_function_available_if_false_returns_raw_fn() -> None:
    """Layer 1 gating on the with-parens form: when ``available_if``
    returns False, the decorator returns the raw fn unchanged so
    module-level callers don't end up with a half-built agentic
    instance. Confirms ``__call__`` honors the Layer 1 early-exit."""
    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(available_if=lambda: False, name="gated_agentic")
    def gated(x: str) -> str:
        return x

    # Returned object should be the raw fn, not an agentic_function instance
    assert not hasattr(gated, "_wrapper")
    assert get("gated_agentic") is None


# ---------------------------------------------------------------------------
# Layer 2 — exposure whitelist (TOOLSETS["full"]["tools"])
# ---------------------------------------------------------------------------

def test_exposure_is_registration_driven() -> None:
    """Layer 2 is registration-driven: a normally-registered tool is
    exposed without any whitelist edit; ``expose=False`` hides it from
    every LLM-facing query (agent_tools / get_agent_tool /
    list_registered_agent_tools) while keeping it in the registry."""
    import openprogram.functions as F

    @function(name="exposed_probe")
    def p1() -> str:
        return "x"

    @function(name="hidden_probe", expose=False)
    def p2() -> str:
        return "x"

    # Exposed by registration; hidden by expose=False — no TOOLSETS edit.
    names = [t.name for t in F.agent_tools(names=["exposed_probe", "hidden_probe"])]
    assert names == ["exposed_probe"]
    assert F.get_agent_tool("exposed_probe") is not None
    assert F.get_agent_tool("hidden_probe") is None
    listed = F.list_registered_agent_tools()
    assert "exposed_probe" in listed
    assert "hidden_probe" not in listed


def test_exposure_disabled_via_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``_exposed_set`` to return ``None`` disables the exposure
    filter entirely (a test-harness escape hatch)."""
    import openprogram.functions as F

    @function(name="probe_hidden_expose_false", expose=False)
    def p() -> str:
        return "x"

    # expose=False → hidden by default.
    assert F.get_agent_tool("probe_hidden_expose_false") is None

    monkeypatch.setattr(F, "_exposed_set", lambda: None)
    # Filter disabled → even an expose=False tool resolves.
    assert F.get_agent_tool("probe_hidden_expose_false") is not None
