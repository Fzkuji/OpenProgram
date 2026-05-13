"""parse_args — turn an LLM reply into ``(chosen, kwargs)`` ready to dispatch.

Single-call API that does everything the old ``parse_action`` + ``prepare_args``
pipeline did, plus type / enum validation, plus an LLM-driven retry when
the reply fails to parse or validate.

Usage:

    fns = [analyze_sentiment, fallback_reply,
           ("done", "Use when the conversation is over.")]
    menu = render_options(fns)
    reply = runtime.exec(content=[{"type":"text","text": prompt + menu}])
    chosen, args = parse_args(reply, fns, runtime=runtime)

    if isinstance(chosen, str):
        route_to(chosen, args)
    else:
        result = chosen(**args)

The first element of the returned tuple is either a callable (when the
chosen option was a function) or a string (when the chosen option was a
text-only option). The second element is the kwargs dict — for callables
it already has ``runtime`` etc. auto-injected and is safe to splat.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Callable

from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import (
    _functions_to_registry,
    _iter_json_objects,
)


_AUTO_PARAMS = {"runtime", "exec_runtime", "review_runtime"}
_CALL_KEY_ALIASES = ("call", "action", "function", "tool")
# Types we will isinstance-check. Anything outside this set
# (Optional[X], Union, custom classes, etc.) is skipped — the
# validator is best-effort, not exhaustive.
_VALIDATABLE_TYPES = {str, int, float, bool, list, dict}


# ---------------------------------------------------------------------------
# Internal exception used for the retry loop. Converted to a public
# ValueError when retries are exhausted.
# ---------------------------------------------------------------------------


class _ParseError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Action extraction (was render_options / parse_action)
# ---------------------------------------------------------------------------


def _has_call_key(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in _CALL_KEY_ALIASES:
        if k in obj:
            return k
    return None


def _normalize_action(obj: dict, matched_key: str) -> dict:
    if matched_key == "call":
        return obj
    out = {"call": obj[matched_key]}
    for k, v in obj.items():
        if k == matched_key:
            continue
        out[k] = v
    return out


def _extract_action(text: str) -> dict | None:
    """Extract ``{"call": "...", "args": {...}}`` from LLM text, or None."""
    if not text:
        return None

    for fence in re.findall(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL):
        try:
            obj = json.loads(fence)
        except json.JSONDecodeError:
            for obj in _iter_json_objects(fence):
                key = _has_call_key(obj)
                if key:
                    return _normalize_action(obj, key)
            continue
        key = _has_call_key(obj)
        if key:
            return _normalize_action(obj, key)

    for obj in _iter_json_objects(text):
        key = _has_call_key(obj)
        if key:
            return _normalize_action(obj, key)

    return None


# ---------------------------------------------------------------------------
# Field-level validation
# ---------------------------------------------------------------------------


def _validate_field(name: str, value, meta: dict) -> None:
    """Type and enum check for a single field. Raises _ParseError on mismatch."""
    expected = meta.get("type")
    if expected in _VALIDATABLE_TYPES:
        # bool is a subclass of int in Python — exclude that confusion:
        # if we expect int/float, a bool value is NOT acceptable.
        if expected in (int, float) and isinstance(value, bool):
            raise _ParseError(
                "type_mismatch",
                f"Field {name!r} must be {expected.__name__}, got bool",
            )
        # float accepts int too (widely accepted convention)
        if expected is float and isinstance(value, int):
            pass
        elif not isinstance(value, expected):
            raise _ParseError(
                "type_mismatch",
                f"Field {name!r} must be {expected.__name__}, "
                f"got {type(value).__name__} ({value!r})",
            )
    enum = meta.get("options")
    if enum and value not in enum:
        raise _ParseError(
            "enum_violation",
            f"Field {name!r} must be one of {list(enum)}, got {value!r}",
        )


# ---------------------------------------------------------------------------
# Core parse step — pure Python, no LLM call
# ---------------------------------------------------------------------------


def _try_parse(reply: str, registry: dict, runtime: Runtime, context: dict | None):
    action = _extract_action(reply)
    if action is None:
        raise _ParseError(
            "no_action",
            "Reply contained no parseable JSON with a 'call' field.",
        )

    call = action.get("call")
    if call not in registry:
        raise _ParseError(
            "unknown_call",
            f"Picked {call!r}, which is not an available option. "
            f"Valid options: {list(registry)}",
        )

    spec = registry[call]
    is_text = spec.get("_is_text", False)
    input_spec = spec.get("input", {})

    args = dict(action.get("args") or {})

    if is_text:
        # All declared schema fields are required.
        missing = [n for n in input_spec if n not in args]
        if missing:
            raise _ParseError(
                "missing_required",
                f"Option {call!r} is missing required fields: {missing}",
            )
        for fname, meta in input_spec.items():
            _validate_field(fname, args[fname], meta)
        # Drop any extra fields the LLM hallucinated.
        args = {k: v for k, v in args.items() if k in input_spec}
        return call, args

    # Callable option.
    target = spec["function"]
    raw = getattr(target, "_fn", target)
    sig = inspect.signature(raw)

    # Fill source="context" params from caller-supplied context dict.
    if context:
        for pname, pmeta in input_spec.items():
            if pmeta.get("source") == "context" and pname not in args:
                if pname in context:
                    args[pname] = context[pname]

    # Auto-inject runtime-style params.
    for p in _AUTO_PARAMS:
        if p in sig.parameters:
            args[p] = runtime

    # Drop args the function doesn't accept.
    valid = set(sig.parameters.keys())
    args = {k: v for k, v in args.items() if k in valid}

    # Check missing required (signature defaults).
    missing = [
        n for n, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty and n not in args
    ]
    if missing:
        raise _ParseError(
            "missing_required",
            f"Call {call!r} is missing required arguments: {missing}",
        )

    # Validate LLM-supplied fields against declared input_meta types/enums.
    for fname, meta in input_spec.items():
        if meta.get("source") != "llm" or fname not in args:
            continue
        _validate_field(fname, args[fname], meta)

    return target, args


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_args(
    reply: str,
    options,
    runtime: Runtime,
    *,
    context: dict | None = None,
    max_retries: int = 1,
) -> tuple[Callable | str, dict]:
    """Parse an LLM reply, locate the chosen option, bind its args.

    Args:
        reply:    Raw text from ``runtime.exec(...)`` — the LLM's response
                  to the prompt that included a ``render_options`` menu.
        options:  Either a list of options (recommended) or a legacy
                  registry dict. List items can be: a callable, a tuple
                  ``(callable, "description")``, a string ``"name"``, or
                  a tuple ``("name", "description", schema)``.
        runtime:  Runtime instance for auto-inject params and for the
                  retry LLM call.
        context:  Optional dict for filling ``hidden`` callable params
                  by name (e.g. ``{"session_id": "abc"}``).
        max_retries: How many times to ask the LLM to re-pick when the
                  current reply fails to parse or validate. Default 1.
                  Set to 0 to disable retries.

    Returns:
        ``(chosen, kwargs)``:
          - For a callable option: ``chosen`` is the original function
            (the ``@agentic_function`` wrapper if decorated) and
            ``kwargs`` is the fully-injected kwargs ready for
            ``chosen(**kwargs)``.
          - For a text option: ``chosen`` is the option's name string and
            ``kwargs`` is the LLM-supplied args dict (already validated
            against the schema).

    Raises:
        ValueError: when ``max_retries`` is exhausted; the message
                    contains the last error and the last reply head.
    """
    if runtime is None:
        raise ValueError("runtime is required for parse_args()")

    if isinstance(options, (list, tuple)):
        registry = _functions_to_registry(options)
    else:
        registry = options

    if not registry:
        raise ValueError("options list is empty")

    last_reply = reply
    last_error: _ParseError | None = None

    for attempt in range(max_retries + 1):
        try:
            return _try_parse(last_reply, registry, runtime, context)
        except _ParseError as e:
            last_error = e
            if attempt >= max_retries:
                break
            # Defer import to avoid circular dependency at module load.
            from openprogram.programs.functions.buildin._retry_choice import (
                _retry_choice,
            )
            from openprogram.programs.functions.buildin.render_options import (
                render_options,
            )
            last_reply = str(_retry_choice(
                prev_reply=last_reply,
                error_msg=e.message,
                menu=render_options(registry),
                runtime=runtime,
            ))

    raise ValueError(
        f"parse_args failed after {max_retries + 1} attempt(s). "
        f"Last error ({last_error.kind}): {last_error.message}. "
        f"Last reply head: {last_reply[:200]!r}"
    )
