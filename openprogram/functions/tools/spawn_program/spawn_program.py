"""spawn_program tool — invoke any registered @agentic_function.

OpenProgram's programs (``@agentic_function``-decorated Python functions)
already know how to drive an LLM: they have their own system prompts,
their own context sculpting, and they can recursively spawn children.
Exposing the whole registry as a single tool lets an agent pick the
right sub-program by name rather than us hand-wiring each one as its
own tool.

Execution is synchronous — the tool blocks until the sub-program returns
its value, then hands the result back as a string. That's deliberate:

  * matches the usual tool-call mental model (agent asks, agent waits)
  * the runtime is auto-inherited via ``_current_runtime`` ContextVar,
    so the spawned program shares the same conv / provider / cache
  * cheaper than rolling a full async-task registry for a feature most
    sub-programs don't actually need

If a program takes 10 minutes we do block for 10 minutes. If you want
fire-and-forget, that's a future ``spawn_program_bg`` (not this tool).

Two call modes:

  1. ``list_only=True``    — return the catalogue. Useful so the agent
                             can discover what's available before calling.
  2. ``program="X"`` + args — actually run X with the given kwargs.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from ..._helpers import read_bool_param, read_string_param
from ..._runtime import function


NAME = "spawn_program"

DESCRIPTION = (
    "Invoke a registered @agentic_function sub-program by name. Call with "
    "`list_only=true` to see the catalogue; then call again with "
    "`program=<name>` and `args={...}` to run it. The sub-program shares "
    "the current runtime (same provider / session / context tree) and "
    "runs synchronously — the tool blocks until it returns."
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "program": {
                "type": "string",
                "description": "Name of the program to run (e.g. `research_agent`, `extract_pdf_figures`). Required unless list_only=true.",
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


def _list_registry() -> str:
    from openprogram.agentic_programming.function import _registry

    if not _registry:
        return "No @agentic_function programs are currently registered."

    lines = [f"# Registered programs ({len(_registry)})\n"]
    for name in sorted(_registry):
        af = _registry[name]
        try:
            spec = af.spec
            params = spec.get("parameters", {}).get("properties", {})
            required = set(spec.get("parameters", {}).get("required", []))
            param_summary = ", ".join(
                f"{p}{'' if p in required else '?'}" for p in params.keys()
            ) or "(no args)"
            desc = (spec.get("description") or "").strip().split("\n")[0][:140]
        except Exception:
            param_summary = "?"
            desc = ""
        lines.append(f"- **{name}**({param_summary}) — {desc}")
    return "\n".join(lines)


def _tool_check_fn() -> bool:
    from openprogram.agentic_programming.function import _registry

    return bool(_registry)


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

    from openprogram.agentic_programming.function import _registry

    if program not in _registry:
        # A catalogued-but-not-installed harness gets an actionable
        # message (the GUI agent is opt-in — it downloads PyTorch).
        try:
            from openprogram.functions._programs import get_program
            prog = get_program(program)
        except Exception:
            prog = None
        if prog is not None and not prog.is_installed():
            size = " (downloads PyTorch, ~300 MB; ~3 GB on CUDA)" if prog.heavy else ""
            return (
                f"Error: {prog.function} is not installed{size}. "
                f"Install it with: openprogram programs install {prog.extra} "
                f"— or via `openprogram setup` → programs. "
                f"It registers on the next launch."
            )
        available = ", ".join(sorted(_registry)) or "(none registered)"
        return (
            f"Error: program {program!r} is not registered. "
            f"Known: {available}. Call with list_only=true for details."
        )

    af = _registry[program]
    call_args = dict(args or {})

    # Filter out any stray `runtime` the model tried to pass — the
    # decorator injects it from the active ContextVar, and a user-
    # supplied value would be ignored anyway.
    call_args.pop("runtime", None)
    call_args.pop("exec_runtime", None)
    call_args.pop("review_runtime", None)

    try:
        result = af(**call_args)
    except TypeError as e:
        return f"Error: bad arguments to {program}: {e}"
    except Exception as e:
        return f"Error: {program} raised {type(e).__name__}: {e}"

    if inspect.iscoroutine(result):
        # An async @agentic_function was invoked from a sync path. We
        # shouldn't be here in practice (runtime.exec handles async
        # tools), but handle it rather than returning the coroutine
        # object as a string.
        return (
            f"Error: {program} is async; spawn_program currently only "
            "supports sync programs. File an issue if you need this."
        )

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
