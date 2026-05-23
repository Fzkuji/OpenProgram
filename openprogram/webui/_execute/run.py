"""action="run" branch of execute_in_context.

Extracted verbatim from openprogram/webui/server.py:_execute_in_context.
Behavior is unchanged. server-module globals are accessed via
`from openprogram.webui import server as _s`.

Exceptions raised here are intentionally NOT caught — the caller in
_execute/__init__.py wraps the dispatch in a unified try/except so the
chat + run paths share the same cancellation / error handling.
"""
from __future__ import annotations

import os
import time


def run_function(
    *,
    session_id: str,
    msg_id: str,
    func_name: str,
    kwargs: dict | None,
    conv: dict,
    runtime,
    exec_thinking_effort: str | None,
) -> None:
    """Run the function-execution branch. Returns None; results are broadcast via WS."""
    from openprogram.webui import server as _s
    from openprogram.webui._exec_dag import (
        build_exec_dag,
        live_progress,
    )

    # Validate create() description
    if func_name == "create" and kwargs and "description" in kwargs:
        desc = kwargs["description"].strip()
        if len(desc) < 5:
            _s._broadcast_chat_response(session_id, msg_id, {
                "type": "result",
                "content": "Description too short. What function would you like to create?",
                "function": func_name,
            })
            return
        try:
            check = runtime.exec(
                f'Is this a clear description of a Python function? '
                f'Reply ONLY "yes" or "no, <reason>".\n\nDescription: "{desc}"'
            )
            if check.strip().lower().startswith("no"):
                reason = check.strip()[2:].strip().lstrip(",:").strip() or "unclear"
                _s._broadcast_chat_response(session_id, msg_id, {
                    "type": "result",
                    "content": f"Unclear description: {reason}\n\nPlease describe what the function should **do**.",
                    "function": func_name,
                })
                return
        except Exception:
            pass

    _s._log(f"[exec] running function: {func_name}({', '.join(f'{k}=...' for k in (kwargs or {}))})")
    # Build display params string (exclude runtime/callback)
    _display_params = ", ".join(
        f"{k}={v!r}" if len(repr(v)) < 60 else f"{k}=..."
        for k, v in (kwargs or {}).items()
        if k not in ("runtime", "callback")
    )
    with _s._running_tasks_lock:
        _s._running_tasks[session_id] = {
            "msg_id": msg_id,
            "func_name": func_name,
            "started_at": time.time(),
            "display_params": _display_params,
            "loaded_func_ref": None,  # set after load
            "stream_events": [],  # buffered for refresh recovery
        }
    _s._emit_running_task_event(session_id)
    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "status",
        "content": f"Running {func_name}...",
    })

    loaded_func = _s._load_function(func_name)
    if loaded_func is None:
        _s._broadcast_chat_response(session_id, msg_id, {"type": "error", "content": f"Function '{func_name}' not found."})
        return
    with _s._running_tasks_lock:
        if session_id in _s._running_tasks:
            _s._running_tasks[session_id]["loaded_func_ref"] = loaded_func
    call_kwargs = dict(kwargs or {})
    # Resolve string function-name parameters to actual function objects
    # (e.g. edit(function="sentiment") → edit(function=<sentiment function>))
    for param_key in ("fn", "function"):
        if param_key in call_kwargs and isinstance(call_kwargs[param_key], str):
            resolved_function = _s._load_function(call_kwargs[param_key])
            if resolved_function is not None:
                call_kwargs[param_key] = resolved_function
    # Pull workdir out before it can collide with any function arg.
    # Decoupled from function signature: purely a runtime-level setting.
    # Accept both spellings — chat command parsing uses the user-
    # facing `work_dir=...`, the /api/run handler already renames
    # to `_work_dir` for clarity.
    _work_dir = call_kwargs.pop("_work_dir", None) or call_kwargs.pop("work_dir", None)

    # Use exec runtime (separate from chat runtime)
    # Check if function has no_tools flag (pure text, no shell/tools)
    _no_tools = getattr(loaded_func, 'no_tools', False)
    exec_rt = _s._get_exec_runtime(no_tools=_no_tools)
    # Exec-side reasoning effort. When the caller didn't pick
    # one, fall back to "medium" — NOT the model's catalog
    # default, which for xhigh-capable models (gpt-5.5 …) is
    # "xhigh". An autonomous @agentic_function fires many
    # exec calls in a loop; xhigh on each turns a run into a
    # multi-minute-per-step crawl. medium still reasons,
    # without that cost. An explicit choice always wins.
    _exec_effort = exec_thinking_effort or "medium"
    _s._apply_thinking_effort(exec_rt, _exec_effort)
    if _work_dir:
        _work_dir = os.path.abspath(os.path.expanduser(_work_dir))
        os.makedirs(_work_dir, exist_ok=True)
        exec_rt.set_workdir(_work_dir)
        conv.setdefault("last_workdirs", {})[func_name] = _work_dir
        _s._log(f"[exec] workdir: {_work_dir}")
    _s._log(f"[exec] new runtime: provider={type(exec_rt).__name__}, no_tools={_no_tools}, id={id(exec_rt)}, thinking={_exec_effort}")
    _s._register_active_runtime(session_id, exec_rt)
    _s._inject_runtime(loaded_func, call_kwargs, exec_rt)

    # Register streaming callback for real-time LLM output
    def _on_stream(event: dict):
        # Buffer for refresh recovery (keep last 200 events)
        with _s._running_tasks_lock:
            ti = _s._running_tasks.get(session_id)
            if ti and "stream_events" in ti:
                ti["stream_events"].append(event)
                if len(ti["stream_events"]) > 200:
                    ti["stream_events"] = ti["stream_events"][-200:]
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "stream_event",
            "event": event,
            "function": func_name,
        })
    exec_rt.on_stream = _on_stream

    _s._save_session(session_id)

    # Install the session's DAG GraphStore so the
    # @agentic_function decorator and Runtime.exec invoked
    # inside the program persist their code / llm nodes into
    # this session's DAG — the same wiring process_user_turn
    # does for the chat path (dispatcher.py:334). Without it
    # `_store` is None and every DAG write inside the program
    # silently no-ops, so a multi-step agent run collapsed to
    # nothing in the graph.
    from openprogram.context.storage import (
        GraphStore as _GraphStore,
        _store as _store_var,
    )
    from openprogram.agentic_programming.function import (
        _call_id as _call_id_var,
    )
    from openprogram.agent.session_db import default_db as _default_db
    _store_token = None
    _call_id_token = None
    try:
        _store_token = _store_var.set(
            _GraphStore(_default_db().db_path, session_id)
        )
        # Anchor the program's top-level @agentic_function node
        # under the "run ..." command message: the decorator
        # reads `_call_id` for `called_by`, so setting it to the
        # command's id links the whole execution subtree to the
        # conversation instead of leaving it a floating root.
        _call_id_token = _call_id_var.set(msg_id)
    except Exception:
        _store_token = None
        _call_id_token = None

    # Live progress: a long @agentic_function run would
    # otherwise show nothing but a spinner until it ends.
    # `live_progress` (webui/_exec_dag.py) polls the DAG
    # while the run executes and pushes tree_update +
    # branches_list so the UI fills in node by node.
    with live_progress(session_id, msg_id, func_name), \
            _s._web_follow_up(session_id, msg_id, func_name, tree_cb=None):
        try:
            result = _s._format_result(loaded_func(**call_kwargs), action=func_name)
        finally:
            with _s._running_tasks_lock:
                _s._running_tasks.pop(session_id, None)
            _s._emit_running_task_event(session_id)
            _s._unregister_active_runtime(session_id)
            if _call_id_token is not None:
                try:
                    _call_id_var.reset(_call_id_token)
                except Exception:
                    pass
            if _store_token is not None:
                try:
                    _store_var.reset(_store_token)
                except Exception:
                    pass
        # Store session id for modify/resume before closing
        _last_session_id = getattr(exec_rt, 'last_thread_id', None) or getattr(exec_rt, '_session_id', None)
        # For Claude Code: keep runtime alive for modify reuse
        # For others: close after extracting session id
        _is_persistent = getattr(exec_rt, "has_session", False)
        if _is_persistent:
            # Close the previous stored runtime if any
            old_rt = conv.get("_last_exec_runtime")
            if old_rt and old_rt is not exec_rt and hasattr(old_rt, 'close'):
                old_rt.close()
            conv["_last_exec_runtime"] = exec_rt
        else:
            if hasattr(exec_rt, 'close'):
                exec_rt.close()

    # Store session id and cumulative usage in conversation for modify reuse
    if _last_session_id:
        conv["_last_exec_session"] = _last_session_id
    _cum = getattr(exec_rt, '_session_cumulative', None)
    if _cum:
        conv["_last_exec_cumulative_usage"] = _cum

    # Execution Tree — rebuilt from the DAG nodes the run
    # wrote to SessionDB (nested function + LLM calls), so
    # the inline tree matches the right-rail graph. Falls
    # back to a flat single-node stub if the run left no
    # nodes (e.g. an expose="hidden" function).
    tree_dict = build_exec_dag(session_id, func_name, msg_id) or {
        "path": func_name,
        "name": func_name,
        "params": {k: v for k, v in call_kwargs.items() if k != "runtime"},
        "output": result,
        "status": "success",
    }

    _s._log(f"[exec] {func_name} completed, result length: {len(str(result))}")

    # Store assistant reply with attempts array
    now = time.time()
    _func_usage = getattr(exec_rt, 'last_usage', None) or {}
    attempt_entry = {
        "content": str(result),
        "tree": tree_dict,
        "timestamp": now,
        "usage": _func_usage,
    }
    reply_msg = {
        "role": "assistant",
        "type": "result",
        "id": msg_id + "_reply",
        "content": str(result),
        "function": func_name,
        "display": "runtime",
        "timestamp": now,
        "attempts": [attempt_entry],
        "current_attempt": 0,
        "usage": _func_usage,
        "parent_id": msg_id,  # child of this run's user turn
    }
    _s._append_msg(conv, reply_msg)
    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": str(result),
        "function": func_name,
        "display": "runtime",
        "context_tree": tree_dict,
        "attempts": reply_msg["attempts"],
        "current_attempt": 0,
        "usage": _func_usage,
    })
    _s._broadcast_context_stats(session_id, msg_id, exec_runtime=exec_rt)
