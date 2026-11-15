"""Invoke an explicitly registered Program using its Python callable.

The callable inherits the active Runtime and Context. Listing uses the same
shared AgentTool registry as normal tool dispatch.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from openprogram.programs._authoring.helpers import read_bool_param, read_string_param
from openprogram.programs._host.runtime import function


NAME = "program"

DESCRIPTION = (
    "Run a predefined Program by name. Call with list_only=true to see "
    "registered Programs, then supply program and args. The Program shares "
    "the current Runtime and Context. This tool waits for completion."
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "program": {
                "type": "string",
                "description": "Name of the program to run. Required unless list_only=true.",
            },
            "args": {
                "type": "object",
                "description": "Keyword arguments for the program, as a JSON object. Runtime is auto-injected — do not pass it.",
            },
            "list_only": {
                "type": "boolean",
                "description": "When true, return the catalogue of registered programs instead of running one.",
            },
        },
    },
}


def _program_entries():
    from openprogram.programs._host.runtime import all_tools
    return {
        tool.name: tool for tool in all_tools()
        if getattr(tool, "_is_agent_method", False)
        and callable(getattr(tool, "_python_callable", None))
    }


def _list_registry() -> str:
    entries = _program_entries()
    if not entries:
        return "No Programs are currently registered."
    lines = [f"# Registered Programs ({len(entries)})\n"]
    for name, tool in sorted(entries.items()):
        params = tool.parameters.get("properties", {})
        required = set(tool.parameters.get("required", []))
        summary = ", ".join(f"{param}{'' if param in required else '?'}" for param in params) or "(no args)"
        description = tool.description.strip().split("\n")[0][:140]
        lines.append(f"- **{name}**({summary}) — {description}")
    return "\n".join(lines)


def _tool_check_fn() -> bool:
    return bool(_program_entries())


def execute(
    program: str | None = None,
    args: dict | None = None,
    list_only: bool = False,
    **kw: Any,
) -> str:
    list_only = read_bool_param(kw, "list_only", "listOnly", default=list_only)
    program = program or read_string_param(kw, "program", "name")

    if list_only or not program:
        # Listing is also the fallback when the model calls without a
        # program name — better than failing silently.
        return _list_registry()

    entries = _program_entries()

    if program not in entries:
        available = ", ".join(sorted(entries)) or "(none registered)"
        return (
            f"Error: program {program!r} is not registered. "
            f"Known: {available}. Call with list_only=true for details."
        )

    entry = entries[program]._python_callable
    call_args = dict(args or {})

    # Filter out any stray `runtime` the model tried to pass — the
    # method adapter injects it from the active ContextVar, and a user-
    # supplied value would be ignored anyway.
    call_args.pop("runtime", None)
    call_args.pop("exec_runtime", None)
    call_args.pop("review_runtime", None)

    try:
        result = entry(**call_args)
    except TypeError as e:
        return f"Error: bad arguments to {program}: {e}"
    except Exception as e:
        return f"Error: {program} raised {type(e).__name__}: {e}"

    if inspect.isawaitable(result):
        from openprogram.runtime.agent.runtime.shared import _run_async
        try:
            result = _run_async(result)
        except Exception as exc:
            return f"Error: {program} raised {type(exc).__name__}: {exc}"

    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except Exception:
        return str(result)



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    check_fn=_tool_check_fn,
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
