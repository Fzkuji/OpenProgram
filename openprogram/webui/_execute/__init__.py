"""execute_in_context — chat dispatch with shared setup + error handling.

Originally lived as `_execute_in_context` in openprogram/webui/server.py.
Split into:
  - this module: common setup, branch dispatch, unified try/except/finally
  - chat.py: action="query" body (run_query)

The former ``action="run"`` path (manual @agentic_function trigger via
``/run`` slash command or fn-form) was removed in favour of
``dispatcher.dispatch_forced_tool_call`` — UI-triggered and LLM-issued
@agentic_function calls now share one execution path.

Two other actions handled inline here (small enough not to warrant
their own modules):
  - spawn  : ``/spawn label: prompt`` — user-initiated peer agent
             spawn (same session, new branch / new root). Runs
             ``run_agent_turn`` synchronously and broadcasts a
             result envelope.
  - merge  : ``/merge sid_a sid_b: message`` — user-initiated peer
             session merge. Runs ``process_merge_turn`` synchronously.

server.py keeps a thin `_execute_in_context` shim that forwards here, so
existing callers (ws_actions/chat.py, _chat_routes.py) keep working.
"""
from __future__ import annotations

import json
import time
import traceback


def _run_spawn(*, session_id: str, msg_id: str, kwargs: dict, agent_id: str) -> None:
    """User-initiated ``/spawn`` — runs another agent in the same
    session and lands the reply as a branch (or a new root) in the
    same DAG.

    ``msg_id`` is the user message that typed the ``/spawn`` command.
    In ``inherit`` mode the spawn forks off that message; in
    ``clean`` mode it starts a new root inside the same session
    (no parent_id).

    With ``wait=True`` (default) the call blocks until the spawn
    finishes and writes a fully-populated ``attach`` pointer row.
    With ``wait=False`` (``/task --async ...``) we write a
    ``status=running`` placeholder attach pointer, submit the task
    to ``TaskRunner``, and return immediately — the runner finishes
    the attach card when the task hits a terminal state and
    broadcasts ``session_reload``.

    The attach pointer's ``session_id`` is the SAME session — opening
    it just checks out that branch in this same chat view; there is
    no separate sub_xxx session to navigate to.
    """
    import json
    import time
    import uuid

    from openprogram.webui import server as _s
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
    # to TaskRunner and return immediately. Default True for backward
    # compat — /task --async opts into the new path.
    wait = kwargs.get("wait")
    if wait is None:
        wait = True
    wait = bool(wait)
    chosen_agent = (kwargs.get("agent_id") or "").strip() or agent_id

    if not prompt:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": "/spawn requires a prompt — usage: /spawn label: prompt text",
            "display": "chat",
        })
        return

    if not wait:
        # Async path: write a placeholder attach card with
        # status=running, submit to the runner, return immediately.
        _run_spawn_async(
            session_id=session_id,
            msg_id=msg_id,
            prompt=prompt,
            label=label,
            context=context,
            chosen_agent=chosen_agent,
        )
        return

    try:
        from openprogram.agent.sub_agent_run import run_agent_turn
        result = run_agent_turn(
            session_id=session_id,
            prompt=prompt,
            agent_id=chosen_agent,
            parent_id=msg_id if context == "inherit" else None,
            label=label,
        )
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"spawn failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    body = (
        result.final_text
        or result.error
        or "(spawned agent returned no text)"
    )
    tail = f"branch={session_id}:{result.head_id or '?'}"
    payload = f"{body}\n\n[spawned agent {tail}]"

    # Persist an attach pointer so the chat view records that this turn
    # spawned another agent. ``session_id`` deliberately matches the
    # CURRENT session — the spawn lives as a branch in the same git
    # repo, not as a separate sub_xxx session. The pointer hangs off
    # ``msg_id`` via ``called_by`` so ``linear_history`` skips it and
    # the chat splices it in as a standalone AttachCard row (see
    # ``ws_actions/session.py`` chain splicing).
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        # Record parent HEAD so the attach pointer doesn't push the
        # active branch onto a synthetic side child.
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        # The attach pointer hangs off the FORK POINT (the main-branch
        # turn the spawn forked from), not the spawn user msg itself.
        # The spawn user msg lives on the new (probe / ocean / ...)
        # branch — if the attach pointer hung off it, the card would
        # only be visible from inside that new branch. By hanging it
        # off the fork point, both branches' chat views can splice
        # the card in: main's chain still contains the fork point, and
        # so does the spawned branch's chain (it descends from there).
        # Fallback to the spawn user msg if its parent can't be
        # resolved — better an attach card visible only on the new
        # branch than no card at all.
        fork_anchor = msg_id
        try:
            pair = store._open(session_id)
            if pair is not None:
                _, _idx = pair
                spawn_node = _idx.nodes_by_id.get(msg_id)
                if spawn_node:
                    parent_id = (spawn_node.metadata or {}).get("parent_id")
                    if parent_id:
                        fork_anchor = parent_id
        except Exception:
            pass
        # Pin the source branch's ContextCommit so the generator can
        # expand its items into the next turn's commit (see
        # docs/design/context/context-attach-merge.md scenario B). Absent =
        # generator falls back to the single-item legacy path.
        source_commit_id = None
        if result.head_id:
            try:
                from openprogram.context.commit.store import load_commit_for_head
                _src = load_commit_for_head(store, session_id, result.head_id)
                if _src is not None:
                    source_commit_id = _src.id
            except Exception:
                pass

        attach_node_id = uuid.uuid4().hex[:12]
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": (result.final_text or result.error or "(no output)").strip(),
            # Only ``called_by`` — NOT ``parent_id``. With both set,
            # the attach pointer would be both a conv child (picked
            # up by linear_history) AND a side-call (picked up by the
            # splicer), so the row showed up twice in the chat. With
            # called_by alone it's a pure side-call: linear_history
            # ignores it, the splicer in ws_actions/session.py grafts
            # it back in once.
            "called_by": fork_anchor,
            "timestamp": time.time(),
            "is_error": bool(result.failed or result.error),
            "agent_id": chosen_agent,
            "extra": json.dumps({
                "attach": {
                    # Same session — opening the card checks out the
                    # branch in this view, no cross-session navigation.
                    "session_id": session_id,
                    "head_id": result.head_id,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": source_commit_id,
                },
            }, default=str),
        }
        store.append_message(session_id, attach_msg)
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(session_id, f"spawn agent: {label or chosen_agent}")
        # Note: do NOT also push attach_msg into the in-memory
        # conv["messages"]. The session_reload broadcast below makes
        # the client call load_session, which pulls a fresh chain
        # from SessionDB — that round-trip is the single source of
        # truth. Appending here too produced the attach card twice.
    except Exception:  # noqa: BLE001
        pass

    # Signal clients tailing this session that the transcript changed —
    # the frontend already listens for ``session_reload`` and re-pulls
    # (``load_session``) when the active session id matches. Used for
    # the new attach pointer row to show up without a manual refresh.
    try:
        _s._broadcast(json.dumps({
            "type": "session_reload",
            "data": {"session_id": session_id, "reason": "spawn"},
        }, default=str))
    except Exception:
        pass

    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": payload,
        "function": "task",
        "display": "runtime",
    })


