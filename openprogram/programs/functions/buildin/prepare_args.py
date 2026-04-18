"""prepare_args — prepare function call arguments from LLM action + context."""

from __future__ import annotations

import inspect

from openprogram.agentic_programming.runtime import Runtime

_AUTO_PARAMS = {"runtime", "exec_runtime", "review_runtime", "callback"}


def prepare_args(action: dict, available: dict, runtime: Runtime,
                 context: dict = None, fix_fn=None) -> dict:
    """Prepare complete arguments for a function call.

    Merges three sources:
        1. LLM-provided args from action["args"]
        2. Context-filled args (e.g. text from task)
        3. Framework-injected args (runtime)

    Also validates and fixes missing required params.

    Args:
        action: Parsed LLM action, e.g. {"call": "polish_text", "args": {"style": "academic"}}.
        available: Function registry dict.
        runtime: Runtime instance to inject.
        context: Auto-fill values, e.g. {"text": task}.
        fix_fn: Optional @agentic_function to call when required params
                are missing. Signature: (func_name, missing, runtime) -> dict.

    Returns:
        Complete args dict ready for fn(**args).
    """
    func_name = action["call"]
    spec = available[func_name]
    target_func = spec["function"]
    input_spec = spec.get("input", {})

    # Start with LLM-provided args
    args = dict(action.get("args", {}))

    # Fill source="context" params from context
    if context:
        for param_name, param_info in input_spec.items():
            if param_info.get("source") == "context" and param_name not in args:
                if param_name in context:
                    args[param_name] = context[param_name]

    # Inject runtime
    unwrapped_func = getattr(target_func, '_fn', target_func)
    sig = inspect.signature(unwrapped_func)
    for p in _AUTO_PARAMS:
        if p in sig.parameters:
            args[p] = runtime

    # Filter out params the function doesn't accept
    valid_params = set(sig.parameters.keys())
    args = {k: v for k, v in args.items() if k in valid_params}

    # Check for missing required params
    missing = [
        name for name, param in sig.parameters.items()
        if param.default is inspect.Parameter.empty and name not in args
    ]
    if missing:
        if fix_fn:
            extra = fix_fn(func_name=func_name, missing=missing, runtime=runtime)
            if isinstance(extra, dict):
                args.update({k: v for k, v in extra.items() if k in valid_params})
            still_missing = [n for n in missing if n not in args]
            if still_missing:
                raise ValueError(
                    f"prepare_args: {func_name}() is still missing required "
                    f"params after fix_fn: {still_missing}"
                )
        else:
            raise ValueError(
                f"prepare_args: {func_name}() is missing required params "
                f"{missing} and no fix_fn was provided"
            )

    return args
