"""The ``/spawn`` and ``/merge`` slash actions of a conversation turn.

``run_spawn`` runs another agent in the same session and lands its reply as
a branch (or a new root); ``run_merge`` merges peer branches into this
session. Both report to the clients through
:mod:`openprogram.runtime.interface_link`.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def run_spawn(*, session_id: str, msg_id: str, kwargs: dict, agent_id: str) -> bool:
    """User-initiated ``/spawn`` — runs another agent in the same
    session and lands the reply as a branch (or a new root) in the
    same DAG.

    ``msg_id`` is the user message that typed the ``/spawn`` command.
    In ``inherit`` mode the spawn forks off that message; in
    ``clean`` mode it starts a new root inside the same session
    (no predecessor).

    With ``wait=True`` (default) the call blocks until the spawn
    finishes and writes a fully-populated ``attach`` pointer row.
    With ``wait=False`` (``/task --async ...``) we write a
    ``status=running`` placeholder attach pointer, submit the job
    to ``JobRunner``, and return immediately — the runner finishes
    the attach card when the job hits a terminal state.

    The attach pointer's ``session_id`` is the SAME session — opening
    it just checks out that branch in this same chat view; there is
    no separate sub_xxx session to navigate to.
    """
    import json
    import time
    import uuid

    from openprogram.runtime import interface_link
    prompt = (kwargs.get("prompt") or "").strip()
    label = (kwargs.get("label") or "").strip() or None
    # Accept ``context`` (new) and ``mode`` (legacy). "detached"/"clean"
    # both mean new-root; "inline"/"inherit" both mean fork-from-here.
    # Default is "clean": sub-agent starts blank, caller packs whatever
    # context it needs into prompt. Inherit must be opted into.
    raw = (kwargs.get("context") or kwargs.get("mode")
           or "clean").strip().lower()
    if raw in ("inline", "inherit"):
        context = "inherit"
    else:
        context = "clean"
    # wait flag: True = synchronous (existing behavior), False = submit
    # to JobRunner and return immediately. Default True for backward
    # compat — /task --async opts into the new path.
    wait = kwargs.get("wait")
    if wait is None:
        wait = True
    wait = bool(wait)
    chosen_agent = (kwargs.get("agent_id") or "").strip() or agent_id

    if not prompt:
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": "/spawn requires a prompt — usage: /spawn label: prompt text",
            "display": "chat",
        })
        return False

    if not wait:
        # Async path: write a placeholder attach card with
        # status=running, submit to the runner, return immediately.
        run_spawn_async(
            session_id=session_id,
            msg_id=msg_id,
            prompt=prompt,
            label=label,
            context=context,
            chosen_agent=chosen_agent,
        )
        return True

    # The card hangs off the /task line itself, so it draws right under what
    # the user typed, the same as the background path.
    attach_node_id = uuid.uuid4().hex[:12]

    def _on_accepted(job) -> None:
        from openprogram.runtime.conversation.sub_agent_run import write_attach_placeholder_for_spawn
        # While it runs the row stays out of the spawned turn's own context;
        # the end write removes this delivery so the finished report is read.
        write_attach_placeholder_for_spawn(
            session_id=session_id,
            caller_msg_id=msg_id,
            label=label,
            prompt=prompt,
            chosen_agent=chosen_agent,
            node_id=attach_node_id,
            job_id=job.id,
            delivery="tool_result",
        )

    try:
        from openprogram.runtime.conversation.sub_agent_run import run_agent_turn
        result = run_agent_turn(
            session_id=session_id,
            prompt=prompt,
            agent_id=chosen_agent,
            branch_from=msg_id if context == "inherit" else None,
            label=label,
            # The spawned branch stays a branch, so the window stays on the
            # conversation the user typed /task in.
            advance_head=False,
            on_accepted=_on_accepted,
            attach_pointer_id=attach_node_id,
        )
    except Exception as e:  # noqa: BLE001
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"spawn failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return False

    body = (
        result.final_text
        or result.error
        or "(spawned agent returned no text)"
    )
    tail = f"branch={session_id}:{result.head_id or '?'}"
    payload = f"{body}\n\n[spawned agent {tail}]"

    # Settle the attach row so the chat view records that this turn spawned
    # another agent. ``session_id`` deliberately matches the CURRENT session:
    # the spawn lives as a branch here, not as a separate sub_xxx session.
    # The transcript publisher sends the row to every open view.
    from openprogram.runtime.conversation.sub_agent_run import write_attach_pointer_for_spawn
    write_attach_pointer_for_spawn(
        session_id=session_id,
        caller_msg_id=msg_id,
        result=result,
        label=label,
        prompt=prompt,
        chosen_agent=chosen_agent,
        node_id=attach_node_id,
        delivery=None,
    )

    interface_link.broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": payload,
        "function": "agent",
        "display": "runtime",
    })
    return not bool(result.failed or result.error)


def run_spawn_async(
    *,
    session_id: str,
    msg_id: str,
    prompt: str,
    label: str | None,
    context: str,
    chosen_agent: str,
) -> None:
    """``/task --async`` path — write a placeholder attach card with
    ``status=running``, submit the job, return.

    The JobRunner takes over from here: on terminal it updates the
    attach card via :meth:`JobRunner._update_attach_card` (fills
    head_id, source_commit_id, status). Open transcripts get each change
    of the card from the transcript publisher.
    """
    import json
    import time
    import uuid

    from openprogram.runtime import interface_link

    def _fail_staged_attach(detail: str) -> None:
        """Close a placeholder if async job admission/submission fails."""
        try:
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store import SessionNodeWriter

            db = default_db()
            pair = db._open(session_id)  # noqa: SLF001
            if pair is None:
                return
            node = pair[1].nodes_by_id.get(attach_node_id)
            if node is None:
                return
            metadata = dict(node.metadata or {})
            attach = dict(metadata.get("attach") or {})
            attach["status"] = "failed"
            metadata["attach"] = attach
            metadata["status"] = "failed"
            metadata["reason_code"] = "agent_runner_error"
            SessionNodeWriter(db, session_id).update(
                attach_node_id,
                output=detail,
                metadata=metadata,
            )
            db.commit_turn(session_id, "spawn agent async failed")
        except Exception:
            # The placeholder card stays 'running' for a job that never started.
            _log.warning("spawn placeholder %s not failed in session %s", attach_node_id, session_id, exc_info=True)

    # 锚点 = 发起调用的节点本身（与同步路径 write_attach_pointer_for_spawn
    # 一致：attach 的 predecessor 就是触发它的那轮）。这里以前抄的是同步
    # 路径的旧版"上溯到调用者父节点"，同步侧早改掉了，这份拷贝漂移了——
    # 结果异步卡片显示在调用点上面一层。卡片在哪被调用就锚在哪。
    attach_node_id = uuid.uuid4().hex[:12]
    try:
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
        fork_anchor = msg_id

        # Pre-mint the job id so the placeholder attach card carries
        # job_id from the start (UI can correlate). We can't go
        # through runner.spawn_job first because the placeholder
        # needs to exist *before* the runner's attach card update
        # path fires.
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": "(running)",
            "predecessor": fork_anchor,
            "timestamp": time.time(),
            "is_error": False,
            "agent_id": chosen_agent,
            "extra": json.dumps({
                "attach": {
                    "session_id": session_id,
                    "head_id": None,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": None,
                    "status": "running",
                    # job_id stitched in below once runner mints one
                },
            }, default=str),
        }
        store.append_message(session_id, attach_msg, advance_head=False)
        store.commit_turn(session_id, f"spawn agent async: {label or chosen_agent}")
    except Exception as e:  # noqa: BLE001
        _fail_staged_attach(f"async spawn failed to stage: {type(e).__name__}: {e}")
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"async spawn failed to stage: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    # Submit to runner. The runner takes over: status transitions and
    # attach card finalization.
    try:
        from openprogram.runtime.conversation.sub_agent_run import run_agent_turn_async
        job_id = run_agent_turn_async(
            session_id=session_id,
            prompt=prompt,
            agent_id=chosen_agent,
            branch_from=msg_id if context == "inherit" else None,
            label=label,
            subject=label or prompt[:60],
            description=prompt,
            context_mode=context,
            attach_pointer_id=attach_node_id,
            caller_msg_id=msg_id,
        )
    except Exception as e:  # noqa: BLE001
        _fail_staged_attach(f"async spawn submit failed: {type(e).__name__}: {e}")
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"async spawn submit failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    # Stitch job_id into the attach card. The runner settles the same card
    # when the job ends, which can come first: merge into the card as it is
    # at the write, so a finished card keeps its outcome.
    try:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter
        db = default_db()

        def stitch(node) -> dict:
            md = node.metadata or {}
            # The top-level metadata.attach is the card's record (the
            # _msg_adapter promotes the placeholder's extra.attach to it);
            # extra.attach is the older place.
            if isinstance(md.get("attach"), dict):
                attach = dict(md["attach"])
            else:
                extra_raw = md.get("extra")
                try:
                    extra_json = (
                        json.loads(extra_raw)
                        if isinstance(extra_raw, str)
                        else (extra_raw or {})
                    )
                except Exception:
                    extra_json = {}
                attach = dict(extra_json.get("attach") or {}) if isinstance(extra_json, dict) else {}
            attach["job_id"] = job_id
            # The frontend reads the top-level attach first; extra stays in
            # step for any consumer that reads that path. Without job_id the
            # card cannot show its Cancel button while the job runs.
            fields: dict = {"metadata": {"extra": json.dumps({"attach": attach}, default=str),
                                         "attach": attach}}
            if attach.get("status") in (None, "running"):
                fields["output"] = "(running)"
            return fields

        if SessionNodeWriter(db, session_id).merge(attach_node_id, stitch):
            db.commit_turn(session_id, f"job: stitch {job_id}")
    except Exception:
        # The job runs; the placeholder card just lacks its job id (no Cancel).
        _log.warning("job id not stitched onto placeholder in session %s", session_id, exc_info=True)

    interface_link.broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": (
            f"[job spawned async] job_id={job_id} label={label or '(none)'}\n"
            f"The job is running in the background. The Branches panel will "
            f"animate while it runs; the attach card will update when it finishes."
        ),
        "function": "agent",
        "display": "runtime",
    })


def run_merge(*, session_id: str, msg_id: str, kwargs: dict, agent_id: str,
              branch_id: str | None = None) -> bool:
    """User-initiated ``/merge`` — runs ``process_merge_turn`` and
    broadcasts the result text into this (target) session.

    Each token in the slash command may be ``sid`` (HEAD implied) or
    ``sid:head_id`` (specific branch tip). The parser passes strings
    through unmodified; we normalize here so same-session and
    cross-session merges share one entry point.

    ``branch_id`` is the branch the ``/merge`` was typed on; the merge
    reply continues it even if the person selects another meanwhile.
    """
    from openprogram.runtime import interface_link
    raw_tokens = list(kwargs.get("sub_sessions") or [])
    message = (kwargs.get("message") or "").strip()

    if not raw_tokens:
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": (
                "/merge requires at least one peer — usage: "
                "/merge sid_a sid_b:head_b: message text"
            ),
            "display": "chat",
        })
        return False

    peers: list[dict] = []
    base_peer: int | None = None
    for token in raw_tokens:
        s = str(token).strip()
        if not s:
            continue
        # ``*`` prefix marks this peer as the merge base (attach-style).
        is_base = False
        if s.startswith("*"):
            is_base = True
            s = s[1:].strip()
            if not s:
                continue
        if ":" in s:
            sid, head_id = s.split(":", 1)
            peer = {"session_id": sid.strip(), "head_id": head_id.strip() or None}
        else:
            peer = {"session_id": s, "head_id": None}
        if is_base and base_peer is None:
            base_peer = len(peers)
        peers.append(peer)

    try:
        from openprogram.runtime.conversation.merge import process_merge_turn
        result = process_merge_turn(
            target_session_id=session_id,
            peers=peers,
            message=message,
            agent_id=agent_id,
            base_peer=base_peer,
            target_branch_id=branch_id,
        )
    except Exception as e:  # noqa: BLE001
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"merge failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return False

    if result.failed:
        interface_link.broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": result.error or "merge failed (no error message)",
            "display": "chat",
        })
        return False

    # process_merge_turn advanced the store HEAD to the merge reply, but
    # the webui mirror (conv["head_id"] / conv["messages"]) is still on
    # the pre-merge head — and the turn's caller calls _save_session
    # right after this returns, which flushes the mirror back into
    # SessionDB and would silently orphan the merge reply. Route the
    # move through _set_active_head, the single head-write primitive
    # that refreshes DB + mirror + message cache together.
    from openprogram.runtime.conversation.merge import still_selected
    if result.target_assistant_id and still_selected(session_id, result.branch_id):
        interface_link.set_active_head(session_id, result.target_assistant_id)

    extra_lines = []
    if result.commit_id:
        extra_lines.append(f"[merge commit={result.commit_id}]")
    if result.commit_parents:
        extra_lines.append(f"[parents={', '.join(result.commit_parents)}]")
    suffix = ("\n\n" + "\n".join(extra_lines)) if extra_lines else ""
    interface_link.broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": (result.final_text or "(merge produced no text)") + suffix,
        "function": "merge",
        "display": "runtime",
    })
    return True
