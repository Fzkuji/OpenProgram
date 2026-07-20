"""Run an agent turn that can be inherited (sibling branch) or clean
(new root) — both inside the same session.

All agents are peers. There is no "sub-agent type". A turn is just
``(predecessor, prompt, agent_id)``:

  * ``predecessor = <existing_node_id>`` — the new turn forks off that
    node. The agent inherits the conversation chain that leads to
    ``predecessor`` as context. This is the normal "fork from here" /
    Claude-Code Task feel.
  * ``predecessor = None`` — the new turn starts a fresh root. The
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
    branch_from: Optional[str] = None,
    label: Optional[str] = None,
    spawn_caller: Optional[str] = None,
) -> AgentTurnResult:
    """Run one agent turn inside ``session_id``.

    ``predecessor`` controls the context:
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
    # Inherit: history is whatever leads to ``predecessor``, which the
    # dispatcher already resolves from ``predecessor``.
    # Spawned sub-agents run with ``permission_mode="bypass"``: there's
    # no UI subscribed to approval_request events on the spawned lane
    # (the chat view only listens to its own turn), so the default
    # ``"ask"`` would hang on every bash/list/read until the 300s
    # timeout and return ``[denied]`` for every tool call. Spawning a
    # sub-agent is itself an explicit user act, so the user has
    # already consented to tool use within that turn.
    req = TurnRequest(
        session_id=session_id,
        user_text=prompt,
        agent_id=agent_id,
        source="agent_spawn",
        branch_from=branch_from,
        history_override=[] if branch_from is None else None,
        permission_mode="bypass",
        # New-branch (branch_from=None) root points its caller at the
        # spawning node, so the branch is an explicit spawn (see
        # session-dag.md §2.3). No-op for inherit forks.
        spawn_caller=spawn_caller,
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


def emit_spawn_event(
    *,
    session_id: str,
    status: str,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    card_id: str,
    tool_call_id: Optional[str] = None,
    head_id: Optional[str] = None,
    content: str = "",
    task_id: Optional[str] = None,
) -> None:
    """Push one ``sub_agent`` stream event so the caller's live turn can
    draw the spawn card without waiting for a page reload.

    The ``attach`` payload is deliberately the same dict shape
    ``write_attach_pointer_for_spawn`` persists into ``extra.attach``,
    so the live path in ``chat-stream.ts`` and the reload path in
    ``conv-mapper.ts`` build structurally identical ``attachCards``
    entries. ``card_id`` is stable across the running → terminal pair,
    letting the client patch the card in place instead of appending a
    second one.
    """
    from openprogram.agent.event_bus import emit_ws_frame

    emit_ws_frame({
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "session_id": session_id,
            "event": {
                "type": "sub_agent",
                # Anchors the card to the execution-timeline row drawn
                # for the spawning tool call. Absent (slash-command
                # spawns) the client falls back to FIFO order, the same
                # way the reload path matches cards to spawn blocks.
                "tool_call_id": tool_call_id,
                "card_id": card_id,
                "content": content,
                "attach": {
                    "session_id": session_id,
                    "head_id": head_id,
                    "label": label or "",
                    "prompt": (prompt or "")[:500],
                    "status": status,
                    "task_id": task_id,
                },
                "agent_id": chosen_agent,
            },
        },
    })


