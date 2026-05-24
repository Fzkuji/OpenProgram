"""``openprogram agent`` — spawn / merge agent branches from the shell.

These commands invoke ``run_agent_turn`` / ``process_merge_turn``
directly against the in-process ``SessionStore`` singleton — no WS, no
webui. Useful for scripting batch agent fan-out from CI / shell
pipelines.

Same-session multi-agent model: every spawn lands as a branch (or
new root) inside the target session's git repo. The command name
``subagent`` is kept for backwards-compatibility with existing
scripts but the model is now peer-agent, not parent/sub.

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
    context: str = "inherit",
    as_json: bool = True,
) -> int:
    """Spawn an agent in ``session``.

    ``context="inherit"`` (default): the new agent forks off
    ``parent_msg`` (or the session HEAD if omitted) and inherits the
    conversation chain. Reply is a sibling branch in the same session.

    ``context="clean"``: the new agent starts at a NEW root inside
    the same session. It sees only the prompt; the reply becomes an
    independent DAG tree alongside the original conversation.
    """
    from openprogram.agent.session_db import default_db
    from openprogram.agent.sub_agent_run import run_agent_turn

    db = default_db()
    sess = db.get_session(session) if session else None
    if sess is None:
        _print({"error": f"unknown session: {session}"}, as_json=as_json)
        return 2

    raw = (context or "inherit").strip().lower() or "inherit"
    # Accept legacy mode names: inline → inherit, detached → clean.
    if raw in ("detached", "clean"):
        ctx = "clean"
    elif raw in ("inline", "inherit"):
        ctx = "inherit"
    else:
        _print({"error": f"unknown context {context!r}"}, as_json=as_json)
        return 2

    if ctx == "inherit":
        parent_id = parent_msg or sess.get("head_id")
        if not parent_id:
            _print(
                {"error": (
                    f"session {session} has no head_id and no --parent-msg "
                    "was given; nothing to spawn from in inherit mode"
                )},
                as_json=as_json,
            )
            return 2
    else:
        parent_id = None  # clean root

    result = run_agent_turn(
        session_id=session,
        prompt=prompt,
        agent_id=agent_id,
        parent_id=parent_id,
        label=label,
    )
    out = {
        "context": ctx,
        "head_id": result.head_id,
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
