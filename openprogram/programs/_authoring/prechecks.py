"""Argument checks that run before a call reaches the permission decision.

A tool registers a check here, so a call that would fail on its arguments goes
back to the model before any approval card or review: the owner never approves
a call that cannot run. The check is pure: it reads the arguments only and
returns the problem as text, or ``None`` when the call may proceed.

``call_problem`` is the one entry: it also answers for a call whose required
guide the model does not have in view. A malformed call is the model's to fix
and is not a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

Check = Callable[[dict], Optional[str]]

_registry: dict[str, Check] = {}


def register(tool: str, check: Check) -> None:
    _registry[tool] = check


def problem(tool: str, args: dict) -> Optional[str]:
    """What is wrong with ``args`` for ``tool``, or ``None``."""
    check = _registry.get(tool)
    if check is None:
        return None
    try:
        return check(args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 — a broken check must not block the call
        return None


@dataclass(frozen=True)
class CallProblem:
    code: str
    text: str


def call_problem(name: str, args: dict) -> Optional[CallProblem]:
    """Why this call cannot run as written, or ``None``."""
    from openprogram.programs._authoring import guides
    unread = guides.missing_required(name, args if isinstance(args, dict) else {})
    if unread:
        return CallProblem("GUIDE_REQUIRED", guides.refusal(name, unread))
    invalid = problem(name, args)
    if invalid:
        return CallProblem("INVALID_ARGUMENTS", f"{name}: {invalid}")
    return None


__all__ = ["CallProblem", "call_problem", "problem", "register"]