def write_attach_pointer_for_spawn(
    *,
    session_id: str,
    caller_msg_id: str,
    result: AgentTurnResult,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    node_id: Optional[str] = None,
) -> Optional[str]:
    """Write an `attach`-function pointer node for a synchronous
    task() spawn (LLM tool call, wait=True). Mirrors the body of
    ``_run_spawn`` in webui/_execute/__init__.py — kept in sync so the
    DAG sees the same node shape whether the user typed ``/spawn`` or
    the LLM called the ``task`` tool.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    if not result or not result.head_id:
        return None
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        # Anchor the attach pointer DIRECTLY to the caller turn (the
        # LLM reply that ran the task() tool call, or the user_msg of
        # a slash-command path). This is the call-edge semantics from
        # docs/design/runtime/dag-node-model.md: attach is a function_call
        # whose ``predecessor`` is the turn that triggered it. Previously
        # this code re-anchored to the caller's parent (the spawn
        # user_msg) which made depth.py collapse attach onto the same
        # row as the LLM reply.
        fork_anchor = caller_msg_id

        source_commit_id = None
        try:
            from openprogram.context.commit.store import load_commit_for_head
            _src = load_commit_for_head(store, session_id, result.head_id)
            if _src is not None:
                source_commit_id = _src.id
        except Exception:
            pass

        # Reuse the id the live spawn event already announced, so the
        # card the client drew mid-stream and the row a reload reads
        # from the DB are the same node — otherwise a refresh would
        # show the spawn twice.
        attach_node_id = node_id or _uuid.uuid4().hex[:12]
        # Attach is a branch-referencing function_call that lives ON
        # the main sequence (per docs/design/runtime/dag-node-model.md), so it
        # must hang off the caller via ``predecessor`` (sequence edge),
        # not ``predecessor`` (which would put it on a side branch and
        # leave the caller's reply orphaned as its own tip).
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": (result.final_text or result.error or "(no output)").strip(),
            "predecessor": fork_anchor,
            "timestamp": _time.time(),
            "is_error": bool(result.failed or result.error),
            "agent_id": chosen_agent,
            "extra": _json.dumps({
                "attach": {
                    "session_id": session_id,
                    "head_id": result.head_id,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": source_commit_id,
                    "status": "completed",
                },
            }, default=str),
        }
        store.append_message(session_id, attach_msg)
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(session_id, f"task tool spawn: {label or chosen_agent}")
        # Hide the spawned sub-branch from the Branches panel — its
        # content is now reachable from main via the attach pointer.
        # Same retirement the async runner does on completion (see
        # task/runner.py::_update_attach_card).
        try:
            store.mark_merged(session_id, [result.head_id])
        except Exception:
            pass
        # Broadcast session_reload so the UI re-renders the DAG with
        # the new attach pointer + reference edge.
        # 步 4：走总线（ws.frame 事件），不再 import webui；帧内容不变。
        from openprogram.agent.event_bus import emit_ws_frame
        emit_ws_frame({
            "type": "session_reload",
            "data": {"session_id": session_id, "reason": "task_tool_spawn"},
        })
        return attach_node_id
    except Exception:
        return None


def write_attach_placeholder_for_spawn(
    *,
    session_id: str,
    caller_msg_id: str,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
) -> Optional[str]:
    """Write a ``status=running`` placeholder attach card for an async
    spawn, anchored at the CALLING node（在哪调用就锚在哪）. The runner
    patches it on terminal via ``_update_attach_card``. Without this the
    task(wait=False) path had no card at all — the result later arrived
    as a task_followup with nothing anchoring it in the transcript.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        attach_node_id = _uuid.uuid4().hex[:12]
        store.append_message(session_id, {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": "(running)",
            "predecessor": caller_msg_id,
            "timestamp": _time.time(),
            "is_error": False,
            "agent_id": chosen_agent,
            "extra": _json.dumps({
                "attach": {
                    "session_id": session_id,
                    "head_id": None,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": None,
                    "status": "running",
                },
            }, default=str),
        })
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(
            session_id, f"task tool spawn (async): {label or chosen_agent}",
        )
        return attach_node_id
    except Exception:
        return None


def run_agent_turn_async(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    branch_from: Optional[str] = None,
    label: Optional[str] = None,
    subject: str = "",
    description: str = "",
    context_mode: str = "inherit",
    parent_task_id: Optional[str] = None,
    attach_pointer_id: Optional[str] = None,
    target_branch_head_id: Optional[str] = None,
    caller_msg_id: Optional[str] = None,
    caller_session_id: Optional[str] = None,
    spawn_depth: int = 0,
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
        context_mode=context_mode if branch_from is not None or context_mode == "clean" else context_mode,
        parent_msg_id=branch_from,
        parent_task_id=parent_task_id,
        label=label,
        attach_pointer_id=attach_pointer_id,
        target_branch_head_id=target_branch_head_id,
        wait=False,
        caller_msg_id=caller_msg_id,
        caller_session_id=caller_session_id,
        spawn_depth=spawn_depth,
    )