def _run_spawn_async(
    *,
    session_id: str,
    msg_id: str,
    prompt: str,
    label: str | None,
    context: str,
    chosen_agent: str,
) -> None:
    """``/task --async`` path — write a placeholder attach card with
    ``status=running``, submit the task, broadcast session_reload, return.

    The TaskRunner takes over from here: on terminal it updates the
    attach card via :meth:`TaskRunner._update_attach_card` (fills
    head_id, source_commit_id, status) and broadcasts another
    session_reload so the UI re-renders the card with the result.
    """
    import json
    import time
    import uuid

    from openprogram.webui import server as _s

    # Resolve fork anchor + parent head (same logic as the sync path).
    attach_node_id = uuid.uuid4().hex[:12]
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        fork_anchor = msg_id
        try:
            pair = store._open(session_id)  # noqa: SLF001
            if pair is not None:
                _, _idx = pair
                spawn_node = _idx.nodes_by_id.get(msg_id)
                if spawn_node:
                    parent_id = (spawn_node.metadata or {}).get("parent_id")
                    if parent_id:
                        fork_anchor = parent_id
        except Exception:
            pass

        # Pre-mint the task id so the placeholder attach card carries
        # task_id from the start (UI can correlate). We can't go
        # through runner.spawn_task first because the placeholder
        # needs to exist *before* the runner's attach card update
        # path fires.
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": "(running)",
            "called_by": fork_anchor,
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
                    # task_id stitched in below once runner mints one
                },
            }, default=str),
        }
        store.append_message(session_id, attach_msg)
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(session_id, f"spawn agent async: {label or chosen_agent}")
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"async spawn failed to stage: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    # Submit to runner. The runner takes over: status transitions,
    # attach card finalization, session_reload broadcasts.
    try:
        from openprogram.agent.sub_agent_run import run_agent_turn_async
        task_id = run_agent_turn_async(
            session_id=session_id,
            prompt=prompt,
            agent_id=chosen_agent,
            parent_id=msg_id if context == "inherit" else None,
            label=label,
            subject=label or prompt[:60],
            description=prompt,
            context_mode=context,
            attach_pointer_id=attach_node_id,
            caller_msg_id=msg_id,
        )
    except Exception as e:  # noqa: BLE001
        # Roll back the placeholder by stamping it errored.
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"async spawn submit failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    # Stitch task_id into the attach card's extra blob (re-write).
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.store import GraphStoreShim
        db = default_db()
        pair = db._open(session_id)  # noqa: SLF001
        if pair is not None:
            _, idx = pair
            node = idx.nodes_by_id.get(attach_node_id)
            if node:
                md = dict(node.metadata or {})
                # Prefer the top-level metadata.attach dict — the
                # _msg_adapter promotes the placeholder's extra.attach
                # blob to metadata.attach on the way in (and pops the
                # original `extra` field), so by the time we stitch
                # here the only source of truth for label / status /
                # session_id is metadata.attach. Falling back to
                # extra_json.attach was bug city: it returned {} and
                # blew away every field except task_id.
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
                    attach = dict(extra_json.get("attach") or {})
                attach["task_id"] = task_id
                # Keep extra in sync for any consumer that reads from
                # that path too (Backend round-trip drops the field,
                # but downstream callers may still inspect it).
                md["extra"] = json.dumps({"attach": attach}, default=str)
                # Mirror onto the top-level metadata.attach the same
                # way the runner does in _update_attach_card — the
                # frontend's _readAttach reads the top-level first
                # and only falls back to extra-json, so without this
                # the UI sees task_id missing on the placeholder
                # card and can't render the Cancel button while the
                # task is still running.
                md["attach"] = attach
                shim = GraphStoreShim(db, session_id)
                shim.update(attach_node_id, output="(running)", metadata=md)
                db.commit_turn(session_id, f"task: stitch {task_id}")
    except Exception:
        pass

    # Broadcast so the chat UI picks up the new placeholder card +
    # branches panel shows the upcoming branch.
    try:
        _s._broadcast(json.dumps({
            "type": "session_reload",
            "data": {"session_id": session_id, "reason": "spawn_async"},
        }, default=str))
    except Exception:
        pass

    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": (
            f"[task spawned async] task_id={task_id} label={label or '(none)'}\n"
            f"The task is running in the background. The Branches panel will "
            f"animate while it runs; the attach card will update when it finishes."
        ),
        "function": "task",
        "display": "runtime",
    })


