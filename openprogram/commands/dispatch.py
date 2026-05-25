"""Resolve + render a slash-command invocation into the next action
the host should take.

Pure function — does not push the rendered message into a session,
post to the agent, or spawn a subagent. Returns a structured result
describing what to do next; the calling layer (web ws_actions or CLI
handler) carries it out. Keeps this module trivially testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import registry as _reg
from . import template as _tpl


@dataclass
class InvokeResult:
    ok: bool
    kind: str = ""                       # "prompt" | "local" | "error"
    rendered: str = ""                   # populated when kind == "prompt"
    context: str = "inline"              # "inline" | "fork"
    agent: str = "general-purpose"
    model: str = "inherit"
    effort: str = "inherit"
    allowed_tools: list[str] = field(default_factory=list)
    local_handler: Any = None
    error: str = ""
    source: str = ""
    command_name: str = ""


def invoke(
    text: str, *,
    session_id: str = "",
    cwd: Optional[str] = None,
) -> InvokeResult:
    """Resolve the typed ``text`` against the registry and produce an
    :class:`InvokeResult`. ``text`` may be ``"/review src/foo.py"``
    or ``"review src/foo.py"`` (with or without leading slash) and
    may include a source prefix like ``"(user)review"``.
    """
    s = (text or "").strip()
    if not s:
        return InvokeResult(ok=False, kind="error", error="empty command")
    if s.startswith("/"):
        s = s[1:]
    # Split off args from the (possibly tagged) head.
    head, _, rest = s.partition(" ")
    spec = _reg.resolve(head)
    if spec is None:
        return InvokeResult(
            ok=False, kind="error",
            error=f"unknown command: /{head}",
            command_name=head,
        )

    base = InvokeResult(
        ok=True, command_name=spec.name, source=spec.source,
        context=(spec.raw.context if spec.raw else "inline"),
        agent=(spec.raw.agent if spec.raw else "general-purpose"),
        model=(spec.raw.model if spec.raw else "inherit"),
        effort=(spec.raw.effort if spec.raw else "inherit"),
        allowed_tools=list(spec.raw.allowed_tools if spec.raw else []),
    )

    if spec.source == "builtin" and spec.builtin_handler is not None:
        base.kind = "local"
        base.local_handler = spec.builtin_handler
        return base

    if spec.source == "mcp":
        # Body lives on the server. Return enough metadata for the
        # caller (HTTP route) to await ``client.get_prompt(...)``.
        base.kind = "mcp_prompt"
        base.rendered = ""
        base.allowed_tools = []
        extras = (spec.raw.extras if spec.raw else {}) or {}
        base.local_handler = {
            "server": extras.get("_mcp_server"),
            "prompt": extras.get("_mcp_prompt"),
            "raw_args": rest,
            "declared": list(spec.raw.arguments if spec.raw else []),
        }
        return base

    if not spec.raw or not spec.raw.body:
        return InvokeResult(
            ok=False, kind="error",
            error=f"command /{spec.name} has no body",
            command_name=spec.name, source=spec.source,
        )

    env = {
        "OPENPROGRAM_COMMAND_DIR": str(Path(spec.path).parent) if spec.path else "",
        "OPENPROGRAM_SESSION_ID": session_id or "",
        "OPENPROGRAM_CWD": str(cwd or os.getcwd()),
    }
    rendered = _tpl.render(
        spec.raw.body,
        raw_args=rest,
        declared_args=spec.raw.arguments,
        env=env,
    )
    base.kind = "prompt"
    base.rendered = rendered
    return base
