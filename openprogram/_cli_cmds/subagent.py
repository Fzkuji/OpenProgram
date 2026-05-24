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
    mode: str = "inline",
    as_json: bool = True,
) -> int:
    """Spawn a sub-agent.

    ``mode="inline"`` (default): the sub-agent inherits the parent
    session's conversation; its reply is a sibling branch in the SAME
    session. ``mode="detached"``: brand-new peer session, only the
    prompt is visible to the sub-agent.

    If ``parent_msg`` is omitted we hang the spawn off the parent
    session's current HEAD."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.sub_agent_run import (
        run_inline_agent_turn, run_sub_agent_turn,
    )

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
                "was given; nothing to spawn from"
            )},
            as_json=as_json,
        )
        return 2

    mode_clean = (mode or "inline").strip().lower() or "inline"
    if mode_clean not in ("inline", "detached"):
        _print({"error": f"unknown mode {mode!r}"}, as_json=as_json)
        return 2

    if mode_clean == "inline":
        result = run_inline_agent_turn(
            parent_session_id=session,
            parent_assistant_id=parent_id,
            prompt=prompt,
            agent_id=agent_id,
            label=label,
        )
    else:
        result = run_sub_agent_turn(
            parent_session_id=session,
            parent_assistant_id=parent_id,
            prompt=prompt,
            agent_id=agent_id,
            label=label,
        )
    out = {
        "mode": result.mode,
        "head_id": result.head_id,
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
    base_peer: int | None = None,
    as_json: bool = True,
) -> int:
    """Merge ``subs`` (peer branches) onto ``target`` with the given
    instruction. Each item in ``subs`` is ``sid`` (= that session's
    HEAD) or ``sid:head_id`` (a specific branch tip — same-session or
    cross-session). Writes a multi-parent ContextCommit on target.

    ``base_peer`` (0-based index into ``subs``) optionally marks one
    peer as the merge base — the merge agent writes its reply as a
    continuation of that branch, with the others as supplemental
    context. Equivalent to attach-style merging."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent._merge import process_merge_turn

    db = default_db()
    if db.get_session(target) is None:
        _print({"error": f"unknown target session: {target}"}, as_json=as_json)
        return 2

    if not subs:
        _print({"error": "no peer branches given"}, as_json=as_json)
        return 2

    peers: list[dict] = []
    for s in subs:
        s = (str(s) or "").strip()
        if not s:
            continue
        if ":" in s:
            sid, head_id = s.split(":", 1)
            peers.append({
                "session_id": sid.strip(),
                "head_id": head_id.strip() or None,
            })
        else:
            peers.append({"session_id": s, "head_id": None})

    result = process_merge_turn(
        target_session_id=target,
        peers=peers,
        message=message,
        agent_id=agent_id,
        base_peer=base_peer,
    )
    out = {
        "target_assistant_id": result.target_assistant_id,
        "commit_id": result.commit_id,
        "parent_ids": list(result.parent_ids),
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
        "base_peer": result.base_peer,
    }
    _print(out, as_json=as_json)
    return 1 if result.failed else 0
