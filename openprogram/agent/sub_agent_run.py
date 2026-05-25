"""Run an agent turn that can be inherited (sibling branch) or clean
(new root) — both inside the same session.

All agents are peers. There is no "sub-agent type". A turn is just
``(parent_id, prompt, agent_id)``:

  * ``parent_id = <existing_node_id>`` — the new turn forks off that
    node. The agent inherits the conversation chain that leads to
    ``parent_id`` as context. This is the normal "fork from here" /
    Claude-Code Task feel.
  * ``parent_id = None`` — the new turn starts a fresh root. The
    agent sees only the prompt; its turn series becomes an
    independent DAG tree inside the same session repo.

Either way the new turn lands in the parent session's git repo as
a branch (or a new root commit). No separate ``sub_xxx`` session id
is minted — the previous design of "detached spawn = independent
session" has been removed; multi-root DAGs in a single repo cover
the same use case without a separate ``session_id`` namespace.

Merge is the symmetric op (see ``_merge.process_merge_turn``): pick
N branch heads in one session, write a multi-parent commit node
referencing all of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentTurnResult:
    """Outcome of one agent turn (whether inherit or clean)."""

    head_id: Optional[str] = None      # new assistant message id (the branch tip)
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None


def run_agent_turn(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    parent_id: Optional[str] = None,
    label: Optional[str] = None,
) -> AgentTurnResult:
    """Run one agent turn inside ``session_id``.

    ``parent_id`` controls the context:
      * ``None`` → new root (clean start, agent sees only ``prompt``).
      * ``<node_id>`` → fork off that node (agent inherits the chain
        ending at ``node_id`` as context).

    Returns ``AgentTurnResult`` with the new branch tip's assistant
    message id and final text. Caller decides what to do with it
    (write an attach indicator, surface in chat, kick off a merge).
    """
    from openprogram.agent.session_db import default_db
    from openprogram.agent.dispatcher import TurnRequest, process_user_turn

    store = default_db()
    if store._open(session_id) is None:
        return AgentTurnResult(
            failed=True,
            error=f"session {session_id!r} not found",
        )

    # Clean start: pass ``history_override=[]`` so the dispatcher's
    # context assembly doesn't pull in any conversation history.
    # Inherit: history is whatever leads to ``parent_id``, which the
    # dispatcher already resolves from ``parent_id``.
    req = TurnRequest(
        session_id=session_id,
        user_text=prompt,
        agent_id=agent_id,
        source="agent_spawn",
        parent_id=parent_id,
        history_override=[] if parent_id is None else None,
    )
    try:
        turn = process_user_turn(req)
    except Exception as e:  # noqa: BLE001
        return AgentTurnResult(
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )

    # dispatcher already stamped ``agent_id`` on the user + assistant
    # rows via ``req.agent_id``. If a label was provided, attach it as
    # a named branch so the right-rail "Branches" panel and the DAG
    # use the human label instead of the bare commit hash.
    if turn.assistant_msg_id and label:
        try:
            store.set_branch_name(session_id, turn.assistant_msg_id, label)
        except Exception:  # noqa: BLE001
            pass

    return AgentTurnResult(
        head_id=turn.assistant_msg_id,
        final_text=turn.final_text or "",
        failed=bool(turn.failed),
        error=turn.error,
    )


def run_agent_turn_async(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    parent_id: Optional[str] = None,
    label: Optional[str] = None,
    subject: str = "",
    description: str = "",
    context_mode: str = "inherit",
    parent_task_id: Optional[str] = None,
    attach_pointer_id: Optional[str] = None,
    target_branch_head_id: Optional[str] = None,
) -> str:
    """Submit an agent turn to the task runner, return ``task_id``.

    Non-blocking counterpart of :func:`run_agent_turn`. The runner
    walks the task through the state machine on a worker thread and
    eventually invokes ``run_agent_turn`` under the hood. Callers
    that need the result block on ``runner.await_task(task_id)``;
    callers that want fire-and-forget (the ``--async`` slash flag,
    plan-mode spawns) ignore the return value.
    """
    from openprogram.agent.task import get_runner
    runner = get_runner()
    return runner.spawn_task(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        subject=subject or (description or prompt[:60]),
        description=description or prompt,
        context_mode=context_mode if parent_id is not None or context_mode == "clean" else context_mode,
        parent_msg_id=parent_id,
        parent_task_id=parent_task_id,
        label=label,
        attach_pointer_id=attach_pointer_id,
        target_branch_head_id=target_branch_head_id,
    )
