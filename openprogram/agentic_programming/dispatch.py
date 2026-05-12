"""
dispatch — decide_loop: let the LLM repeatedly pick one option from a
named option_set and execute it, until it picks "done".

Pairs with the ``option_set`` and ``when`` parameters on
``@agentic_function``. Functions tagged with the same ``option_set``
name form the menu of choices the LLM sees each turn.

Usage:

    from openprogram import agentic_function, decide_loop, create_runtime

    @agentic_function(option_set="paper_pipeline", when="Call when you need to fetch a PDF from arxiv.")
    def download_pdf(arxiv_id: str) -> str: ...

    @agentic_function(option_set="paper_pipeline", when="Call after PDF is downloaded, to extract figures.")
    def extract_figures(pdf_path: str, out_dir: str, runtime=None) -> list[dict]: ...

    runtime = create_runtime()
    result = decide_loop(
        option_set="paper_pipeline",
        goal="Process arxiv 2501.12345 into figures under ./out/",
        runtime=runtime,
    )

The LLM emits ``{"call": "...", "args": {...}}`` each turn and exits
with ``{"call": "done", "result": "..."}``.
"""

from __future__ import annotations

import inspect
from typing import Optional

from openprogram.agentic_programming.function import (
    agentic_function as _AF,
    get_option_set,
    _plain_option_meta,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json
from openprogram.programs.functions.buildin.parse_action import parse_action


def _resolve(option) -> tuple:
    """Return (callable_to_invoke, raw_fn_for_introspection, when, input_meta)."""
    if isinstance(option, _AF):
        return option, option._fn, option.when, option.input_meta
    meta = _plain_option_meta.get(option, {})
    return option, option, meta.get("when"), meta.get("input_meta", {})


_PROMPT_TEMPLATE = """\
You are deciding which action to take next to reach a goal.

Goal: {goal}

History of actions taken so far:
{history}

Available actions ({option_set}):
{menu}

Pick exactly one action. Reply with JSON only, no prose:

  {{"call": "<action_name>", "args": {{...}}}}

When the goal is fully achieved, reply:

  {{"call": "done", "result": "<final summary>"}}
"""


def _format_menu(funcs: dict) -> str:
    lines = []
    for name, opt in funcs.items():
        _, raw_fn, when, input_meta = _resolve(opt)
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


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no actions yet)"
    return "\n".join(
        f"  {i+1}. {h['call']}({h.get('args', {})}) → {h.get('summary', h.get('error', ''))}"
        for i, h in enumerate(history)
    )


def _summarize(result) -> str:
    s = repr(result)
    return s if len(s) <= 200 else s[:200] + "..."


def decide_loop(
    option_set: str,
    goal: str,
    runtime: Runtime,
    max_steps: int = 20,
) -> dict:
    """Let the LLM pick options from ``option_set`` until it picks done.

    Each turn:
      1. Render history + menu (all @agentic_function with this
         option_set tag) into the prompt.
      2. LLM replies with ``{"call": ..., "args": ...}``.
      3. Look up the function by name, call it (auto-injects runtime
         if the function takes a runtime parameter).
      4. Append summary to history.

    Exits when the LLM picks ``"call": "done"`` or ``max_steps`` is
    reached. Unparseable replies and unknown calls are recorded in
    history and the loop continues — the LLM sees its error next turn.

    Returns:
        ``{"done": bool, "final": ..., "history": [...], "reason": ...}``
    """
    if runtime is None:
        raise ValueError("runtime is required for decide_loop()")

    funcs = get_option_set(option_set)
    if not funcs:
        raise ValueError(
            f"No @agentic_function found with option_set={option_set!r}. "
            "Tag your functions with @agentic_function(option_set=..., when=...)."
        )

    history: list[dict] = []
    final = None
    reason = "max_steps_reached"

    for step in range(max_steps):
        prompt = _PROMPT_TEMPLATE.format(
            goal=goal,
            history=_format_history(history),
            option_set=option_set,
            menu=_format_menu(funcs),
        )
        reply = runtime.exec(content=[{"type": "text", "text": prompt}])
        action = parse_action(str(reply))

        if action is None:
            try:
                action = parse_json(str(reply))
            except (ValueError, TypeError):
                history.append({
                    "call": "(unparseable)",
                    "error": f"could not parse JSON: {str(reply)[:120]}",
                })
                continue

        call = action.get("call")
        args = action.get("args", {}) or {}

        if call == "done":
            final = action.get("result")
            reason = "done"
            break

        if call not in funcs:
            history.append({
                "call": str(call),
                "error": f"unknown action; valid: {list(funcs.keys())}",
            })
            continue

        callee, raw_fn, _, _ = _resolve(funcs[call])
        fn_params = inspect.signature(raw_fn).parameters
        if "runtime" in fn_params and "runtime" not in args:
            args = {**args, "runtime": runtime}

        try:
            result = callee(**args)
        except Exception as e:
            history.append({
                "call": call,
                "args": {k: v for k, v in args.items() if k != "runtime"},
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        history.append({
            "call": call,
            "args": {k: v for k, v in args.items() if k != "runtime"},
            "summary": _summarize(result),
        })

    return {
        "done": reason == "done",
        "final": final,
        "history": history,
        "reason": reason,
    }


__all__ = ["decide_loop", "get_option_set"]
