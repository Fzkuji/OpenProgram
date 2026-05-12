"""
dispatch — make_choice: ask the LLM to pick one option from a named
option_set, then execute it and return that option's result.

Single-shot decision. If you want repeated decisions (loop until
"done"), wrap make_choice in your own loop or use ``agent_loop``.

Pairs with the ``option_set`` and ``when`` parameters on
``@agentic_function`` (LLM-driven options) and ``register_option``
(plain Python functions). Functions tagged with the same option_set
name form the menu the LLM picks from.

Usage:

    from openprogram import agentic_function, register_option, make_choice, create_runtime

    @agentic_function(option_set="route", when="Call when the user asks a research question.")
    def research_branch(query: str, runtime=None) -> str: ...

    @register_option("route", when="Call when the user just wants a plain web search.")
    def web_search(query: str) -> list[str]: ...

    runtime = create_runtime()
    result = make_choice(
        option_set="route",
        goal="User said: 'find me three papers on knowledge editing'",
        runtime=runtime,
    )
"""

from __future__ import annotations

import inspect

from openprogram.agentic_programming.function import (
    agentic_function as _AF,
    get_option_set,
    _plain_option_meta,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json
from openprogram.programs.functions.buildin.parse_action import parse_action


def _resolve(option, extra_meta: dict | None = None) -> tuple:
    """Return (callable_to_invoke, raw_fn_for_introspection, when, input_meta).

    ``extra_meta`` is a fn→{when, input_meta} dict for locally-passed
    options that aren't in the global ``_plain_option_meta``.
    """
    if isinstance(option, _AF):
        return option, option._fn, option.when, option.input_meta
    meta = (extra_meta or {}).get(option) or _plain_option_meta.get(option, {})
    return option, option, meta.get("when"), meta.get("input_meta", {})


_PROMPT_TEMPLATE = """\
You are picking exactly one action to take.

Context / goal: {goal}

Available actions in option_set "{option_set}":
{menu}

Pick exactly one action. Reply with JSON only, no prose:

  {{"call": "<action_name>", "args": {{...}}}}
"""


def _format_menu(funcs: dict, extra_meta: dict | None = None) -> str:
    lines = []
    for name, opt in funcs.items():
        _, raw_fn, when, input_meta = _resolve(opt, extra_meta)
        sig = inspect.signature(raw_fn)
        llm_params = [
            f"{p.name}: {p.annotation.__name__ if hasattr(p.annotation, '__name__') else 'any'}"
            for p in sig.parameters.values()
            if p.name not in ("runtime",)
            and not input_meta.get(p.name, {}).get("hidden", False)
        ]
        sig_str = f"{name}({', '.join(llm_params)})"
        lines.append(f"- {sig_str}")
        if when:
            lines.append(f"    When to use: {when}")
        doc = (raw_fn.__doc__ or "").strip().split("\n", 1)[0]
        if doc:
            lines.append(f"    {doc}")
    return "\n".join(lines) if lines else "(no actions registered)"


def _build_local_options(options) -> tuple[dict, dict]:
    """Normalize a local ``options=`` list into (funcs, local_meta).

    Each item is either:
      - a callable (uses fn.__name__; when=None unless already in
        _plain_option_meta from a prior register_option)
      - (callable, when_str)
      - (callable, when_str, name_override)
    """
    funcs: dict = {}
    local_meta: dict = {}
    for item in options:
        when = None
        name = None
        if callable(item):
            fn = item
        elif isinstance(item, tuple):
            fn = item[0]
            if len(item) >= 2:
                when = item[1]
            if len(item) >= 3:
                name = item[2]
        else:
            raise TypeError(f"option must be callable or tuple, got {type(item)}")
        key = name or getattr(fn, "__name__", repr(fn))
        funcs[key] = fn
        if when is not None:
            local_meta[fn] = {"when": when, "input_meta": {}}
    return funcs, local_meta


def make_choice(
    option_set: str | None = None,
    goal: str = "",
    runtime: Runtime = None,
    *,
    options: list | None = None,
    execute: bool = True,
):
    """Have the LLM pick one option and (by default) execute it.

    Provide exactly one of:
      - ``option_set="name"`` — look up globally-registered options
        tagged with that name (via @agentic_function or register_option).
      - ``options=[...]`` — a local list of choices, used only for
        this call. Items may be ``callable``, ``(callable, when_str)``,
        or ``(callable, when_str, name_override)``. Use this when the
        decision is local to one file and you don't want to pollute
        the global option_set namespace.

    Args:
        option_set:  Globally-registered option_set name. Mutually
                     exclusive with ``options``.
        goal:        Context the LLM uses to make the choice.
        runtime:     Runtime used to call the LLM.
        options:     Local list of choices. Mutually exclusive with
                     ``option_set``.
        execute:     If True (default), call the chosen function with
                     the args the LLM provided and return its result.
                     If False, return ``{"call": ..., "args": ...}``.

    Returns:
        Result of the chosen function (or raw decision when execute=False).
    """
    if runtime is None:
        raise ValueError("runtime is required for make_choice()")
    if (option_set is None) == (options is None):
        raise ValueError("pass exactly one of option_set= or options=")

    local_meta: dict = {}
    if option_set is not None:
        funcs = get_option_set(option_set)
        menu_label = option_set
        if not funcs:
            raise ValueError(
                f"No options registered with option_set={option_set!r}. "
                "Tag functions with @agentic_function(option_set=..., when=...) "
                "or register_option(option_set, when=...)."
            )
    else:
        funcs, local_meta = _build_local_options(options)
        menu_label = "(local)"
        if not funcs:
            raise ValueError("options= list is empty")

    prompt = _PROMPT_TEMPLATE.format(
        goal=goal,
        option_set=menu_label,
        menu=_format_menu(funcs, extra_meta=local_meta),
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

    callee, raw_fn, _, _ = _resolve(funcs[call], local_meta)
    fn_params = inspect.signature(raw_fn).parameters
    if "runtime" in fn_params and "runtime" not in args:
        args = {**args, "runtime": runtime}

    return callee(**args)


__all__ = ["make_choice", "get_option_set"]
