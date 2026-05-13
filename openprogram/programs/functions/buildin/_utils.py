"""Shared utilities for built-in agentic functions."""

from __future__ import annotations

import json
import re


def parse_json(text: str) -> dict:
    """Extract the first JSON object from text, handling markdown fences."""
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try markdown-fenced JSON
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Find first '{' and try balanced extraction
    result = _extract_first_json_object(text)
    if result is not None:
        return result

    raise ValueError("No valid JSON found in response")


_AUTO_PARAMS = ("runtime", "exec_runtime", "review_runtime")


def _functions_to_registry(options) -> dict:
    """Build a catalog registry dict from a heterogeneous options list.

    Each item in the list can be:
      - ``callable`` — function, metadata auto-extracted from
        ``fn.__doc__`` + ``fn.input_meta``.
      - ``(callable, description_override)`` — same, with description
        overridden.
      - ``"name"`` — text-only option (no description, no args). The
        chosen option's name is returned to the caller as a string.
      - ``("name", "description")`` — text option with description.
      - ``("name", "description", schema)`` — text option with arg schema.
        ``schema`` is a dict like::

            {
                "arg_a": "what it is",  # short form, type defaults to str
                "arg_b": {
                    "type": int,
                    "description": "what it is",
                    "options": ["x", "y"],
                },
            }

    Returns a dict ``{name: {"function": ..., "description": str,
    "input": {param: {"source": ..., "type": ..., "description": ...,
    "options": [...]}}, "_is_text": bool}}``.

    Raises:
        ValueError: if two items share the same name.
        TypeError:  if an item shape is unrecognized.
    """
    registry: dict = {}
    for item in options:
        name, entry = _normalize_option(item)
        if name in registry:
            raise ValueError(
                f"Duplicate option name {name!r} in options list"
            )
        registry[name] = entry
    return registry


def _normalize_option(item) -> tuple[str, dict]:
    import inspect

    # Callable: bare function
    if callable(item) and not isinstance(item, (tuple, str)):
        return _callable_entry(item, override_desc=None)

    # Text option: bare string name
    if isinstance(item, str):
        return item, {
            "function": None,
            "description": "",
            "input": {},
            "_is_text": True,
        }

    # Tuple: dispatch by first element
    if isinstance(item, tuple):
        if len(item) == 0:
            raise TypeError("Empty tuple in options list")
        first = item[0]
        if callable(first) and not isinstance(first, str):
            override = item[1] if len(item) >= 2 else None
            return _callable_entry(first, override_desc=override)
        if isinstance(first, str):
            name = first
            desc = item[1] if len(item) >= 2 else ""
            schema = item[2] if len(item) >= 3 else {}
            return name, {
                "function": None,
                "description": desc,
                "input": _normalize_text_schema(schema),
                "_is_text": True,
            }
        raise TypeError(
            f"First element of option tuple must be callable or str, "
            f"got {type(first).__name__}"
        )

    raise TypeError(
        f"Option must be callable, str, or tuple; got {type(item).__name__}"
    )


def _callable_entry(fn, override_desc) -> tuple[str, dict]:
    import inspect
    raw = getattr(fn, "_fn", fn)
    input_meta = getattr(fn, "input_meta", {}) or {}
    doc = (raw.__doc__ or "").strip()
    desc = (
        override_desc
        if override_desc
        else (doc.split("\n\n", 1)[0].strip() if doc else "")
    )

    sig = inspect.signature(raw)
    input_spec: dict = {}
    for pname, param in sig.parameters.items():
        meta = input_meta.get(pname, {}) or {}
        is_auto = pname in _AUTO_PARAMS
        is_hidden = bool(meta.get("hidden", False))
        source = "context" if (is_auto or is_hidden) else "llm"
        entry: dict = {
            "source": source,
            "type": (
                param.annotation
                if param.annotation is not inspect.Parameter.empty
                else str
            ),
        }
        if "description" in meta:
            entry["description"] = meta["description"]
        if "options" in meta:
            entry["options"] = meta["options"]
        input_spec[pname] = entry

    return raw.__name__, {
        "function": fn,
        "description": desc,
        "input": input_spec,
        "_is_text": False,
    }


def _normalize_text_schema(schema) -> dict:
    """Turn a text option's user-supplied schema into registry input_spec form.

    Accepts:
      {arg: "description"}                — type defaults to str
      {arg: {"type": T, "description": ..., "options": [...]}}
    """
    if not isinstance(schema, dict):
        raise TypeError(
            f"Text option schema must be a dict, got {type(schema).__name__}"
        )
    out: dict = {}
    for arg_name, value in schema.items():
        if isinstance(value, str):
            out[arg_name] = {
                "source": "llm",
                "type": str,
                "description": value,
            }
        elif isinstance(value, dict):
            entry: dict = {
                "source": "llm",
                "type": value.get("type", str),
            }
            if "description" in value:
                entry["description"] = value["description"]
            if "options" in value:
                entry["options"] = value["options"]
            out[arg_name] = entry
        else:
            raise TypeError(
                f"Schema value for {arg_name!r} must be str or dict, "
                f"got {type(value).__name__}"
            )
    return out


def _iter_json_objects(text: str):
    """Yield every dict that parses as JSON at any '{' position in text.

    Order: first-found-first-yielded. Caller decides which one wins
    (e.g. pick the first whose dict has a 'call' key).
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        emitted_here = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(text[start:i + 1])
                        emitted_here = True
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)


def _extract_first_json_object(text: str) -> dict | None:
    """Find the first valid JSON object in text by bracket balancing.

    More reliable than regex — handles nested braces correctly.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break  # This { didn't work, try next one
        start = text.find("{", start + 1)
    return None
