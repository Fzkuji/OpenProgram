"""prepare_args — merge LLM-selected args with context/runtime defaults."""

from __future__ import annotations

import inspect


def prepare_args(action: dict, available: dict, runtime, context: dict | None = None, fix_fn=None) -> dict:
    """Build kwargs for a dispatched function call.

    Sources:
    - action["args"] from the LLM
    - context values for params declared with source="context"
    - runtime auto-injection for a `runtime` parameter
    - optional fix_fn callback when required LLM args are missing
    """
    context = context or {}
    action_args = dict((action or {}).get("args") or {})
    fn_name = (action or {}).get("call")
    spec = (available or {}).get(fn_name, {})
    fn = spec.get("function")
    input_spec = spec.get("input", {})

    kwargs = {}
    missing = []

    for param_name, meta in input_spec.items():
        source = meta.get("source") if isinstance(meta, dict) else None
        if source == "context":
            if param_name in context:
                kwargs[param_name] = context[param_name]
        elif source == "llm":
            if param_name in action_args:
                kwargs[param_name] = action_args[param_name]
            else:
                missing.append(param_name)

    if missing and fix_fn is not None:
        repaired = fix_fn(func_name=fn_name, missing=missing, runtime=runtime) or {}
        for key in missing:
            if key in repaired:
                kwargs[key] = repaired[key]

    kwargs.update({k: v for k, v in action_args.items() if k not in kwargs})

    if fn is not None:
        sig = inspect.signature(fn)
        if "runtime" in sig.parameters and "runtime" not in kwargs:
            kwargs["runtime"] = runtime
        kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

    return kwargs
