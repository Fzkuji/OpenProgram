"""Run a sub-agent turn and attach the resulting session to a parent.

In this design, **all agents are peer-level sessions**. There is no
sub-agent type. ``run_sub_agent_turn`` is just two ops chained:

  1. Create an independent session (same shape as any chat session,
     same SessionStore root, its own ``~/.agentic/sessions-git/<sid>/``
     repo). Run one turn against it via the regular dispatcher.

  2. Attach: in the parent session's DAG, write a pointer node whose
     metadata carries ``attached_session_id`` + ``attached_head_id``
     + the sub-agent's final reply text. The UI uses these to
     inline-render the sub-session's DAG under the parent's node.

That's the whole model. No worktrees, no ContextVar overrides, no
branch refs — those were artifacts of an earlier design that
conflated "spawn an independent agent" with "graft its commits onto
the parent's branch".

``merge`` is the symmetric op: pick N peer sessions, write a
multi-parent commit node into a target session pointing at all of
them. See ``_merge.process_merge_turn``. Neither op gives any
session a special "parent" status — the relationship lives in the
attach / merge nodes, not in the sessions themselves.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubAgentTurnResult:
    """Outcome of one attach-style sub-agent run.

    Two shapes covered by the same dataclass — the caller can tell
    them apart by which of ``sub_session_id`` / ``head_id`` is set:

      * detached spawn → ``sub_session_id`` populated (a brand-new
        peer session). ``attach_node_id`` points at the indicator
        row written into the parent's DAG.
      * inline spawn → ``sub_session_id`` empty, ``head_id`` is the
        assistant message id of the new in-session sibling. No
        attach node — the new sibling shows up via the existing
        Branches UI / sibling navigator.
    """
    sub_session_id: str = ""
    sub_head_id: Optional[str] = None
    sub_commit_id: Optional[str] = None
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None
    attach_node_id: Optional[str] = None
    mode: str = "detached"   # "detached" | "inline"
    head_id: Optional[str] = None        # inline mode: parent's new head


def _mint_sub_session_id(parent_session_id: str, label: Optional[str]) -> str:
    """Stable-ish sub-session id derived from the parent. Hex tail keeps
    it unique across concurrent spawns; embedding a short parent prefix
    keeps the sidebar grouping readable when sessions are listed."""
    parent_short = (parent_session_id or "x").replace("-", "_")[:10]
    label_part = ""
    if label:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:16]
        if safe:
            label_part = f"_{safe}"
    suffix = uuid.uuid4().hex[:8]
    return f"sub_{parent_short}{label_part}_{suffix}"


def run_sub_agent_turn(
    parent_session_id: str,
    parent_assistant_id: str,
    prompt: str,
    agent_id: str,
    *,
    label: Optional[str] = None,
) -> SubAgentTurnResult:
    """Run one peer-level agent turn and attach it to the parent's DAG.

    Steps:

    1. Mint a fresh ``sub_session_id``; create the session in the same
       SessionStore the parent lives in.
    2. Run ``process_user_turn`` on that session with
       ``history_override=[]`` so the sub-agent starts from an empty
       conversation (the prompt is self-contained).
    3. Commit the sub-session's turn (so its history + context commit
       are on disk).
    4. In the parent's DAG, append an attach node referencing the
       sub-session. ``called_by=parent_assistant_id`` keeps the node
       off the parent's main conversation chain — it's a side child
       the UI can show as a sub-tree, not a row the LLM has to
       reconcile on its next turn.

    Returns a structured result so the WS layer / callers can surface
    the sub-session id (for "open sub-agent transcript") and the
    sub-commit id (for a later merge turn's ``parent_ids``).
    """
    from openprogram.agent.session_db import default_db

    store = default_db()
    parent_pair = store._open(parent_session_id)
    if parent_pair is None:
        return SubAgentTurnResult(
            failed=True,
            error=f"parent session {parent_session_id!r} not found",
        )

    sub_sid = _mint_sub_session_id(parent_session_id, label)

    title = (prompt or "").strip().splitlines()[0][:60] if prompt else (label or sub_sid)
    store.create_session(
        sub_sid, agent_id,
        title=title,
        source="sub_agent",
        parent_session_id=parent_session_id,
        parent_assistant_id=parent_assistant_id,
        label=label or "",
    )

    result_text = ""
    result_failed = False
    result_error: Optional[str] = None
    sub_head_id: Optional[str] = None
    sub_commit_id: Optional[str] = None

    try:
        from openprogram.agent.dispatcher import TurnRequest, process_user_turn

        req = TurnRequest(
            session_id=sub_sid,
            user_text=prompt,
            agent_id=agent_id,
            source="sub_agent",
            history_override=[],
        )
        try:
            turn = process_user_turn(req)
            result_text = turn.final_text or ""
            result_failed = bool(turn.failed)
            result_error = turn.error
            sub_head_id = turn.assistant_msg_id
        except Exception as e:  # noqa: BLE001
            result_failed = True
            result_error = f"{type(e).__name__}: {e}"

        try:
            sub_commit_id = store.commit_turn(
                sub_sid, f"sub_agent turn: {label or 'unlabeled'}",
            )
        except Exception as e:  # noqa: BLE001
            if result_error is None:
                result_error = f"sub commit failed: {type(e).__name__}: {e}"

        # Snapshot parent HEAD so the attach node doesn't advance the
        # parent's chat chain. ``_msg_to_node`` only routes ``called_by``
        # off the dict for tool rows, so an assistant-shape row would
        # otherwise bump HEAD onto a synthetic side child.
        parent_head_before = None
        try:
            sess_row = store.get_session(parent_session_id) or {}
            parent_head_before = sess_row.get("head_id")
        except Exception:
            parent_head_before = None

        attach_node_id = uuid.uuid4().hex[:12]
        try:
            preview = (result_text or result_error or "(no output)").strip()
            store.append_message(parent_session_id, {
                "id": attach_node_id,
                "role": "assistant",
                "display": "runtime",
                "function": "attach",
                "content": preview,
                "parent_id": parent_assistant_id,
                "called_by": parent_assistant_id,
                "timestamp": time.time(),
                "is_error": bool(result_failed or result_error),
                "extra": json.dumps({
                    "attach": {
                        "session_id": sub_sid,
                        "head_id": sub_head_id,
                        "commit_id": sub_commit_id,
                        "label": label or "",
                        "prompt": (prompt or "")[:500],
                    },
                }, default=str),
            })
            if parent_head_before:
                try:
                    store.set_head(parent_session_id, parent_head_before)
                except Exception:
                    pass
            store.commit_turn(
                parent_session_id,
                f"attach sub_agent: {label or sub_sid}",
            )
        except Exception as e:  # noqa: BLE001
            if result_error is None:
                result_error = f"attach write failed: {type(e).__name__}: {e}"

        return SubAgentTurnResult(
            sub_session_id=sub_sid,
            sub_head_id=sub_head_id,
            sub_commit_id=sub_commit_id,
            final_text=result_text,
            failed=result_failed,
            error=result_error,
            attach_node_id=attach_node_id,
        )

    except Exception as e:  # noqa: BLE001
        return SubAgentTurnResult(
            sub_session_id=sub_sid,
            sub_head_id=sub_head_id,
            sub_commit_id=sub_commit_id,
            final_text=result_text,
            failed=True,
            error=result_error or f"{type(e).__name__}: {e}",
        )


def run_inline_agent_turn(
    parent_session_id: str,
    parent_assistant_id: str,
    prompt: str,
    agent_id: str,
    *,
    label: Optional[str] = None,
) -> SubAgentTurnResult:
    """In-session spawn — the agent runs ON the parent session.

    The agent inherits the full parent conversation as context. Its
    reply is a NEW sibling branch off ``parent_assistant_id`` (sharing
    the parent's predecessor chain up to that point). Mechanically
    identical to a user pressing "fork from here" then sending the
    prompt — except an agent, not the user, picked the prompt.

    Distinct from ``run_sub_agent_turn`` in two ways:
      1. No new session is created — the new ``user → assistant``
         pair lands inside ``parent_session_id``.
      2. No attach indicator node is written: the new sibling already
         shows up via the existing Branches panel + ``< N/M >``
         sibling navigator, like any other fork.

    Returns ``SubAgentTurnResult`` with ``mode="inline"`` and
    ``head_id`` pointing at the new assistant message id (the parent
    session's new branch tip).
    """
    from openprogram.agent.session_db import default_db
    from openprogram.agent.dispatcher import TurnRequest, process_user_turn

    store = default_db()
    if store._open(parent_session_id) is None:
        return SubAgentTurnResult(
            mode="inline",
            failed=True,
            error=f"parent session {parent_session_id!r} not found",
        )

    try:
        req = TurnRequest(
            session_id=parent_session_id,
            user_text=prompt,
            agent_id=agent_id,
            source="sub_agent_inline",
            # parent_id pins the new user message as a sibling off
            # parent_assistant_id — the dispatcher writes it under
            # the same predecessor, forking the DAG. The fork is
            # implicit: previously-existing children of
            # parent_assistant_id stay where they are; this one
            # joins them as another branch.
            parent_id=parent_assistant_id,
        )
        turn = process_user_turn(req)
    except Exception as e:  # noqa: BLE001
        return SubAgentTurnResult(
            mode="inline",
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )

    return SubAgentTurnResult(
        mode="inline",
        head_id=turn.assistant_msg_id,
        final_text=turn.final_text or "",
        failed=bool(turn.failed),
        error=turn.error,
    )
