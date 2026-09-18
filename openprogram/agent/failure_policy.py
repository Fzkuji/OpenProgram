"""Bounded diagnostic state for repeated tool failures, not permission policy."""
from __future__ import annotations

import json
from typing import Any

MAX_AGENT_REPEAT_FAILURES = 16


def repeat_key(name: str, args: Any) -> str:
    # Only this built-in contract declares description to be presentation-only.
    if name == "bash" and isinstance(args, dict):
        args = {key: value for key, value in args.items() if key != "description"}
    try:
        blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        blob = repr(args)
    return f"{name}\0{blob}"


def record_failure(failures: dict[str, int], key: str, count: int) -> None:
    """Keep checkpoint-compatible state; eviction never authorizes an action."""
    failures.pop(key, None)
    failures[key] = count
    while len(failures) > MAX_AGENT_REPEAT_FAILURES:
        del failures[next(iter(failures))]
