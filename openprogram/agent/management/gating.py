"""Unified extension gating — shared helpers for tools / skills / MCP.

All three extension types use the same agent-profile shape::

    agent.json
    ├─ tools:   {disabled: [...], allowed: [...]}
    ├─ skills:  {disabled: [...], allowed: [...], categories: [...]}
    └─ mcp:     {disabled: [...], allowed: [...], required: [...]}

All name lists support fnmatch wildcards (``*`` / ``?`` / ``[abc]``).
Exact names are the trivial case (``"bash"`` matches only ``bash``).
"""
from __future__ import annotations

import fnmatch
from typing import Iterable


def match_any(name: str, patterns: Iterable[str]) -> bool:
    """True iff ``name`` matches any fnmatch pattern in ``patterns``.

    Empty / falsy ``patterns`` returns False (the caller wants
    'no constraint'). Patterns are case-sensitive via fnmatchcase.
    """
    if not patterns:
        return False
    for pat in patterns:
        if not isinstance(pat, str):
            continue
        try:
            if fnmatch.fnmatchcase(name, pat):
                return True
        except Exception:
            continue
    return False


def gate(
    *,
    name: str,
    category: str = "",
    disabled: Iterable[str] = (),
    allowed: Iterable[str] = (),
    categories: Iterable[str] = (),
) -> str | None:
    """Evaluate a name (+ optional category) against the standard
    disabled / allowed / categories trio.

    Returns ``None`` if the item is allowed through, or a human-readable
    rejection reason string. Caller decides how to surface the
    rejection (chat message, log line, HTTP error, etc.).

    Resolution order (matches the per-type list semantics):

    1. ``disabled`` — explicit deny wins outright.
    2. ``allowed`` — when non-empty, name must match at least one pattern.
       Empty list = no whitelist constraint.
    3. ``categories`` — when non-empty AND the item declares a category,
       the category must match at least one pattern. Skills-only field;
       tools / MCP just leave it empty.
    """
    disabled = list(disabled or [])
    allowed = list(allowed or [])
    categories = list(categories or [])
    if match_any(name, disabled):
        return f"{name!r} is disabled for this agent."
    if allowed and not match_any(name, allowed):
        return f"{name!r} is not in this agent's allowed list."
    if categories and category and not match_any(category, categories):
        return (
            f"category {category!r} is not permitted "
            f"(allowed: {', '.join(categories)})."
        )
    return None


def check_required(installed: Iterable[str], required: Iterable[str]) -> list[str]:
    """Return the patterns from ``required`` that nothing in ``installed``
    matches. Empty result = all required deps present.

    Used by MCP / plugin-style "this agent needs server X to function"
    checks. ``required`` patterns are fnmatch'd against ``installed``
    names so ``required=["github*"]`` accepts ``github-mcp`` etc.
    """
    installed_list = list(installed or [])
    missing: list[str] = []
    for pat in required or []:
        if not isinstance(pat, str):
            continue
        if not any(fnmatch.fnmatchcase(n, pat) for n in installed_list):
            missing.append(pat)
    return missing
