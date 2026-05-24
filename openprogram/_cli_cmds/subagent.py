"""``openprogram subagent`` — spawn / merge peer sessions from the shell.

These commands invoke ``run_sub_agent_turn`` / ``process_merge_turn``
directly against the in-process ``SessionStore`` singleton — no WS, no
webui. Useful for scripting batch sub-agent fan-out from CI / shell
pipelines.

Output is JSON for easy piping; ``--json=false`` prints a human-readable
summary instead.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        return
    if payload.get("error"):
        print(f"error: {payload['error']}", file=sys.stderr)
    for k, v in payload.items():
        if k == "error":
            continue
        if isinstance(v, list):
            print(f"{k}:")
            for item in v:
                print(f"  - {item}")
        else:
            print(f"{k}: {v}")


def _cmd_subagent_spawn(
    session: str,
    prompt: str,
    *,
    parent_msg: str | None = None,
    label: str | None = None,
    agent_id: str = "main",
    as_json: bool = True,
) -> int:
    """Spawn a peer sub-agent off the given parent session.

    If ``parent_msg`` is omitted we hang the attach pointer off the
    parent's current HEAD (so the spawn looks like a child of whichever
    turn was last active)."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    db = default_db()
    sess = db.get_session(session) if session else None
    if sess is None:
        _print({"error": f"unknown session: {session}"}, as_json=as_json)
        return 2

    parent_id = parent_msg or sess.get("head_id")
    if not parent_id:
        _print(
            {"error": (
                f"session {session} has no head_id and no --parent-msg "
                "was given; nothing to attach to"
            )},
            as_json=as_json,
        )
        return 2

    result = run_sub_agent_turn(
        parent_session_id=session,
        parent_assistant_id=parent_id,
        prompt=prompt,
        agent_id=agent_id,
        label=label,
    )
    out = {
        "sub_session_id": result.sub_session_id,
        "sub_head_id": result.sub_head_id,
        "sub_commit_id": result.sub_commit_id,
        "attach_node_id": result.attach_node_id,
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
    }
    _print(out, as_json=as_json)
    return 1 if result.failed else 0


def _cmd_subagent_merge(
    target: str,
    subs: list[str],
    message: str,
    *,
    agent_id: str = "main",
    as_json: bool = True,
) -> int:
    """Merge ``subs`` (peer session ids) onto ``target`` with the given
    instruction text. Writes a multi-parent ContextCommit on target."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent._merge import process_merge_turn

    db = default_db()
    if db.get_session(target) is None:
        _print({"error": f"unknown target session: {target}"}, as_json=as_json)
        return 2

    if not subs:
        _print({"error": "no peer sessions given"}, as_json=as_json)
        return 2

    result = process_merge_turn(
        target_session_id=target,
        sub_sessions=list(subs),
        message=message,
        agent_id=agent_id,
    )
    out = {
        "target_assistant_id": result.target_assistant_id,
        "commit_id": result.commit_id,
        "parent_ids": list(result.parent_ids),
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
    }
    _print(out, as_json=as_json)
    return 1 if result.failed else 0
