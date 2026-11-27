"""The Program host, as the runtime reaches it.

Programs sit above the runtime: they import it, and it never imports them.
What the runtime still needs from the Program host — the tool registry and
its policy chain, the wrappers a tool call runs through, the named services
— it reaches through ``host()``, which ``openprogram.programs`` registers
when it loads (``programs._host.lower_layers``). ``INTERFACE`` is the whole
contract: registration fails when a name is missing, and runtime code uses
no other name.

What a single Program does at a moment of the runtime (a Goal settling when
a turn ends, the todo list joining the context, a tool guide attached to a
result) does not belong here: that Program registers a hook with the
runtime part that owns the moment. Neither does the tool protocol the agent
loop runs (``ToolReturn``, the result cap, the running tool call's ids):
that is the runtime's own (``agent.tool_execution``, ``agent.call_state``).

Until the Programs have loaded, the first ``host()`` loads them through
``host.runtime_facts.load_programs``, which the composition root
(``openprogram/__init__.py``) provides.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openprogram.host import runtime_facts

INTERFACE = frozenset({
    # The tool registry and the policy chain on top of it.
    "get",
    "get_agent_tool",
    "as_resident",
    "unavailable_tool",
    "agent_tools",
    "apply_tool_policy",
    "unsafe_in_for",
    "split_tools_for_dispatch",
    "loaded_tools",
    "note_request_loaded",
    "install_turn_tools",
    "deferred_catalog",
    "deferred_catalog_text",
    "catalog_note",
    "DEFAULT_TOOLS",
    "is_verifier_read_tool",
    "allowed_tool_names",
    # Registering an Agent method as a tool, and the hint a failed
    # argument check of a deferred tool adds.
    "build_and_register_tool",
    "schema_hint",
    # Checks a call's arguments before approval.
    "call_problem",
    "CallProblem",
    # Shared services, applications and Program metadata.
    "services",
    "open_application_ids",
    "load_functions_meta",
    "original_owner",
    # Extensions (MCP tools, plugins): remove tools, list them, and the
    # context a plugin registers its parts through.
    "unregister",
    "all_tools",
    "tool_owner",
    "tool_tier",
    "ProgramContext",
    # The conversation runtime: a turn installs the applications open for
    # its session, so their deferred entries are resident for its requests.
    "install_open_applications",
    "reset_open_applications",
})

_host: Any = None


def register(program_host: Any, members: Iterable[str]) -> None:
    """Install the Program host; ``members`` are the names it provides."""
    missing = INTERFACE - set(members)
    if missing:
        raise ValueError(f"the Program host lacks {sorted(missing)}")
    global _host
    _host = program_host


def host() -> Any:
    """The registered Program host, loading the Programs first if needed."""
    if _host is None:
        runtime_facts.load_programs()
    if _host is None:
        raise RuntimeError("the Programs layer is not loaded")
    return _host


def loaded() -> bool:
    """Whether the Programs have registered their host."""
    return _host is not None


__all__ = ["INTERFACE", "host", "loaded", "register"]