def _run_merge(*, session_id: str, msg_id: str, kwargs: dict, agent_id: str) -> None:
    """User-initiated ``/merge`` — runs ``process_merge_turn`` and
    broadcasts the result text into this (target) session.

    Each token in the slash command may be ``sid`` (HEAD implied) or
    ``sid:head_id`` (specific branch tip). The parser passes strings
    through unmodified; we normalize here so same-session and
    cross-session merges share one entry point.
    """
    from openprogram.webui import server as _s
    raw_tokens = list(kwargs.get("sub_sessions") or [])
    message = (kwargs.get("message") or "").strip()

    if not raw_tokens:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": (
                "/merge requires at least one peer — usage: "
                "/merge sid_a sid_b:head_b: message text"
            ),
            "display": "chat",
        })
        return

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
        from openprogram.agent._merge import process_merge_turn
        result = process_merge_turn(
            target_session_id=session_id,
            peers=peers,
            message=message,
            agent_id=agent_id,
            base_peer=base_peer,
        )
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"merge failed: {type(e).__name__}: {e}",
            "display": "chat",
        })
        return

    if result.failed:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": result.error or "merge failed (no error message)",
            "display": "chat",
        })
        return

    extra_lines = []
    if result.commit_id:
        extra_lines.append(f"[merge commit={result.commit_id}]")
    if result.parent_ids:
        extra_lines.append(f"[parents={', '.join(result.parent_ids)}]")
    suffix = ("\n\n" + "\n".join(extra_lines)) if extra_lines else ""
    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": (result.final_text or "(merge produced no text)") + suffix,
        "function": "merge",
        "display": "runtime",
    })


