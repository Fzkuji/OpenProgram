"""
dispatch — make_choice: ask the LLM to pick one option from an
explicit list of callables, then execute the chosen one and return
its result.

Single-shot decision, single way to declare options: pass them
inline as a list to the call. No global registry, no decorator
side-effects, no import-order surprises. If you want to share the
same list across files, just import the callables and build the
list — same as any other Python function.

Usage:

    from openprogram import make_choice, create_runtime

    def search_web(query: str) -> str: ...
    def call_api(endpoint: str) -> str: ...
    def reply_directly(text: str) -> str: ...

    runtime = create_runtime()
    result = make_choice(
        goal="用户输入: '帮我找今天的天气'",
        options=[
            (search_web,     "Use when the user wants current external info."),
            (call_api,       "Use when the user wants to hit a specific service."),
            (reply_directly, "Use when the answer is already obvious."),
        ],
        runtime=runtime,
    )

Each entry is either a bare ``callable`` or a ``(callable, when)``
tuple. ``when`` is a short note telling the LLM when this option
applies. ``@agentic_function``-wrapped callables work too — they're
invoked through their wrapper so the context tree still records them.
"""

from __future__ import annotations

import inspect

from openprogram.agentic_programming.function import agentic_function as _AF
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json
from openprogram.programs.functions.buildin.parse_action import parse_action


_PROMPT_TEMPLATE = """\
You are picking exactly one action to take.

Context / goal: {goal}

Available actions:
{menu}

Pick exactly one action. Reply with JSON only, no prose:

  {{"call": "<action_name>", "args": {{...}}}}
"""


def _normalize(options: list) -> tuple[dict, dict]:
    """Turn the options= list into (funcs_by_name, when_by_fn).

    Each item is callable, (callable, when), or (callable, when, name_override).
    """
    funcs: dict = {}
    when_by_fn: dict = {}
    for item in options:
        when = None
        name_override = None
        if callable(item):
            fn = item
        elif isinstance(item, tuple):
            fn = item[0]
            if len(item) >= 2:
                when = item[1]
            if len(item) >= 3:
                name_override = item[2]
        else:
            raise TypeError(f"option must be callable or tuple, got {type(item)!r}")
        key = name_override or getattr(fn, "__name__", repr(fn))
        funcs[key] = fn
        when_by_fn[fn] = when
    return funcs, when_by_fn


def _raw_fn(opt):
    return opt._fn if isinstance(opt, _AF) else opt


def _format_menu(funcs: dict, when_by_fn: dict) -> str:
    lines = []
    for name, opt in funcs.items():
        raw = _raw_fn(opt)
        sig = inspect.signature(raw)
        input_meta = opt.input_meta if isinstance(opt, _AF) else {}
        llm_params = [
            f"{p.name}: {p.annotation.__name__ if hasattr(p.annotation, '__name__') else 'any'}"
            for p in sig.parameters.values()
            if p.name != "runtime"
            and not input_meta.get(p.name, {}).get("hidden", False)
        ]
        lines.append(f"- {name}({', '.join(llm_params)})")
        when = when_by_fn.get(opt)
        if when:
            lines.append(f"    When to use: {when}")
        doc = (raw.__doc__ or "").strip().split("\n", 1)[0]
        if doc:
            lines.append(f"    {doc}")
    return "\n".join(lines)


def make_choice(
    goal: str,
    options: list,
    runtime: Runtime,
    *,
    execute: bool = True,
):
    """Have the LLM pick one option from ``options`` and (by default) run it.

    Args:
        goal:     Context the LLM uses to decide.
        options:  List of choices. Each item is ``callable`` or
                  ``(callable, when_str)`` or ``(callable, when_str, name_override)``.
        runtime:  Runtime used to call the LLM.
        execute:  True (default) → invoke the chosen function with
                  the args the LLM provided and return its result.
                  False → return ``{"call": ..., "args": ...}`` without
                  invoking; useful for human-in-the-loop confirmation.

    Returns:
        Whatever the chosen function returns (or the raw decision
        when ``execute=False``).

    Raises:
        ValueError: if ``options`` is empty, the LLM reply doesn't
                    parse, or the LLM names an unknown action.
    """
    if runtime is None:
        raise ValueError("runtime is required for make_choice()")
    if not options:
        raise ValueError("options= list is empty")

    funcs, when_by_fn = _normalize(options)

    prompt = _PROMPT_TEMPLATE.format(
        goal=goal,
        menu=_format_menu(funcs, when_by_fn),
    )
    reply = runtime.exec(content=[{"type": "text", "text": prompt}])

    action = parse_action(str(reply))
    if action is None:
        try:
            action = parse_json(str(reply))
        except (ValueError, TypeError):
            raise ValueError(f"LLM reply was not parseable JSON: {str(reply)[:200]}")

    call = action.get("call")
    args = action.get("args", {}) or {}

    if call not in funcs:
        raise ValueError(
            f"LLM picked unknown action {call!r}; valid: {list(funcs.keys())}"
        )

    if not execute:
        return {"call": call, "args": args}

    chosen = funcs[call]
    raw = _raw_fn(chosen)
    if "runtime" in inspect.signature(raw).parameters and "runtime" not in args:
        args = {**args, "runtime": runtime}

    return chosen(**args)


__all__ = ["make_choice"]
