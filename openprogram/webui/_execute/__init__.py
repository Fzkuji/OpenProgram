"""execute_in_context — chat/run dispatch with shared setup + error handling.

Originally lived as `_execute_in_context` in openprogram/webui/server.py.
Split into:
  - this module: common setup, branch dispatch, unified try/except/finally
  - chat.py: action="query" body (run_query)
  - run.py:  action="run"  body (run_function)

Two newer actions handled inline here (small enough not to warrant
their own modules):
  - spawn  : ``/spawn label: prompt`` — user-initiated peer-session
             attach. Runs ``run_sub_agent_turn`` synchronously and
             broadcasts a result envelope.
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
    """User-initiated ``/spawn`` — runs a peer sub-agent against the
    given prompt and attaches the result into this session's DAG.

    ``msg_id`` is the user message that typed the ``/spawn`` command;
    the attach pointer node hangs off it (parent_assistant_id=msg_id),
    so the spawn looks like a direct child of the user's instruction.
    """
    from openprogram.webui import server as _s
    prompt = (kwargs.get("prompt") or "").strip()
    label = (kwargs.get("label") or "").strip() or None

    if not prompt:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": "/spawn requires a prompt — usage: /spawn label: prompt text",
            "display": "chat",
        })
        return

    try:
        from openprogram.agent.sub_agent_run import run_sub_agent_turn
        result = run_sub_agent_turn(
            parent_session_id=session_id,
            parent_assistant_id=msg_id,
            prompt=prompt,
            agent_id=agent_id,
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
        or "(sub-agent returned no text)"
    )
    payload = f"{body}\n\n[sub-agent session={result.sub_session_id}"
    if result.sub_commit_id:
        payload += f" commit={result.sub_commit_id}"
    payload += "]"

    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": payload,
        "function": "spawn",
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
    for token in raw_tokens:
        s = str(token).strip()
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

    try:
        from openprogram.agent._merge import process_merge_turn
        result = process_merge_turn(
            target_session_id=session_id,
            peers=peers,
            message=message,
            agent_id=agent_id,
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
                    agent_id=_agent_id,
                    attachments=attachments,
                )
            elif action == "run":
                from . import run as _run
                _run.run_function(
                    session_id=session_id,
                    msg_id=msg_id,
                    func_name=func_name,
                    kwargs=kwargs,
                    conv=conv,
                    runtime=runtime,
                    exec_thinking_effort=exec_thinking_effort,
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