def execute_in_context(
    session_id: str,
    msg_id: str,
    action: str,
    func_name: str = None,
    kwargs: dict = None,
    query: str = None,
    thinking_effort: str = None,
    exec_thinking_effort: str = None,
    tools_flag=None,
    permission_mode: str = None,
    service_tier: str = None,
    attachments: list = None,
) -> None:
    """Execute a chat query or function call within the conversation's DAG.

    This is the core execution engine. Everything runs under the conversation's
    root Context, so summarize() automatically provides conversation history.
    """
    from openprogram.webui import server as _s

    _conv_token = _s._set_current_session_id(session_id)
    try:
        conv = _s._get_or_create_session(session_id)
        # Resolve the owning agent once so every persist call in this
        # function uses a stable id even if the caller later rebinds
        # the conv dict.
        _agent_id = conv.get("agent_id") or _s._default_agent_id()
        runtime = _s._get_session_runtime(session_id, msg_id=msg_id)
        from openprogram.agent.session_config import (
            load_session_run_config,
            permission_from_config,
            save_session_run_config,
        )
        if tools_flag is not None or thinking_effort is not None \
                or permission_mode is not None:
            run_cfg = save_session_run_config(
                session_id,
                agent_id=_agent_id,
                tools=tools_flag,
                thinking_effort=thinking_effort,
                permission_mode=permission_mode,
            )
        else:
            run_cfg = load_session_run_config(session_id)
        effective_thinking = run_cfg.thinking_effort
        effective_permission = permission_from_config(run_cfg, default="bypass")

        # Apply thinking effort to chat runtime
        _s._apply_thinking_effort(runtime, effective_thinking)

        # Default chat-runtime cwd = the session's git workdir/. The
        # /api/run path supplies its own work_dir and overrides this via
        # run.py::run_function's set_workdir call, so the user-supplied
        # override still wins.
        try:
            from openprogram.agent._workdir import apply_default_workdir
            _applied_wd = apply_default_workdir(runtime, session_id)
            if _applied_wd is not None:
                _s._log(f"[exec] chat workdir: {_applied_wd}")
        except Exception:
            pass

        try:
            if action == "query":
                from . import chat as _chat
                _chat.run_query(
                    session_id=session_id,
                    msg_id=msg_id,
                    query=query,
                    conv=conv,
                    runtime=runtime,
                    run_cfg=run_cfg,
                    effective_thinking=effective_thinking,
                    effective_permission=effective_permission,
                    service_tier=service_tier,
                    agent_id=_agent_id,
                    attachments=attachments,
                )
            elif action == "spawn":
                _run_spawn(
                    session_id=session_id,
                    msg_id=msg_id,
                    kwargs=kwargs or {},
                    agent_id=_agent_id,
                )
            elif action == "merge":
                _run_merge(
                    session_id=session_id,
                    msg_id=msg_id,
                    kwargs=kwargs or {},
                    agent_id=_agent_id,
                )
        finally:
            pass

        # Update conversation title from first user message
        if not conv.get("_titled"):
            title = (query or func_name or "")[:50]
            if title:
                conv["title"] = title + ("..." if len(title) >= 50 else "")
                conv["_titled"] = True

        # Broadcast updated chat session info (session_id may have been set)
        chat_session_id = getattr(runtime, '_session_id', None) if runtime else None
        if chat_session_id:
            _s._broadcast(json.dumps({
                "type": "chat_session_update",
                "data": {"session_id": chat_session_id},
            }, default=str))

        # Persist sessions to disk after each execution
        _s._save_session(session_id)

    except (Exception, _s._CancelledError) as e:
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)
        _s._emit_running_task_event(session_id)
        _s._unregister_active_runtime(session_id)

        # Cancellation path — either the exception came from /api/stop killing
        # the subprocess, or a CancelledError was raised by the cancel hook
        # (e.g. loops between exec calls). Mark any still-running tree nodes
        # as cancelled and emit a "stopped" result instead of an error message.
        if _s._is_cancelled(session_id) or isinstance(e, _s._CancelledError):
            _s._clear_cancel(session_id)
            # tree Context retired — no live tree to walk / persist on
            # cancel. The DAG nodes the @agentic_function wrapper wrote
            # before cancellation are already in SessionDB.
            try:
                conv = _s._get_or_create_session(session_id)
                now = time.time()
                _s._append_msg(conv, {
                    "role": "assistant",
                    "type": "cancelled",
                    "id": msg_id + "_reply",
                    "parent_id": msg_id,
                    "content": "Execution stopped by user.",
                    "function": func_name,
                    "display": "runtime",
                    "timestamp": now,
                })
                _s._save_session(session_id)
            except Exception:
                pass
            _s._broadcast_chat_response(session_id, msg_id, {
                "type": "result",
                "content": "Execution stopped by user.",
                "function": func_name,
                "cancelled": True,
                "context_tree": None,
            })
            return

        error_content = f"Error: {e}\n\n{traceback.format_exc()}"
        # Plain chat errors (action="query", no function) should be shown as
        # chat messages with a retry button, not as runtime blocks.
        error_display = "runtime" if func_name else "chat"
        try:
            conv = _s._get_or_create_session(session_id)
            now = time.time()
            error_msg = {
                "role": "assistant",
                "type": "error",
                "id": msg_id + "_reply",
                "content": error_content,
                "function": func_name,
                "display": error_display,
                "timestamp": now,
                "attempts": [{"content": error_content, "timestamp": now}],
                "current_attempt": 0,
            }
            if not func_name:
                error_msg["retry_query"] = query
            error_msg["parent_id"] = msg_id
            _s._append_msg(conv, error_msg)
            _s._save_session(session_id)
        except Exception:
            pass
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": error_content,
            "function": func_name,
            "display": error_display,
            "retry_query": query if not func_name else None,
        })
    finally:
        _s._reset_current_session_id(_conv_token)


__all__ = ["execute_in_context"]
