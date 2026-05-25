"""Render a command body with user-supplied arguments and env.

Substitutions supported (subset of the design doc §3 — enough for
Phase 1/2, the rest land later):

  $ARGUMENTS                  full args string (verbatim)
  $0 $1 ... $9                positional args (shell-quoted parse)
  {{name}}                    named arg, declared via ``arguments:``
  ${OPENPROGRAM_COMMAND_DIR}  absolute dir containing the source file
  ${OPENPROGRAM_SESSION_ID}   current session id
  ${OPENPROGRAM_CWD}          current working directory

Shell `!` blocks and `@` file refs are deferred to a later phase; they
need a security review before being turned on.
"""
from __future__ import annotations

import re
import shlex
from typing import Any


_NAMED_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")
_POS_RE = re.compile(r"\$(\d)")
_ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def parse_args(raw: str) -> list[str]:
    """Split a raw argument string into positional tokens.

    Tries shell-style first (quotes / escapes); falls back to
    whitespace split when ``shlex`` raises (e.g. unclosed quote).
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=True)
    except ValueError:
        return raw.split()


def render(
    body: str,
    *,
    raw_args: str = "",
    declared_args: list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Render ``body`` with the substitutions defined above.

    Named args are taken from ``raw_args``: shlex-split, then mapped
    positionally onto ``declared_args``. Anything not declared by name
    is still reachable via ``$ARGUMENTS`` / ``$N``.
    """
    if not body:
        return ""
    declared_args = declared_args or []
    env = env or {}

    positional = parse_args(raw_args)

    # Map positional → named by declaration order.
    named: dict[str, str] = {}
    for i, spec in enumerate(declared_args):
        name = spec.get("name") if isinstance(spec, dict) else None
        if not name:
            continue
        if i < len(positional):
            named[name] = positional[i]
        else:
            named[name] = ""

    text = body.replace("$ARGUMENTS", raw_args.strip())

    def _pos(m: re.Match) -> str:
        idx = int(m.group(1))
        return positional[idx] if idx < len(positional) else ""

    text = _POS_RE.sub(_pos, text)
    text = _NAMED_RE.sub(lambda m: named.get(m.group(1), ""), text)
    text = _ENV_RE.sub(lambda m: env.get(m.group(1), ""), text)
    return text
