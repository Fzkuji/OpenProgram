"""
Visualization server — FastAPI + WebSocket for real-time Context tree viewing
and interactive chat-style function execution.

Runs in a background thread alongside user code. Streams tree updates to
connected browsers via WebSocket.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Optional

from openprogram.agentic_programming.context import Context, _current_ctx
from openprogram.programs.functions.buildin.ask_user import set_ask_user, ask_user
from openprogram.agentic_programming.events import on_event, off_event
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime

# Pause / stop / cancel primitives live in agentic_web._pause_stop
from openprogram.webui._pause_stop import (
    pause_execution,
    resume_execution,
    wait_if_paused,
    mark_cancelled as _mark_cancelled,
    is_cancelled as _is_cancelled,
    clear_cancel as _clear_cancel,
    register_active_runtime as _register_active_runtime,
    unregister_active_runtime as _unregister_active_runtime,
    kill_active_runtime as _kill_active_runtime,
    mark_context_cancelled as _mark_context_cancelled,
    set_current_conv_id as _set_current_conv_id,
    reset_current_conv_id as _reset_current_conv_id,
)
from openprogram.agentic_programming.function import CancelledError as _CancelledError


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_root_contexts: list[dict] = []
_root_contexts_lock = threading.Lock()
_ws_connections: list[Any] = []
_ws_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Conversation storage (in-memory)
_conversations: dict[str, dict] = {}
_conversations_lock = threading.Lock()

# Global default providers (used when creating new conversations)
# (Provider state moved to openprogram.webui._runtime_management)

# Follow-up answer queues — keyed by conversation ID. When a function calls
# ask_user(), the handler puts the question on WebSocket and blocks on this
# queue. The frontend sends the answer back via WebSocket.
_follow_up_queues: dict = {}
_follow_up_lock = threading.Lock()

# Track running tasks so refresh can recover them
_running_tasks: dict = {}  # conv_id → {msg_id, func_name, started_at, ...}
_running_tasks_lock = threading.Lock()



# ---------------------------------------------------------------------------
# Follow-up context manager — shared by run / edit / any command handler
# ---------------------------------------------------------------------------
from contextlib import contextmanager as _contextmanager


@_contextmanager
def _web_follow_up(conv_id: str, msg_id: str, func_name: str, tree_cb=None):
    """Set up follow-up question support for a web UI command execution.

    Registers a global ask_user handler that sends follow-up questions to
    the browser via WebSocket and blocks until the user answers.

    Args:
        conv_id:   Conversation ID (for routing the answer back).
        msg_id:    Message ID (for associating with the right chat message).
        func_name: Function name (for display in the frontend).
        tree_cb:   Optional tree event callback to trigger on follow-up.
    """
    fq = queue.Queue()
    with _follow_up_lock:
        _follow_up_queues[conv_id] = fq

    def _handler(question: str) -> str:
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "follow_up_question",
            "question": question,
            "function": func_name,
        })
        if tree_cb is not None:
            tree_cb("follow_up", {})
        try:
            return fq.get(timeout=300)
        except queue.Empty:
            return ""

    set_ask_user(_handler)
    try:
        yield
    finally:
        set_ask_user(None)
        with _follow_up_lock:
            _follow_up_queues.pop(conv_id, None)



# ---------------------------------------------------------------------------
# Runtime / provider management lives in openprogram.webui._runtime_management
# ---------------------------------------------------------------------------
from openprogram.webui import _runtime_management
from openprogram.webui._runtime_management import (
    _CLI_PROVIDERS,
    _prev_rt_closed,
    _create_runtime_for_visualizer,
    _detect_default_provider,
    _init_providers,
    _get_conv_runtime,
    _get_exec_runtime,
    _switch_runtime,
    _get_provider_info,
)



_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".agentic", "config.json")

from openprogram.webui import persistence as _persist


def _save_conversation(conv_id: str):
    """Persist one conversation's meta + messages.

    Per-function execution trees are written incrementally by
    ``append_tree_event`` in the tree event callback — we do not rewrite
    them here.
    """
    if not conv_id:
        return
    with _conversations_lock:
        conv = _conversations.get(conv_id)
        if conv is None:
            return
        root_ctx = conv.get("root_context")
        runtime = conv.get("runtime")
        meta = {
            "id": conv_id,
            "title": conv.get("title", "Untitled"),
            "provider_name": conv.get("provider_name"),
            "session_id": getattr(runtime, "_session_id", None),
            "model": getattr(runtime, "model", None),
            "created_at": conv.get("created_at"),
            "context_tree": root_ctx._to_dict() if root_ctx is not None else None,
            "_chat_usage": conv.get("_chat_usage"),
            "_last_context_stats": conv.get("_last_context_stats"),
            "_titled": conv.get("_titled", False),
            "_last_exec_session": conv.get("_last_exec_session"),
            "_last_exec_cumulative_usage": conv.get("_last_exec_cumulative_usage"),
        }
        messages = list(conv.get("messages", []))
    try:
        _persist.save_meta(conv_id, meta)
        _persist.save_messages(conv_id, messages)
    except Exception as e:
        _log(f"[save_conversation] {conv_id} error: {e}")


def _delete_conversation_files(conv_id: str):
    try:
        _persist.delete_conversation(conv_id)
    except Exception as e:
        _log(f"[delete_conversation_files] {conv_id} error: {e}")


def _restore_sessions():
    """Restore conversations from ~/.agentic/sessions/ on startup.

    First migrates the legacy monolithic file if present.
    """
    try:
        migrated = _persist.migrate_legacy_file()
        if migrated:
            _log(f"[restore] migrated {migrated} legacy conversation(s)")
    except Exception as e:
        _log(f"[restore] migration failed: {e}")

    for conv_id in _persist.list_conversations():
        try:
            data = _persist.load_conversation(conv_id)
            if data is None:
                continue

            root_ctx = None
            ct = data.get("context_tree")
            if ct:
                root_ctx = Context.from_dict(ct)
                root_ctx.status = "idle"

            provider_name = data.get("provider_name")
            session_id = data.get("session_id")
            model = data.get("model")

            runtime = None
            if provider_name:
                try:
                    runtime = _create_runtime_for_visualizer(provider_name)
                    if model:
                        runtime.model = model
                    if session_id and hasattr(runtime, "_session_id"):
                        runtime._session_id = session_id
                        runtime._turn_count = 1
                        runtime.has_session = True
                except Exception:
                    pass

            with _conversations_lock:
                _conversations[conv_id] = {
                    "id": conv_id,
                    "title": data.get("title", "Untitled"),
                    "root_context": root_ctx,
                    "runtime": runtime,
                    "provider_name": provider_name,
                    "messages": data.get("messages", []),
                    "function_trees": data.get("function_trees", []),
                    "created_at": data.get("created_at", time.time()),
                    "_titled": data.get("_titled", True),
                    "_chat_usage": data.get("_chat_usage"),
                    "_last_context_stats": data.get("_last_context_stats"),
                    "_last_exec_session": data.get("_last_exec_session"),
                    "_last_exec_cumulative_usage": data.get("_last_exec_cumulative_usage"),
                }
            _log(f"[restore] conv {conv_id}: {data.get('title')} (session={session_id})")
        except Exception as e:
            _log(f"[restore] failed for {conv_id}: {e}")


def _load_config() -> dict:
    """Load config from ~/.agentic/config.json."""
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(config: dict):
    """Save config to ~/.agentic/config.json."""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _get_api_key(env_var: str) -> str:
    """Get API key from environment or config file."""
    val = os.environ.get(env_var)
    if val:
        return val
    config = _load_config()
    return config.get("api_keys", {}).get(env_var, "")


def _apply_config_keys():
    """Inject config file API keys into environment (if not already set)."""
    config = _load_config()
    for env_var, val in config.get("api_keys", {}).items():
        if val and not os.environ.get(env_var):
            os.environ[env_var] = val


# Apply config keys on module load
_apply_config_keys()


def _list_providers() -> list[dict]:
    """List available providers and their status."""
    import shutil
    result = []
    checks = [
        # (name, label, available_check, env_keys_for_config_or_None_if_CLI)
        ("openai-codex", "Codex CLI", lambda: shutil.which("codex") is not None, None),
        ("claude-code", "Claude Code CLI", lambda: shutil.which("claude") is not None, None),
        ("gemini-cli", "Gemini CLI", lambda: shutil.which("gemini") is not None, None),
        ("anthropic", "Anthropic API", lambda: bool(_get_api_key("ANTHROPIC_API_KEY")), ["ANTHROPIC_API_KEY"]),
        ("openai", "OpenAI API", lambda: bool(_get_api_key("OPENAI_API_KEY")), ["OPENAI_API_KEY"]),
        ("gemini", "Gemini API", lambda: bool(_get_api_key("GOOGLE_API_KEY") or _get_api_key("GOOGLE_GENERATIVE_AI_API_KEY")), ["GOOGLE_API_KEY"]),
    ]
    for name, label, check, env_keys in checks:
        available = check()
        result.append({
            "name": name,
            "label": label,
            "available": available,
            "active": name == _runtime_management._default_provider,
            "configurable": env_keys is not None,
            "configured": available if env_keys else None,
            "env_keys": env_keys,
        })
    return result


def _find_root(ctx_data: dict) -> Optional[dict]:
    """Walk up to the root of a context path and find the stored root."""
    path = ctx_data.get("path", "")
    root_name = path.split("/")[0] if "/" in path else path
    with _root_contexts_lock:
        for r in _root_contexts:
            if r.get("name") == root_name or r.get("path") == root_name:
                return r
    return None


def _on_context_event(event_type: str, data: dict):
    """Callback registered with the Context event system."""
    _log(f"[event] {event_type}: {data.get('path', '?')} status={data.get('status', '?')}")
    # If we're paused and a node just got created, wait
    if event_type == "node_created":
        wait_if_paused()
        # When stop fires on a paused task, resume_execution() unblocks this
        # thread. Raise immediately so the worker aborts at the nearest node
        # boundary instead of running another full agentic call first.
        from openprogram.webui._pause_stop import _current_conv_id as _cv
        _cid = _cv.get(None)
        if _cid and _is_cancelled(_cid):
            raise _CancelledError(f"Execution stopped by user (conv={_cid})")

    # Store/update root contexts
    path = data.get("path", "")
    if "/" not in path:
        # This is a root node
        with _root_contexts_lock:
            # Update existing or add new
            found = False
            for i, r in enumerate(_root_contexts):
                if r.get("path") == path:
                    _root_contexts[i] = data
                    found = True
                    break
            if not found:
                _root_contexts.append(data)

    # Broadcast to all connected WebSocket clients
    msg = json.dumps({"type": "event", "event": event_type, "data": data}, default=str)
    _broadcast(msg)


def _broadcast(msg: str):
    """Send a message to all connected WebSocket clients."""
    if not _ws_connections or _loop is None:
        return
    with _ws_lock:
        conns = list(_ws_connections)
    for ws in conns:
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(msg), _loop)
        except Exception:
            pass


def _log(text: str):
    """Print to terminal AND broadcast to frontend as a visible log."""
    print(text)
    try:
        msg = json.dumps({"type": "server_log", "text": text}, default=str)
        _broadcast(msg)
    except Exception:
        pass


def _get_full_tree() -> list[dict]:
    """Get current root-level context trees by walking active contexts."""
    # First check if there's a currently running context
    try:
        current = _current_ctx.get(None)
        if current is not None:
            # Walk up to root
            root = current
            while root.parent is not None:
                root = root.parent
            return [root._to_dict()]
    except Exception:
        pass

    with _root_contexts_lock:
        return list(_root_contexts)


def _cleanup_conv_resources(conv_id: str, conv: dict):
    """Clean up all resources associated with a deleted conversation."""
    # Remove root_contexts entries — match by the conversation's root_context name
    root_ctx = conv.get("root_context")
    if root_ctx:
        root_name = getattr(root_ctx, 'name', None) or (root_ctx.get("name") if isinstance(root_ctx, dict) else None)
        if root_name:
            with _root_contexts_lock:
                _root_contexts[:] = [r for r in _root_contexts if r.get("name") != root_name]
    # Clean up follow-up queues and running tasks
    _follow_up_queues.pop(conv_id, None)
    with _running_tasks_lock:
        _running_tasks.pop(conv_id, None)


from openprogram.webui._functions import (
    _discover_functions,
    _extract_input_meta,
    _extract_function_info,
    _extract_all_functions,
    _get_last_ctx,
    _inject_runtime,
    _format_result,
    _find_node_by_path,
    _find_in_tree,
    _FunctionStub,
    _make_stub_from_file,
    _load_function,
)


# ---------------------------------------------------------------------------
# (Function discovery & loading moved to agentic_web._functions)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Conversation management — each conversation has a Context tree
# ---------------------------------------------------------------------------

def _get_or_create_conversation(conv_id: str = None) -> dict:
    """Get or create a conversation with its own Context tree and Runtime."""
    if conv_id is None:
        conv_id = str(uuid.uuid4())[:8]
    with _conversations_lock:
        if conv_id not in _conversations:
            _conversations[conv_id] = {
                "id": conv_id,
                "title": "New conversation",
                "root_context": Context(name="chat_session", status="idle", start_time=time.time()),
                "runtime": None,          # created lazily on first message
                "provider_name": None,
                "messages": [],
                "function_trees": [],
                "created_at": time.time(),
            }
        return _conversations[conv_id]


# Provider-specific thinking effort configurations
# Per-user defaults (explicit; do not revert without asking):
#   - Claude (claude-code, anthropic): auto / max available — adaptive thinking
#   - GPT (codex, openai): maximum effort — the user wants the strongest setting
_THINKING_CONFIGS = {
    "claude-code": {
        "label": "effort",
        "options": [
            {"value": "low", "desc": "Quick responses"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Extra deep reasoning"},
            {"value": "max", "desc": "Maximum effort"},
            {"value": "auto", "desc": "Adaptive"},
        ],
        "default": "auto",
    },
    "openai-codex": {
        "label": "reasoning effort",
        "options": [
            {"value": "none", "desc": "No reasoning"},
            {"value": "low", "desc": "Quick reasoning"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Maximum effort"},
        ],
        "default": "xhigh",
    },
    "anthropic": {
        "label": "thinking",
        "options": [
            {"value": "off", "desc": "No extended thinking"},
            {"value": "low", "desc": "Brief thinking"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Extended thinking"},
        ],
        "default": "high",
    },
    "openai": {
        "label": "reasoning effort",
        "options": [
            {"value": "low", "desc": "Quick reasoning"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Deep reasoning"},
            {"value": "xhigh", "desc": "Maximum effort"},
        ],
        "default": "xhigh",
    },
    "gemini": {
        "label": "thinking",
        "options": [
            {"value": "off", "desc": "No thinking"},
            {"value": "low", "desc": "Brief thinking"},
            {"value": "medium", "desc": "Balanced"},
            {"value": "high", "desc": "Extended thinking"},
            {"value": "auto", "desc": "Dynamic"},
        ],
        "default": "auto",
    },
}


def _get_thinking_config(provider: str) -> dict:
    """Get thinking effort config for a provider."""
    return _THINKING_CONFIGS.get(provider, _THINKING_CONFIGS.get("openai-codex"))


def _default_effort_for(runtime) -> str:
    """Resolve the provider-specific default thinking effort for a runtime.

    Maps runtime class -> provider key -> _THINKING_CONFIGS[...].default.
    Used anywhere we previously fell back to the hardcoded string "medium".
    """
    rt_type = type(runtime).__name__
    mapping = {
        "ClaudeCodeRuntime": "claude-code",
        "OpenAICodexRuntime": "openai-codex",
        "AnthropicRuntime": "anthropic",
        "OpenAIRuntime": "openai",
        "GeminiRuntime": "gemini",
        "GeminiCLIRuntime": "gemini-cli",
    }
    provider = mapping.get(rt_type, "openai-codex")
    return _THINKING_CONFIGS.get(provider, {}).get("default")


def _resolve_effort(effort, runtime) -> str:
    """Return effort if non-empty, else the runtime's provider-default."""
    if effort:
        return effort
    return _default_effort_for(runtime)


def _apply_thinking_effort(runtime, effort: str):
    """Apply thinking effort setting to a runtime based on its provider type.

    Maps effort levels to provider-specific parameters:
      - Codex CLI:       --reasoning-effort flag (none/low/medium/high/xhigh)
      - Claude Code CLI: --effort flag (requires process restart if changed)
      - Anthropic API:   thinking budget parameter
      - OpenAI API:      reasoning_effort parameter
    """
    rt_type = type(runtime).__name__

    # Resolve None/empty -> provider default (no hardcoded "medium")
    effort = _resolve_effort(effort, runtime)

    if rt_type == "OpenAICodexRuntime":
        runtime._reasoning_effort = effort
    elif rt_type == "ClaudeCodeRuntime":
        old_effort = getattr(runtime, '_thinking_effort', None)
        if effort != old_effort:
            runtime._thinking_effort = effort
            # Claude Code CLI needs process restart for effort change
            if hasattr(runtime, '_restart_process'):
                runtime._restart_process()
    else:
        runtime._thinking_effort = effort


def _execute_in_context(conv_id: str, msg_id: str, action: str,
                        func_name: str = None, kwargs: dict = None, query: str = None,
                        thinking_effort: str = None, exec_thinking_effort: str = None):
    """Execute a chat query or function call within the conversation's Context tree.

    This is the core execution engine. Everything runs under the conversation's
    root Context, so summarize() automatically provides conversation history.
    """
    _conv_token = _set_current_conv_id(conv_id)
    try:
        conv = _get_or_create_conversation(conv_id)
        runtime = _get_conv_runtime(conv_id, msg_id=msg_id)

        # Apply thinking effort to chat runtime
        _apply_thinking_effort(runtime, thinking_effort)

        try:
            if action == "query":
                # Direct chat — include conversation history for context
                _log(f"[exec] query: {query[:80]}... (thinking={thinking_effort})")
                with _running_tasks_lock:
                    _running_tasks[conv_id] = {
                        "msg_id": msg_id,
                        "func_name": "_chat",
                        "started_at": time.time(),
                        "display_params": "",
                        "loaded_func_ref": None,
                        "stream_events": [],
                    }
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "status", "content": "Thinking...",
                })

                # Build conversation context from history
                # Rough token estimate: ~4 chars per token, keep under 80k tokens
                _MAX_CONTEXT_CHARS = 320_000
                history_parts = []
                total_chars = 0
                messages = conv.get("messages", [])
                # Walk backwards to prioritize recent messages
                for m in reversed(messages):
                    role = m.get("role", "")
                    content = m.get("content", "")
                    if not content:
                        continue
                    if role == "user":
                        display = m.get("display", "")
                        if display == "runtime":
                            entry = f"[User ran function]: {content}"
                        else:
                            entry = f"[User]: {content}"
                    elif role == "assistant":
                        fn = m.get("function", "")
                        if fn:
                            entry = f"[Function {fn} returned]: {content}"
                        else:
                            entry = f"[Assistant]: {content}"
                    else:
                        continue
                    if total_chars + len(entry) > _MAX_CONTEXT_CHARS:
                        break
                    history_parts.append(entry)
                    total_chars += len(entry)
                history_parts.reverse()

                chat_content = []
                if history_parts:
                    context_text = (
                        "── Conversation history ──\n"
                        + "\n".join(history_parts)
                        + "\n── End of history ──\n\n"
                    )
                    chat_content.append({"type": "text", "text": context_text})
                chat_content.append({"type": "text", "text": query})

                # Set up streaming for CLI providers (enables usage tracking + CLI Output)
                def _on_chat_stream(event: dict):
                    with _running_tasks_lock:
                        ti = _running_tasks.get(conv_id)
                        if ti and "stream_events" in ti:
                            ti["stream_events"].append(event)
                            if len(ti["stream_events"]) > 200:
                                ti["stream_events"] = ti["stream_events"][-200:]
                    _broadcast_chat_response(conv_id, msg_id, {
                        "type": "stream_event",
                        "event": event,
                        "function": "_chat",
                    })
                runtime.on_stream = _on_chat_stream
                _register_active_runtime(conv_id, runtime)

                try:
                    result = runtime.exec(content=chat_content)
                finally:
                    runtime.on_stream = None
                    with _running_tasks_lock:
                        _running_tasks.pop(conv_id, None)
                    _unregister_active_runtime(conv_id)
                _log(f"[exec] query completed, result length: {len(str(result))}")

                # Store assistant reply
                conv["messages"].append({
                    "role": "assistant",
                    "id": msg_id + "_reply",
                    "content": str(result),
                    "timestamp": time.time(),
                })

                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                })
                _broadcast_context_stats(conv_id, msg_id, chat_runtime=runtime)

            elif action == "run":
                # Validate create() description
                if func_name == "create" and kwargs and "description" in kwargs:
                    desc = kwargs["description"].strip()
                    if len(desc) < 5:
                        _broadcast_chat_response(conv_id, msg_id, {
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
                            _broadcast_chat_response(conv_id, msg_id, {
                                "type": "result",
                                "content": f"Unclear description: {reason}\n\nPlease describe what the function should **do**.",
                                "function": func_name,
                            })
                            return
                    except Exception:
                        pass

                _log(f"[exec] running function: {func_name}({', '.join(f'{k}=...' for k in (kwargs or {}))})")
                # Build display params string (exclude runtime/callback)
                _display_params = ", ".join(
                    f"{k}={v!r}" if len(repr(v)) < 60 else f"{k}=..."
                    for k, v in (kwargs or {}).items()
                    if k not in ("runtime", "callback")
                )
                with _running_tasks_lock:
                    _running_tasks[conv_id] = {
                        "msg_id": msg_id,
                        "func_name": func_name,
                        "started_at": time.time(),
                        "display_params": _display_params,
                        "loaded_func_ref": None,  # set after load
                        "stream_events": [],  # buffered for refresh recovery
                    }
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "status",
                    "content": f"Running {func_name}...",
                })

                loaded_func = _load_function(func_name)
                if loaded_func is None:
                    _broadcast_chat_response(conv_id, msg_id, {"type": "error", "content": f"Function '{func_name}' not found."})
                    return
                with _running_tasks_lock:
                    if conv_id in _running_tasks:
                        _running_tasks[conv_id]["loaded_func_ref"] = loaded_func
                call_kwargs = dict(kwargs or {})
                # Resolve string function-name parameters to actual function objects
                # (e.g. edit(function="sentiment") → edit(function=<sentiment function>))
                for param_key in ("fn", "function"):
                    if param_key in call_kwargs and isinstance(call_kwargs[param_key], str):
                        resolved_function = _load_function(call_kwargs[param_key])
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
                exec_rt = _get_exec_runtime(no_tools=_no_tools)
                _apply_thinking_effort(exec_rt, exec_thinking_effort)
                if _work_dir:
                    _work_dir = os.path.abspath(os.path.expanduser(_work_dir))
                    os.makedirs(_work_dir, exist_ok=True)
                    exec_rt.set_workdir(_work_dir)
                    conv.setdefault("last_workdirs", {})[func_name] = _work_dir
                    _log(f"[exec] workdir: {_work_dir}")
                _log(f"[exec] new runtime: provider={type(exec_rt).__name__}, no_tools={_no_tools}, id={id(exec_rt)}, thinking={exec_thinking_effort}")
                _register_active_runtime(conv_id, exec_rt)
                _inject_runtime(loaded_func, call_kwargs, exec_rt)

                # Register streaming callback for real-time LLM output
                def _on_stream(event: dict):
                    # Buffer for refresh recovery (keep last 200 events)
                    with _running_tasks_lock:
                        ti = _running_tasks.get(conv_id)
                        if ti and "stream_events" in ti:
                            ti["stream_events"].append(event)
                            if len(ti["stream_events"]) > 200:
                                ti["stream_events"] = ti["stream_events"][-200:]
                    _broadcast_chat_response(conv_id, msg_id, {
                        "type": "stream_event",
                        "event": event,
                        "function": func_name,
                    })
                exec_rt.on_stream = _on_stream

                # Reserve the func_idx + attempt slot and open its JSONL file
                # so live events can append and refresh shows progress.
                if "function_trees" not in conv:
                    conv["function_trees"] = []
                _run_func_idx = len(conv["function_trees"])
                _run_attempt_idx = 0
                _run_placeholder_tree = {
                    "path": func_name,
                    "name": func_name,
                    "params": {k: v for k, v in call_kwargs.items() if k != "runtime"},
                    "status": "running",
                    "start_time": time.time(),
                    "children": [],
                    "_in_progress": True,
                }
                conv["function_trees"].append(_run_placeholder_tree)
                _persist.init_tree(conv_id, _run_func_idx, _run_attempt_idx)
                _save_conversation(conv_id)

                # Register event-driven tree updates: append each node event
                # to the JSONL file and broadcast a full partial tree.
                def _tree_event_callback(event_type: str, data: dict):
                    try:
                        if event_type == "node_created":
                            _persist.append_tree_event(
                                conv_id, _run_func_idx, _run_attempt_idx,
                                {
                                    "event": "enter",
                                    "path": data.get("path"),
                                    "name": data.get("name"),
                                    "node_type": data.get("node_type", "function"),
                                    "prompt": data.get("prompt", ""),
                                    "params": data.get("params") or {},
                                    "render": data.get("render", "summary"),
                                    "compress": data.get("compress", False),
                                    "ts": data.get("start_time"),
                                },
                            )
                        elif event_type == "node_completed":
                            _persist.append_tree_event(
                                conv_id, _run_func_idx, _run_attempt_idx,
                                {
                                    "event": "exit",
                                    "path": data.get("path"),
                                    "status": data.get("status"),
                                    "output": data.get("output"),
                                    "raw_reply": data.get("raw_reply"),
                                    "attempts": data.get("attempts", []),
                                    "error": data.get("error", ""),
                                    "duration_ms": data.get("duration_ms"),
                                    "ts": data.get("end_time"),
                                },
                            )

                        ctx = _get_last_ctx(loaded_func)
                        if ctx is None:
                            ctx = getattr(loaded_func, 'context', None)
                        if ctx is not None:
                            partial_tree = ctx._to_dict()
                            partial_tree["_in_progress"] = True
                            if _run_func_idx < len(conv.get("function_trees", [])):
                                conv["function_trees"][_run_func_idx] = partial_tree
                            _broadcast_chat_response(conv_id, msg_id, {
                                "type": "tree_update",
                                "tree": partial_tree,
                                "function": func_name,
                            })
                    except Exception:
                        pass

                on_event(_tree_event_callback)

                # Follow-up support + execution
                with _web_follow_up(conv_id, msg_id, func_name, tree_cb=_tree_event_callback):
                    try:
                        result = _format_result(loaded_func(**call_kwargs), action=func_name)
                    finally:
                        off_event(_tree_event_callback)
                        with _running_tasks_lock:
                            _running_tasks.pop(conv_id, None)
                        _unregister_active_runtime(conv_id)
                    # Store session id for modify/resume before closing
                    _last_session_id = getattr(exec_rt, 'last_thread_id', None) or getattr(exec_rt, '_session_id', None)
                    # For Claude Code: keep runtime alive for modify reuse
                    # For others: close after extracting session id
                    _is_persistent = type(exec_rt).__name__ == "ClaudeCodeRuntime"
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

                # Get the context tree from @agentic_function's wrapper
                func_ctx = _get_last_ctx(loaded_func)
                if func_ctx:
                    tree_dict = func_ctx._to_dict()
                else:
                    # Plain function without @agentic_function — build minimal tree
                    tree_dict = {
                        "path": func_name,
                        "name": func_name,
                        "params": {k: v for k, v in call_kwargs.items() if k != "runtime"},
                        "output": result,
                        "status": "success",
                    }

                # Replace the placeholder reserved before execution with the
                # final tree (see `_run_func_idx` above).
                if "function_trees" not in conv:
                    conv["function_trees"] = []
                if 0 <= _run_func_idx < len(conv["function_trees"]):
                    conv["function_trees"][_run_func_idx] = tree_dict
                else:
                    conv["function_trees"].append(tree_dict)

                _log(f"[exec] {func_name} completed, result length: {len(str(result))}")

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
                }
                conv["messages"].append(reply_msg)
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                    "function": func_name,
                    "display": "runtime",
                    "context_tree": tree_dict,
                    "attempts": reply_msg["attempts"],
                    "current_attempt": 0,
                    "usage": _func_usage,
                })
                _broadcast_context_stats(conv_id, msg_id, exec_runtime=exec_rt)

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
            _broadcast(json.dumps({
                "type": "chat_session_update",
                "data": {"session_id": chat_session_id},
            }, default=str))

        # Persist sessions to disk after each execution
        _save_conversation(conv_id)

    except (Exception, _CancelledError) as e:
        with _running_tasks_lock:
            _running_tasks.pop(conv_id, None)
        _unregister_active_runtime(conv_id)

        # Cancellation path — either the exception came from /api/stop killing
        # the subprocess, or a CancelledError was raised by the cancel hook
        # (e.g. loops between exec calls). Mark any still-running tree nodes
        # as cancelled and emit a "stopped" result instead of an error message.
        if _is_cancelled(conv_id) or isinstance(e, _CancelledError):
            _clear_cancel(conv_id)
            ctx = None
            _lf = locals().get("loaded_func")
            if _lf is not None:
                try:
                    ctx = _get_last_ctx(_lf) or getattr(_lf, "context", None)
                except Exception:
                    ctx = None
            if ctx is not None:
                try:
                    _mark_context_cancelled(ctx)
                    # Persist synthetic exit records for nodes that were
                    # running when cancellation fired. Without these, the
                    # JSONL only holds enter events for those nodes, so
                    # replay on page refresh shows them as running again.
                    _fidx = locals().get("_run_func_idx")
                    _aidx = locals().get("_run_attempt_idx")
                    if _fidx is not None and _aidx is not None:
                        def _walk(n):
                            if n is None:
                                return
                            if (getattr(n, "status", "") == "error"
                                    and getattr(n, "error", "") == "Cancelled by user"):
                                _persist.append_tree_event(
                                    conv_id, _fidx, _aidx,
                                    {
                                        "event": "exit",
                                        "path": n.path,
                                        "status": "error",
                                        "output": None,
                                        "raw_reply": None,
                                        "attempts": getattr(n, "attempts", []) or [],
                                        "error": "Cancelled by user",
                                        "duration_ms": getattr(n, "duration_ms", 0),
                                        "ts": getattr(n, "end_time", time.time()),
                                    },
                                )
                            for c in getattr(n, "children", []) or []:
                                _walk(c)
                        _walk(ctx)
                    _broadcast_chat_response(conv_id, msg_id, {
                        "type": "tree_update",
                        "tree": ctx._to_dict(),
                        "function": func_name,
                    })
                except Exception:
                    pass
            try:
                conv = _get_or_create_conversation(conv_id)
                now = time.time()
                conv["messages"].append({
                    "role": "assistant",
                    "type": "cancelled",
                    "id": msg_id + "_reply",
                    "content": "Execution stopped by user.",
                    "function": func_name,
                    "display": "runtime",
                    "timestamp": now,
                })
                _save_conversation(conv_id)
            except Exception:
                pass
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "result",
                "content": "Execution stopped by user.",
                "function": func_name,
                "cancelled": True,
                "context_tree": ctx._to_dict() if ctx is not None else None,
            })
            return

        error_content = f"Error: {e}\n\n{traceback.format_exc()}"
        # Persist error to conversation messages
        try:
            conv = _get_or_create_conversation(conv_id)
            now = time.time()
            error_msg = {
                "role": "assistant",
                "type": "error",
                "id": msg_id + "_reply",
                "content": error_content,
                "function": func_name,
                "display": "runtime",
                "timestamp": now,
                "attempts": [{"content": error_content, "timestamp": now}],
                "current_attempt": 0,
            }
            conv["messages"].append(error_msg)
            _save_conversation(conv_id)
        except Exception:
            pass
        _broadcast_chat_response(conv_id, msg_id, {
            "type": "error",
            "content": error_content,
            "function": func_name,
            "display": "runtime",
        })
    finally:
        _reset_current_conv_id(_conv_token)


def _broadcast_context_stats(conv_id: str, msg_id: str, chat_runtime=None, exec_runtime=None):
    """Broadcast chat & exec token usage stats to frontend.

    Chat usage: use the provider's latest reported value directly.
      - CLI providers report usage that already reflects the full session context.
      - API providers report usage that includes the full conversation in input_tokens.
      - No accumulation — provider knows best about its own usage.
    Exec usage: per-function execution, read from exec_runtime.last_usage.
    """
    conv = _conversations.get(conv_id)
    if not conv:
        return

    _zero = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}

    # --- Chat usage: use last_usage (per-call = current context window size) ---
    # NOT session_usage (cumulative across all API calls, inflated for Codex).
    # last_usage.input_tokens = total tokens sent in the last call ≈ context size.
    if chat_runtime:
        usage = getattr(chat_runtime, 'last_usage', None)
        print(f"[DEBUG] chat_runtime.last_usage = {usage}", flush=True)
        if usage and (usage.get("input_tokens") or usage.get("output_tokens") or usage.get("cache_read") or usage.get("cache_create")):
            conv["_chat_usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read": usage.get("cache_read", 0),
                "cache_create": usage.get("cache_create", 0),
            }
            print(f"[DEBUG] _chat_usage set to: {conv['_chat_usage']}", flush=True)

    # --- Exec usage (per-function, not cumulative) ---
    exec_stats = None
    if exec_runtime:
        eu = getattr(exec_runtime, 'last_usage', None)
        if eu and (eu.get("input_tokens") or eu.get("output_tokens") or eu.get("cache_read") or eu.get("cache_create")):
            exec_stats = {
                "input_tokens": eu.get("input_tokens", 0),
                "output_tokens": eu.get("output_tokens", 0),
                "cache_read": eu.get("cache_read", 0),
                "cache_create": eu.get("cache_create", 0),
            }

    # Include provider name so frontend can apply provider-specific formatting
    provider_name = conv.get("provider_name", _runtime_management._default_provider) or ""

    stats = {
        "type": "context_stats",
        "chat": conv.get("_chat_usage", dict(_zero)),
        "exec": exec_stats,
        "provider": provider_name,
    }
    conv["_last_context_stats"] = stats
    _broadcast_chat_response(conv_id, msg_id, stats)


def _broadcast_chat_response(conv_id: str, msg_id: str, response: dict):
    """Broadcast a chat response to all WebSocket clients."""
    response["conv_id"] = conv_id
    response["msg_id"] = msg_id
    response["timestamp"] = time.time()

    # No need to store in messages list — Context tree IS the storage
    msg = json.dumps({"type": "chat_response", "data": response}, default=str)
    _broadcast(msg)


def _parse_chat_input(text: str) -> dict:
    """Parse user input to determine intent.

    Returns dict with keys:
      - action: "run", "create", "edit", "query"
      - function: function name (if applicable)
      - kwargs: dict of arguments (if applicable)
      - raw: original text
    """
    text = text.strip()
    lower = text.lower()

    # "create ..." -> meta create
    if lower.startswith("create "):
        rest = text[7:].strip()
        # Check if it's "create app" or "create skill"
        if lower.startswith("create app "):
            return {"action": "run", "function": "create_app", "kwargs": {"description": text[11:].strip()}, "raw": text}
        if lower.startswith("create skill "):
            return {"action": "run", "function": "create_skill", "kwargs": {"name": text[13:].strip()}, "raw": text}
        # Parse: create "description" --name xxx  OR  create a function that...
        name = None
        desc = rest
        if "--name " in rest:
            idx = rest.index("--name ")
            name = rest[idx + 7:].strip().split()[0]
            desc = rest[:idx].strip().strip('"').strip("'")
        elif " as " in rest:
            parts = rest.rsplit(" as ", 1)
            desc = parts[0].strip().strip('"').strip("'")
            name = parts[1].strip()
        if not name:
            name = None  # Let create() auto-generate from description
        kwargs = {"description": desc}
        if name is not None:
            kwargs["name"] = name
        return {"action": "run", "function": "create", "kwargs": kwargs, "raw": text}

    # "edit ..." -> meta edit
    if lower.startswith("edit "):
        rest = text[5:].strip()
        parts = rest.split(maxsplit=1)
        name = parts[0]
        instruction = parts[1] if len(parts) > 1 else None
        kwargs = {"name": name}
        if instruction:
            kwargs["instruction"] = instruction
        return {"action": "run", "function": "edit", "kwargs": kwargs, "raw": text}

    # "run func_name key=val ..." -> direct run
    if lower.startswith("run "):
        rest = text[4:].strip()
        try:
            import shlex
            parts = shlex.split(rest)
        except ValueError:
            parts = rest.split()
        func_name = parts[0] if parts else ""
        kwargs = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                # Strip surrounding quotes that shlex preserves on values
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                # Try to parse as JSON value
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass
                kwargs[k] = v
        return {"action": "run", "function": func_name, "kwargs": kwargs, "raw": text}

    # Check if text starts with a known function name
    available = _discover_functions()
    for f in available:
        fname = f["name"]
        if lower.startswith(fname + " ") or lower == fname:
            rest = text[len(fname):].strip()
            kwargs = {}
            # Try to parse remaining as key=value pairs
            for p in rest.split():
                if "=" in p:
                    k, v = p.split("=", 1)
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    kwargs[k] = v
                elif f["params"]:
                    # Assign positionally to first unfilled param
                    for param_name in f["params"]:
                        if param_name not in kwargs and param_name != "runtime":
                            kwargs[param_name] = rest
                            break
                    break
            return {"action": "run", "function": fname, "kwargs": kwargs, "raw": text}

    # Default: general LLM query
    return {"action": "query", "raw": text}


# ---------------------------------------------------------------------------
# WebSocket handler (module-level to avoid FastAPI closure issues)
# ---------------------------------------------------------------------------

async def _websocket_handler(ws):
    """WebSocket endpoint for real-time Context tree updates and chat."""
    await ws.accept()

    with _ws_lock:
        _ws_connections.append(ws)
    try:
        # Send current state on connect
        tree = _get_full_tree()
        await ws.send_text(json.dumps(
            {"type": "full_tree", "data": tree}, default=str
        ))
        functions = _discover_functions()
        await ws.send_text(json.dumps(
            {"type": "functions_list", "data": functions}, default=str
        ))
        with _conversations_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
                for c in _conversations.values()
            ]
        await ws.send_text(json.dumps(
            {"type": "history_list", "data": history}, default=str
        ))
        # Send current provider info
        await ws.send_text(json.dumps(
            {"type": "provider_info", "data": _get_provider_info()}, default=str
        ))

        # Keep alive — receive pings/messages
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                try:
                    cmd = json.loads(data)
                    await _handle_ws_command(ws, cmd)
                except json.JSONDecodeError:
                    pass

    except Exception as e:
        import traceback
        print(f"[ws] connection error: {e}\n{traceback.format_exc()}")
    finally:
        with _ws_lock:
            try:
                _ws_connections.remove(ws)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# WebSocket command handler (module-level so _websocket_handler can call it)
# ---------------------------------------------------------------------------

async def _handle_ws_command(ws, cmd: dict):
    """Handle a WebSocket command from the client."""
    action = cmd.get("action")
    print(f"[ws] command received: action={action}")

    if action == "chat":
        text = cmd.get("text", "").strip()
        conv_id = cmd.get("conv_id")
        # None -> resolved per-provider inside _apply_thinking_effort
        thinking_effort = cmd.get("thinking_effort") or None
        exec_thinking_effort = cmd.get("exec_thinking_effort") or None
        if not text:
            return

        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        # Update title from first message
        if not conv.get("_titled"):
            conv["title"] = text[:50] + ("..." if len(text) > 50 else "")
            conv["_titled"] = True

        # Parse and execute
        parsed = _parse_chat_input(text)

        # Store user message (mark "run" commands with display: "runtime")
        user_msg = {
            "role": "user",
            "id": msg_id,
            "content": text,
            "timestamp": time.time(),
        }
        if parsed["action"] == "run":
            user_msg["display"] = "runtime"
        conv["messages"].append(user_msg)

        # Send acknowledgment with conv_id
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"conv_id": conv_id, "msg_id": msg_id},
        }))

        if parsed["action"] == "run":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"], "thinking_effort": thinking_effort, "exec_thinking_effort": exec_thinking_effort},
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "query"),
                kwargs={"query": parsed["raw"], "thinking_effort": thinking_effort},
                daemon=True,
            ).start()

    elif action == "retry_node":
        node_path = cmd.get("node_path")
        conv_id = cmd.get("conv_id")
        params_override = cmd.get("params")  # optional edited params
        _log(f"[retry] received retry_node: conv_id={conv_id}, node_path={node_path}, params_override={params_override}")
        if not node_path or not conv_id:
            _log(f"[retry] missing node_path or conv_id, aborting")
            await ws.send_text(json.dumps({
                "type": "chat_response",
                "data": {"type": "error", "content": "Retry failed: missing node_path or conv_id", "conv_id": conv_id or "", "msg_id": "err"},
            }))
            return
        msg_id = str(uuid.uuid4())[:8]
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"conv_id": conv_id, "msg_id": msg_id},
        }))
        _log(f"[retry] starting retry thread msg_id={msg_id}")
        threading.Thread(
            target=_retry_node,
            args=(conv_id, msg_id, node_path, params_override),
            daemon=True,
        ).start()

    elif action == "retry_overwrite":
        # Overwrite retry: remove old user+assistant messages for this function, re-run
        conv_id = cmd.get("conv_id")
        func_name = cmd.get("function")
        text = cmd.get("text", "").strip()
        thinking_effort = cmd.get("thinking_effort") or None
        exec_thinking_effort = cmd.get("exec_thinking_effort") or None
        if not conv_id or not text:
            return

        conv = _get_or_create_conversation(conv_id)

        # Retry = fresh session — clear old session state
        conv.pop("_last_exec_session", None)
        old_rt = conv.pop("_last_exec_runtime", None)
        if old_rt and hasattr(old_rt, 'close'):
            old_rt.close()

        messages = conv.get("messages", [])

        # Remove old user (runtime) + assistant messages for this function
        new_messages = []
        skip_next_assistant = False
        for m in messages:
            if skip_next_assistant and m.get("role") == "assistant":
                skip_next_assistant = False
                continue
            if (m.get("role") == "user" and m.get("display") == "runtime"):
                parsed_check = _parse_chat_input(m.get("content", ""))
                if parsed_check.get("function") == func_name:
                    skip_next_assistant = True
                    continue
            new_messages.append(m)
        conv["messages"] = new_messages

        # Remove old function_trees for this function
        conv["function_trees"] = [
            ft for ft in conv.get("function_trees", [])
            if ft.get("name") != func_name and ft.get("path") != func_name
        ]

        msg_id = str(uuid.uuid4())[:8]

        # Preserve original_content from the command payload (for Answer & Retry tracking)
        original_content = cmd.get("original_content", text)

        # Store new user message
        conv["messages"].append({
            "role": "user",
            "id": msg_id,
            "content": text,
            "original_content": original_content,
            "display": "runtime",
            "timestamp": time.time(),
        })

        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"conv_id": conv_id, "msg_id": msg_id},
        }))

        # Parse and execute
        parsed = _parse_chat_input(text)
        print(f"[retry] text={text[:200]}")
        print(f"[retry] parsed={parsed}")
        if parsed["action"] == "run":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"], "thinking_effort": thinking_effort, "exec_thinking_effort": exec_thinking_effort},
                daemon=True,
            ).start()
        else:
            _broadcast_chat_response(conv_id, msg_id, {
                "type": "error",
                "content": f"Could not parse retry command: {text[:100]}",
                "function": func_name,
                "display": "runtime",
            })

    elif action == "switch_attempt":
        conv_id = cmd.get("conv_id")
        func_name = cmd.get("function")
        attempt_idx = cmd.get("attempt_index", 0)
        conv = _conversations.get(conv_id)
        if conv:
            messages = conv.get("messages", [])
            msg_idx = None
            target_msg = None
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if (m.get("role") == "assistant"
                        and m.get("type") == "result"
                        and m.get("function") == func_name
                        and "attempts" in m):
                    target_msg = m
                    msg_idx = i
                    break

            if target_msg and 0 <= attempt_idx < len(target_msg["attempts"]):
                old_idx = target_msg.get("current_attempt", 0)
                attempts = target_msg["attempts"]

                # Save current subsequent messages to current attempt
                subsequent_now = messages[msg_idx + 1:]
                if old_idx < len(attempts):
                    attempts[old_idx]["subsequent_messages"] = subsequent_now

                # Switch to target attempt
                target_msg["current_attempt"] = attempt_idx
                target_msg["content"] = attempts[attempt_idx]["content"]

                # Restore target attempt's subsequent messages
                restored = attempts[attempt_idx].get("subsequent_messages", [])
                conv["messages"] = messages[:msg_idx + 1] + restored

                # Update function_trees to match selected attempt's tree
                selected_tree = attempts[attempt_idx].get("tree")
                if selected_tree:
                    func_trees = conv.get("function_trees", [])
                    for ti, ft in enumerate(func_trees):
                        if ft.get("name") == func_name or ft.get("path") == func_name:
                            func_trees[ti] = selected_tree
                            break

                _save_conversation(conv_id)
                await ws.send_text(json.dumps({
                    "type": "attempt_switched",
                    "data": {
                        "function": func_name,
                        "attempt_index": attempt_idx,
                        "content": attempts[attempt_idx]["content"],
                        "tree": attempts[attempt_idx].get("tree"),
                        "total": len(attempts),
                        "subsequent_messages": restored,
                    },
                }, default=str))

    elif action == "delete_conversation":
        conv_id = cmd.get("conv_id")
        if conv_id:
            with _conversations_lock:
                conv = _conversations.pop(conv_id, None)
            if conv:
                if conv.get("runtime") and hasattr(conv["runtime"], 'close'):
                    conv["runtime"].close()
                # Clean up root_contexts entries belonging to this conversation
                _cleanup_conv_resources(conv_id, conv)
            _delete_conversation_files(conv_id)

    elif action == "clear_conversations":
        with _conversations_lock:
            conv_ids = list(_conversations.keys())
            convs = list(_conversations.values())
            for conv in convs:
                if conv.get("runtime") and hasattr(conv["runtime"], 'close'):
                    conv["runtime"].close()
            _conversations.clear()
        # Clean up all root_contexts and queues
        with _root_contexts_lock:
            _root_contexts.clear()
        for cid in conv_ids:
            _follow_up_queues.pop(cid, None)
            with _running_tasks_lock:
                _running_tasks.pop(cid, None)
            _delete_conversation_files(cid)

    elif action == "load_conversation":
        conv_id = cmd.get("conv_id")
        with _conversations_lock:
            conv = _conversations.get(conv_id)
        if conv:
            # Send conversation with messages + Context tree + provider info
            tree_data = conv["root_context"]._to_dict() if conv.get("root_context") else {}
            await ws.send_text(json.dumps({
                "type": "conversation_loaded",
                "data": {
                    "id": conv["id"],
                    "title": conv["title"],
                    "messages": conv.get("messages", []),
                    "context_tree": tree_data,
                    "function_trees": conv.get("function_trees", []),
                    "provider_info": _get_provider_info(conv_id),
                    "context_stats": conv.get("_last_context_stats"),
                },
            }, default=str))
            # If a task is currently running for this conversation, notify the client
            with _running_tasks_lock:
                task_info = _running_tasks.get(conv_id)
            if task_info:
                # Try to get current partial tree from the running function
                partial_tree = None
                loaded_ref = task_info.get("loaded_func_ref")
                if loaded_ref:
                    try:
                        ctx = _get_last_ctx(loaded_ref)
                        if ctx:
                            partial_tree = ctx._to_dict()
                            partial_tree["_in_progress"] = True
                    except Exception:
                        pass
                await ws.send_text(json.dumps({
                    "type": "running_task",
                    "data": {
                        "conv_id": conv_id,
                        "msg_id": task_info["msg_id"],
                        "func_name": task_info["func_name"],
                        "started_at": task_info["started_at"],
                        "display_params": task_info.get("display_params", ""),
                        "partial_tree": partial_tree,
                        "stream_events": task_info.get("stream_events", []),
                    },
                }, default=str))
        else:
            await ws.send_text(json.dumps({
                "type": "conversation_loaded",
                "data": {
                    "id": conv_id,
                    "title": "New conversation",
                    "context_tree": {},
                    "provider_info": _get_provider_info(),
                },
            }, default=str))


    elif action == "follow_up_answer":
        # User answered a follow-up question from a running function
        fq_conv_id = cmd.get("conv_id", "")
        answer = cmd.get("answer", "")
        with _follow_up_lock:
            fq = _follow_up_queues.get(fq_conv_id)
        if fq is not None:
            fq.put(answer)

    elif action == "list_conversations":
        conv_list = []
        with _conversations_lock:
            for cid, conv in _conversations.items():
                runtime = conv.get("runtime")
                session_id = getattr(runtime, '_session_id', None) if runtime else None
                conv_list.append({
                    "id": cid,
                    "title": conv.get("title", "Untitled"),
                    "created_at": conv.get("created_at"),
                    "has_session": session_id is not None,
                })
        conv_list.sort(key=lambda c: c.get("created_at") or 0)
        await ws.send_text(json.dumps({
            "type": "conversations_list",
            "data": conv_list,
        }, default=str))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def create_app():
    """Create and return the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Agentic Visualizer", docs_url=None, redoc_url=None)

    # Mount static files (CSS, JS)
    from starlette.staticfiles import StaticFiles
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/css", StaticFiles(directory=os.path.join(static_dir, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(static_dir, "js")), name="js")
    app.mount("/images", StaticFiles(directory=os.path.join(static_dir, "images")), name="images")

    @app.on_event("startup")
    async def _capture_loop():
        global _loop
        _loop = asyncio.get_running_loop()

    @app.on_event("startup")
    async def _refresh_claude_registry_if_stale():
        """Background-refresh openprogram/providers/claude_models.json when it's
        more than 24h old. Non-blocking; failures are logged and swallowed so
        a flaky network never prevents the server from starting."""
        import threading
        try:
            from openprogram.legacy_providers.claude_models import is_stale
        except Exception:
            return
        if not is_stale(max_age_hours=24):
            return

        def _do_refresh():
            try:
                from openprogram.legacy_providers.claude_code import ClaudeCodeRuntime
                from openprogram.legacy_providers.claude_models import _refresh_impl
                rt = ClaudeCodeRuntime(model="sonnet")
                try:
                    _refresh_impl(rt)
                finally:
                    rt.close()
            except Exception as e:
                import sys
                print(f"[claude_models] refresh failed: {e}", file=sys.stderr)

        threading.Thread(target=_do_refresh, daemon=True).start()

    # Serve the HTML frontend
    @app.get("/", response_class=HTMLResponse)
    async def root():
        from starlette.responses import RedirectResponse
        return RedirectResponse(url="/new", status_code=302)

    _FAVICON_SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<text x="16" y="23" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" '
        'font-size="22" font-weight="800" text-anchor="middle">'
        '<tspan fill="#3886e5">{</tspan>'
        '<tspan fill="#ff6b6b">L</tspan>'
        '<tspan fill="#3886e5">}</tspan>'
        '</text></svg>'
    )

    @app.get("/favicon.ico")
    @app.get("/favicon.svg")
    async def favicon():
        from starlette.responses import Response
        return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

    def _inject_sidebar(html: str) -> str:
        """Replace <!-- SIDEBAR --> placeholder with shared sidebar HTML."""
        if "<!-- SIDEBAR -->" not in html:
            return html
        sidebar_path = os.path.join(os.path.dirname(__file__), "static", "_sidebar.html")
        try:
            with open(sidebar_path) as f:
                sidebar_html = f.read()
        except FileNotFoundError:
            return html
        return html.replace("<!-- SIDEBAR -->", sidebar_html)

    @app.get("/new", response_class=HTMLResponse)
    async def index():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        with open(html_path) as f:
            content = _inject_sidebar(f.read())
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/config", response_class=HTMLResponse)
    async def config_page():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "config.html")
        with open(html_path) as f:
            content = f.read()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/c/{conv_id}", response_class=HTMLResponse)
    async def conversation_page(conv_id: str):
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        with open(html_path) as f:
            content = _inject_sidebar(f.read())
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "settings.html")
        with open(html_path) as f:
            content = _inject_sidebar(f.read())
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/programs", response_class=HTMLResponse)
    async def programs_page():
        from starlette.responses import Response
        html_path = os.path.join(os.path.dirname(__file__), "static", "programs.html")
        with open(html_path) as f:
            content = _inject_sidebar(f.read())
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # WebSocket — use Starlette's raw WebSocketRoute to avoid FastAPI routing issues
    from starlette.routing import WebSocketRoute
    app.routes.insert(0, WebSocketRoute("/ws", _websocket_handler))

    # REST endpoints
    @app.get("/api/tree")
    async def get_tree():
        return JSONResponse(content=_get_full_tree())

    @app.get("/api/functions")
    async def get_functions():
        return JSONResponse(content=_discover_functions())

    @app.get("/api/programs/meta")
    async def get_programs_meta():
        meta_path = os.path.join(os.path.dirname(__file__), "programs_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                return JSONResponse(content=json.load(f))
        return JSONResponse(content={"favorites": [], "folders": {}})

    @app.post("/api/programs/meta")
    async def save_programs_meta(body: dict = None):
        meta_path = os.path.join(os.path.dirname(__file__), "programs_meta.json")
        with open(meta_path, "w") as f:
            json.dump(body, f, indent=2)
        return JSONResponse(content={"ok": True})

    @app.post("/api/chat")
    async def post_chat(body: dict = None):
        """Handle chat message via REST (alternative to WebSocket)."""
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        text = body.get("text", "").strip()
        conv_id = body.get("conv_id")
        if not text:
            return JSONResponse(content={"error": "empty message"}, status_code=400)

        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        if not conv["messages"]:
            conv["title"] = text[:50]

        parsed = _parse_chat_input(text)
        user_msg = {
            "role": "user",
            "id": msg_id,
            "content": text,
            "timestamp": time.time(),
        }
        if parsed["action"] == "run":
            user_msg["display"] = "runtime"
        conv["messages"].append(user_msg)

        if parsed["action"] == "run":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"]},
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
            threading.Thread(
                target=_execute_in_context,
                args=(conv_id, msg_id, "query"),
                kwargs={"query": parsed["raw"]},
                daemon=True,
            ).start()

        return JSONResponse(content={"conv_id": conv_id, "msg_id": msg_id})

    @app.post("/api/run/{function_name}")
    async def run_function(function_name: str, body: dict = None):
        """Directly run a specific function.

        The `work_dir` field in body is a runtime-level setting (not a function
        argument) — it becomes the cwd for the exec runtime's subprocess. It is
        required and validated server-side as a defense in depth (the UI also
        disables Run when empty).
        """
        kwargs = body or {}
        conv_id = kwargs.pop("_conv_id", None)
        work_dir = kwargs.pop("work_dir", None)
        if not work_dir or not str(work_dir).strip():
            return JSONResponse(
                content={"error": "work_dir is required"},
                status_code=400,
            )
        kwargs["_work_dir"] = work_dir
        conv = _get_or_create_conversation(conv_id)
        conv_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        threading.Thread(
            target=_execute_in_context,
            args=(conv_id, msg_id, "run"),
            kwargs={"func_name": function_name, "kwargs": kwargs},
            daemon=True,
        ).start()

        return JSONResponse(content={"conv_id": conv_id, "msg_id": msg_id})

    @app.post("/api/pick-folder")
    async def pick_folder(body: dict = None):
        """Open the OS-native folder chooser and return the selected path.

        macOS: AppleScript's `choose folder` dialog. User cancel → 200 with
        path=null. We run it here because the webui is local — the dialog
        pops up on the same machine as the browser.
        """
        import subprocess
        import pathlib
        if sys.platform != "darwin":
            return JSONResponse(
                content={"error": "native folder picker only supported on macOS"},
                status_code=501,
            )
        start = (body or {}).get("start") or str(pathlib.Path.home())
        start = os.path.abspath(os.path.expanduser(start))
        if not os.path.isdir(start):
            start = str(pathlib.Path.home())
        script = (
            f'POSIX path of (choose folder with prompt '
            f'"Select working directory" default location '
            f'POSIX file "{start}")'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=600,
            )
        except Exception as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=500)
        if result.returncode != 0:
            # Most common non-zero: user cancelled.
            return JSONResponse(content={"path": None})
        path = result.stdout.strip().rstrip("/")
        return JSONResponse(content={"path": path or None})

    @app.get("/api/browse")
    async def browse_directory(path: str = None):
        """List subdirectories of a path for the workdir picker.

        Defaults to the user's home when path is absent or unreadable. Only
        returns directories (not files) — the picker is for choosing a folder.
        """
        import pathlib
        home = str(pathlib.Path.home())
        target = path or home
        target = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(target):
            target = home
        try:
            entries = sorted(os.listdir(target))
        except PermissionError:
            return JSONResponse(
                content={"error": f"Permission denied: {target}"},
                status_code=403,
            )
        subdirs = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if os.path.isdir(full):
                subdirs.append({"name": name, "path": full})
        parent = os.path.dirname(target) if target != "/" else None
        return JSONResponse(content={
            "path": target,
            "parent": parent if parent and parent != target else None,
            "subdirs": subdirs,
            "home": home,
        })

    @app.get("/api/workdir/defaults")
    async def workdir_defaults(conv_id: str = None, function_name: str = None):
        """Return suggested workdir values for the UI to prefill.

        - `last` — the workdir this conversation last used for this function
        - `repo` — OpenProgram repo root (handy shortcut for meta functions
                   like edit/create/improve that operate on the framework)
        - `home` — user's home directory (picker starting point)
        """
        import pathlib
        repo_root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", ".."
        ))
        last = None
        if conv_id and function_name:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
                if conv:
                    last = conv.get("last_workdirs", {}).get(function_name)
        return JSONResponse(content={
            "last": last,
            "repo": repo_root,
            "home": str(pathlib.Path.home()),
        })

    @app.get("/api/history")
    async def get_history():
        with _conversations_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"],
                 "messages": c.get("messages", []),
                 "message_count": len(c.get("messages", []))}
                for c in sorted(_conversations.values(), key=lambda c: c["created_at"], reverse=True)
            ]
        return JSONResponse(content=history)

    @app.post("/api/history")
    async def save_history(body: dict = None):
        if body and "conv_id" in body:
            conv_id = body["conv_id"]
            with _conversations_lock:
                if conv_id in _conversations:
                    return JSONResponse(content={"saved": True})
        return JSONResponse(content={"saved": False})

    @app.post("/api/pause")
    async def api_pause():
        pause_execution()
        _broadcast(json.dumps({"type": "status", "paused": True}))
        return JSONResponse(content={"paused": True})

    @app.post("/api/resume")
    async def api_resume():
        resume_execution()
        _broadcast(json.dumps({"type": "status", "paused": False}))
        return JSONResponse(content={"paused": False})

    @app.post("/api/stop")
    async def api_stop(body: dict = None):
        """Stop the currently running task for a conversation.

        Flow: mark cancel flag → resume (in case paused) → kill exec subprocess
        → unblock any pending ask_user queue. The exception path in
        _execute_in_context detects the cancel flag and marks running tree
        nodes as cancelled, then broadcasts the final tree.
        """
        conv_id = (body or {}).get("conv_id")
        if not conv_id:
            return JSONResponse(
                content={"stopped": False, "error": "missing conv_id"},
                status_code=400,
            )
        _mark_cancelled(conv_id)
        resume_execution()
        _kill_active_runtime(conv_id)
        with _follow_up_lock:
            q = _follow_up_queues.get(conv_id)
        if q is not None:
            try:
                q.put_nowait({"_cancelled": True})
            except Exception:
                pass
        _broadcast(json.dumps({
            "type": "status",
            "paused": False,
            "stopped": True,
            "conv_id": conv_id,
        }))
        return JSONResponse(content={"stopped": True})

    @app.get("/api/providers")
    async def get_providers():
        return JSONResponse(content=_list_providers())

    # --- Model catalog (LobeChat-style settings) ---------------------------

    @app.get("/api/providers/list")
    async def api_providers_list():
        """Unified provider catalog (registry + CLI) with enable / configure
        status and model counts. Feeds the settings page middle column."""
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={"providers": _mc.list_providers()})

    @app.get("/api/providers/{name}/models")
    async def api_provider_models(name: str):
        """All models registered under this provider + their enabled flag."""
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={
            "provider": name,
            "models": _mc.list_models_for_provider(name),
        })

    @app.post("/api/providers/{name}/toggle")
    async def api_toggle_provider(name: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        enabled = bool((body or {}).get("enabled", False))
        return JSONResponse(content=_mc.toggle_provider(name, enabled))

    @app.post("/api/providers/{name}/models/{model_id:path}/toggle")
    async def api_toggle_model(name: str, model_id: str, body: dict = None):
        from openprogram.webui import _model_catalog as _mc
        enabled = bool((body or {}).get("enabled", False))
        return JSONResponse(content=_mc.toggle_model(name, model_id, enabled))

    @app.get("/api/models/enabled")
    async def api_enabled_models():
        """Flat list of every enabled model across enabled providers.
        Used by the chat page model picker."""
        from openprogram.webui import _model_catalog as _mc
        return JSONResponse(content={"models": _mc.list_enabled_models()})

    @app.get("/api/providers/{name}/configure")
    async def get_provider_configure(name: str):
        """Return the configuration schema (label + step metadata) for a provider."""
        from openprogram.legacy_providers import configuration as _cfg
        entry = _cfg.get_provider(name)
        if entry is None:
            return JSONResponse(
                content={"error": f"No configuration for provider {name!r}"},
                status_code=404,
            )
        return JSONResponse(content={
            "provider": name,
            "label": entry["label"],
            "type": entry["type"],
            "description": entry.get("description", ""),
            "steps": [{"id": s["id"], "label": s["label"]} for s in entry["steps"]],
        })

    @app.post("/api/providers/{name}/configure/step/{step_id}")
    async def run_configure_step(name: str, step_id: str, body: dict = None):
        """Execute one configuration step. Body is the step context (accumulates state)."""
        from openprogram.legacy_providers import configuration as _cfg
        ctx = dict(body or {})
        result = _cfg.run_step(name, step_id, ctx)
        # Return both the result and the updated ctx so the client can keep state
        return JSONResponse(content={"result": result, "context": ctx})

    @app.post("/api/provider/{name}")
    async def switch_provider(name: str, body: dict = None):
        conv_id = body.get("conv_id") if body else None
        # Check if already active for this conversation
        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv and conv.get("provider_name") == name:
                return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        elif name == _runtime_management._default_provider:
            return JSONResponse(content={"switched": False, "already_active": True, "provider": name})
        try:
            _switch_runtime(name, conv_id=conv_id)
            return JSONResponse(content={"switched": True, "provider": name})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)

    @app.get("/api/models")
    async def list_models():
        """List available models for the current provider."""
        # Ensure runtime is initialized
        with _runtime_management._runtime_lock:
            if _runtime_management._default_provider is None:
                _runtime_management._default_provider, _runtime_management._default_runtime = _detect_default_provider()

        provider = _runtime_management._default_provider or "none"
        runtime = _runtime_management._default_runtime
        current_model = runtime.model if runtime else None

        # Auto-detect models from the runtime
        model_list = []
        if runtime and hasattr(runtime, 'list_models'):
            try:
                model_list = runtime.list_models()
            except Exception as e:
                print(f"[list_models] {provider} error: {e}")
        # Ensure current model is in the list
        if current_model and current_model not in model_list:
            model_list = [current_model] + model_list

        return JSONResponse(content={
            "provider": provider,
            "current": current_model,
            "models": model_list,
        })

    @app.post("/api/model")
    async def switch_model(body: dict = None):
        """Switch model (and optionally provider) for the active runtime.

        Body:
          {
            "model":    str,  # either bare id ("gpt-4o") or "provider:id"
            "provider": str,  # optional explicit provider
            "conv_id":  str,  # optional; target a specific conversation
          }

        If "provider" is given (or inferred from "provider:id" model string)
        and differs from the conversation's current provider, we spin up a
        new runtime for that provider. Otherwise we just rebind the model
        on the existing runtime.
        """
        if not body or "model" not in body:
            return JSONResponse(content={"error": "Missing model"}, status_code=400)
        model = body["model"].strip()
        explicit_provider = (body.get("provider") or "").strip() or None
        conv_id = body.get("conv_id")

        # "provider:id" syntax → split
        inferred_provider = None
        bare_model = model
        if explicit_provider is None and ":" in model:
            head, tail = model.split(":", 1)
            # Only treat as provider prefix if head matches a known provider id.
            from openprogram.providers import get_providers as _get_providers
            known = set(_get_providers())
            known.update({"claude-code", "openai-codex", "gemini-cli",
                          "anthropic", "openai", "gemini", "openclaw"})
            if head in known:
                inferred_provider = head
                bare_model = tail
        target_provider = explicit_provider or inferred_provider

        def _apply_to_conv(conv):
            old_rt = conv.get("runtime")
            cur_prov = conv.get("provider_name", _runtime_management._default_provider)
            prov = target_provider or cur_prov
            need_new_rt = (target_provider and target_provider != cur_prov) or (old_rt is None)
            if need_new_rt:
                if old_rt and hasattr(old_rt, "close"):
                    try: old_rt.close()
                    except Exception: pass
                new_rt = _create_runtime_for_visualizer(prov, model=bare_model)
                conv["runtime"] = new_rt
                conv["provider_name"] = prov
            else:
                old_rt.model = bare_model
            return prov

        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv:
                prov = _apply_to_conv(conv)
                info = _get_provider_info(conv_id)
                _broadcast(json.dumps({"type": "provider_changed", "data": info}))
                return JSONResponse(content={"switched": True, "provider": prov, "model": bare_model})

        # Default runtime path (no conv_id).
        if target_provider and target_provider != _runtime_management._default_provider:
            if _runtime_management._default_runtime and hasattr(_runtime_management._default_runtime, "close"):
                try: _runtime_management._default_runtime.close()
                except Exception: pass
            _runtime_management._default_runtime = _create_runtime_for_visualizer(target_provider, model=bare_model)
            _runtime_management._default_provider = target_provider
        elif _runtime_management._default_runtime:
            _runtime_management._default_runtime.model = bare_model
        else:
            return JSONResponse(content={"error": "No active runtime"}, status_code=400)

        info = _get_provider_info()
        _broadcast(json.dumps({"type": "provider_changed", "data": info}))
        return JSONResponse(content={
            "switched": True,
            "provider": target_provider or _runtime_management._default_provider,
            "model": bare_model,
        })

    @app.get("/api/config")
    async def get_config():
        """Get current API key configuration (masked)."""
        config = _load_config()
        keys = config.get("api_keys", {})
        # Mask values: show first 8 chars + "..."
        masked = {k: (v[:8] + "..." if len(v) > 8 else "***") for k, v in keys.items() if v}
        return JSONResponse(content={"api_keys": masked})

    @app.post("/api/config")
    async def save_config(body: dict = None):
        """Save pre-verified API keys to config file and apply to environment."""
        if not body or "api_keys" not in body:
            return JSONResponse(content={"error": "Missing api_keys"}, status_code=400)
        config = _load_config()
        if "api_keys" not in config:
            config["api_keys"] = {}
        for key, val in body["api_keys"].items():
            val = val.strip()
            if val:
                config["api_keys"][key] = val
                os.environ[key] = val
            else:
                config["api_keys"].pop(key, None)
                os.environ.pop(key, None)
        _save_config(config)
        return JSONResponse(content={"saved": True})

    # --- Agent settings (chat + exec) ---

    @app.get("/api/agent_settings")
    async def get_agent_settings(conv_id: str = None):
        """Get current chat and exec agent provider/model settings.

        If conv_id is provided, returns lock state and session_id for that
        specific conversation. Otherwise returns unlocked defaults.
        """
        _init_providers()

        chat_session_id = None
        chat_locked = False
        chat_provider = _runtime_management._chat_provider
        chat_model = _runtime_management._chat_model

        if conv_id:
            with _conversations_lock:
                conv = _conversations.get(conv_id)
            if conv:
                # Locked if conversation has messages
                if conv.get("messages") and len(conv["messages"]) > 0:
                    chat_locked = True
                # Get session_id from conversation runtime
                rt = conv.get("runtime")
                if rt:
                    chat_session_id = getattr(rt, '_session_id', None)
                # Use conversation's provider/model if set
                if conv.get("provider_name"):
                    chat_provider = conv["provider_name"]
                if rt and getattr(rt, 'model', None):
                    chat_model = rt.model

        return JSONResponse(content={
            "chat": {
                "provider": chat_provider,
                "model": chat_model,
                "session_id": chat_session_id,
                "locked": chat_locked,
                "thinking": _get_thinking_config(chat_provider),
            },
            "exec": {
                "provider": _runtime_management._exec_provider,
                "model": _runtime_management._exec_model,
                "thinking": _get_thinking_config(_runtime_management._exec_provider),
            },
            "available": _runtime_management._available_providers,
        })

    @app.post("/api/agent_settings")
    async def set_agent_settings(body: dict = None):
        """Update chat and/or exec agent provider/model."""
        _init_providers()

        changed = False

        if body and "chat" in body:
            chat = body["chat"]
            new_provider = chat.get("provider", _runtime_management._chat_provider)
            new_model = chat.get("model", _runtime_management._chat_model)
            if new_provider != _runtime_management._chat_provider or new_model != _runtime_management._chat_model:
                _runtime_management._chat_provider = new_provider
                _runtime_management._chat_model = new_model
                # Update all existing conversation runtimes
                with _conversations_lock:
                    for conv in _conversations.values():
                        old_rt = conv.get("runtime")
                        if old_rt and hasattr(old_rt, 'close'):
                            old_rt.close()
                        new_rt = _create_runtime_for_visualizer(
                            _runtime_management._chat_provider,
                            model=_runtime_management._chat_model,
                        )
                        conv["runtime"] = new_rt
                        conv["provider_name"] = _runtime_management._chat_provider
                changed = True

        if body and "exec" in body:
            exec_cfg = body["exec"]
            _runtime_management._exec_provider = exec_cfg.get("provider", _runtime_management._exec_provider)
            _runtime_management._exec_model = exec_cfg.get("model", _runtime_management._exec_model)
            changed = True

        if changed:
            _broadcast(json.dumps({
                "type": "agent_settings_changed",
                "data": {
                    "chat": {"provider": _runtime_management._chat_provider, "model": _runtime_management._chat_model},
                    "exec": {"provider": _runtime_management._exec_provider, "model": _runtime_management._exec_model},
                },
            }))

        return JSONResponse(content={
            "chat": {"provider": _runtime_management._chat_provider, "model": _runtime_management._chat_model},
            "exec": {"provider": _runtime_management._exec_provider, "model": _runtime_management._exec_model},
        })


    def _validate_api_key(env_var: str, value: str) -> str | None:
        """Validate an API key by making a lightweight test call. Returns error string or None."""
        try:
            if env_var == "OPENAI_API_KEY":
                import openai
                client = openai.OpenAI(api_key=value)
                client.models.list()
                return None
            elif env_var == "ANTHROPIC_API_KEY":
                import anthropic
                client = anthropic.Anthropic(api_key=value)
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
                return None
            elif env_var in ("GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
                import google.generativeai as genai
                genai.configure(api_key=value)
                # Try models in order until one works
                for m in ("gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"):
                    try:
                        model = genai.GenerativeModel(m)
                        model.generate_content("hi", generation_config={"max_output_tokens": 1})
                        return None
                    except Exception:
                        continue
                # Last resort: list models to verify key is valid
                list(genai.list_models())
                return None
            else:
                return None  # Unknown key type, skip validation
        except Exception as e:
            return str(e)

    @app.post("/api/config/verify")
    async def verify_key(body: dict = None):
        """Verify a single API key without saving."""
        if not body or "env" not in body:
            return JSONResponse(content={"error": "Missing env"}, status_code=400)
        value = body.get("value", "")
        # If masked value, use the stored real key
        if not value or value.endswith("..."):
            config = _load_config()
            value = config.get("api_keys", {}).get(body["env"], "")
        if not value:
            return JSONResponse(content={"valid": False, "error": "No key provided"})
        error = _validate_api_key(body["env"], value)
        return JSONResponse(content={"valid": error is None, "error": error})

    @app.get("/api/node/{path:path}")
    async def get_node(path: str):
        trees = _get_full_tree()
        for tree in trees:
            node = _find_node_by_path(tree, path)
            if node is not None:
                return JSONResponse(content=node)
        return JSONResponse(content={"error": "not found"}, status_code=404)

    # --- Function source code and meta-function operations ---

    @app.get("/api/function/{name}/source")
    async def get_function_source(name: str):
        """Return full source code of a function."""
        base = os.path.dirname(os.path.dirname(__file__))
        for rel_subdir, category in (
            (("programs", "functions", "meta"), "meta"),
            (("programs", "functions", "buildin"), "builtin"),
            (("programs", "functions", "third_party"), "external"),
        ):
            filepath = os.path.join(base, *rel_subdir, f"{name}.py")
            if os.path.isfile(filepath):
                with open(filepath) as f:
                    source = f.read()
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": filepath,
                    "category": category,
                })
        # Search subdirectory projects (app category)
        fn_dir = os.path.join(base, "programs", "applications")
        if os.path.isdir(fn_dir):
            for d in os.listdir(fn_dir):
                full_path = os.path.join(fn_dir, d)
                if os.path.isdir(full_path) and not d.startswith("_"):
                    for root, dirs, files in os.walk(full_path):
                        dirs[:] = [x for x in dirs if not x.startswith(("_", "."))]
                        if "main.py" in files:
                            main_py = os.path.join(root, "main.py")
                            with open(main_py) as f:
                                source = f.read()
                            info = _extract_function_info(main_py, None, "app")
                            if info and info["name"] == name:
                                return JSONResponse(content={
                                    "name": name,
                                    "source": source,
                                    "filepath": main_py,
                                    "category": "app",
                                })
                            break
        # Fallback: try to find as an internal function via inspect
        fn = _load_function(name)
        if fn is None:
            # Check server-module globals (e.g. _chat_query)
            fn = globals().get(name)
        if fn is not None and callable(fn):
            try:
                inner = getattr(fn, '__wrapped__', None) or getattr(fn, '_fn', None) or fn
                source = inspect.getsource(inner)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(inner),
                    "category": "internal",
                })
            except (OSError, TypeError):
                pass
        # Fallback: check the @agentic_function global registry
        from openprogram.agentic_programming.function import _registry
        if name in _registry:
            reg_fn = _registry[name]._fn
            try:
                source = inspect.getsource(reg_fn)
                return JSONResponse(content={
                    "name": name,
                    "source": source,
                    "filepath": inspect.getfile(reg_fn),
                    "category": "external",
                })
            except (OSError, TypeError):
                pass

        # Fallback: grep for the function definition in app project directories.
        # This handles external projects loaded via symlinks in openprogram/programs/applications/.
        import re
        apps_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "programs", "applications")
        func_pattern = re.compile(rf'def\s+{re.escape(name)}\s*\(')
        if os.path.isdir(apps_dir):
            for root, dirs, files in os.walk(apps_dir, followlinks=True):
                dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))
                           and d not in {'node_modules', 'vendor', '__pycache__',
                                         'desktop_env', 'libs', 'build', 'dist',
                                         'benchmarks', 'docs', 'tests', 'memory',
                                         'cache', 'skills', 'actions', 'platforms'}]
                for f in files:
                    if not f.endswith('.py'):
                        continue
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath) as fh:
                            source = fh.read()
                        if func_pattern.search(source):
                            return JSONResponse(content={
                                "name": name,
                                "source": source,
                                "filepath": filepath,
                                "category": "external",
                            })
                    except (OSError, UnicodeDecodeError):
                        continue

        return JSONResponse(content={"error": f"Function '{name}' not found"}, status_code=404)

    @app.post("/api/function/{name}/edit")
    async def edit_function_source(name: str, body: dict = None):
        """Save edited source code for a function."""
        if not body or "source" not in body:
            return JSONResponse(content={"error": "no source provided"}, status_code=400)
        base = os.path.dirname(os.path.dirname(__file__))
        filepath = os.path.join(base, "programs", "functions", "third_party", f"{name}.py")
        # Only allow editing user/builtin functions, not meta functions
        if not os.path.isfile(filepath):
            # Create new file
            pass
        try:
            # Validate syntax
            compile(body["source"], filepath, "exec")
        except SyntaxError as e:
            return JSONResponse(content={"error": f"Syntax error: {e}"}, status_code=400)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(body["source"])
        # Reload the module
        mod_name = f"openprogram.programs.functions.third_party.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"saved": True, "filepath": filepath})

    @app.post("/api/function/{name}/edit")
    async def edit_function(name: str, body: dict = None):
        """Run meta edit() on a function."""
        instruction = (body or {}).get("instruction", "")
        conv_id = (body or {}).get("conv_id")
        conv = _get_or_create_conversation(conv_id)
        msg_id = str(uuid.uuid4())[:8]

        def _do_edit():
            try:
                from openprogram.programs.functions.meta import edit
                from openprogram.legacy_providers import create_runtime
                mod = importlib.import_module(f"openprogram.programs.functions.{name}")
                fn = getattr(mod, name)
                runtime = create_runtime()
                edited = edit(fn=fn, runtime=runtime, instruction=instruction or None)
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": f"Edited function '{name}' successfully.",
                })
            except Exception as e:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Edit failed: {e}",
                })

        threading.Thread(target=_do_edit, daemon=True).start()
        return JSONResponse(content={"conv_id": conv["id"], "msg_id": msg_id})

    @app.delete("/api/function/{name}")
    async def delete_function(name: str):
        """Delete a user function file."""
        base = os.path.dirname(os.path.dirname(__file__))
        filepath = os.path.join(base, "programs", "functions", "third_party", f"{name}.py")
        if not os.path.isfile(filepath):
            return JSONResponse(content={"error": "not found"}, status_code=404)
        # Don't allow deleting built-in functions
        builtin_names = ["general_action", "agent_loop", "wait", "deep_work", "_utils"]
        if name in builtin_names:
            return JSONResponse(content={"error": "cannot delete built-in function"}, status_code=403)
        os.remove(filepath)
        mod_name = f"openprogram.programs.functions.third_party.{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return JSONResponse(content={"deleted": True})

    @app.post("/api/function/create")
    async def create_function(body: dict = None):
        """Create a new function from description."""
        if not body or "description" not in body:
            return JSONResponse(content={"error": "no description"}, status_code=400)
        conv_id = body.get("conv_id")
        conv = _get_or_create_conversation(conv_id)
        msg_id = str(uuid.uuid4())[:8]
        name = body.get("name", "new_func")
        desc = body["description"]

        def _do_create():
            try:
                from openprogram.programs.functions.meta import create
                runtime = _get_runtime()
                fn = create(description=desc, runtime=runtime, name=name)
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "result",
                    "content": f"Created function '{name}' successfully.",
                })
                # Refresh functions list
                functions = _discover_functions()
                _broadcast(json.dumps({"type": "functions_list", "data": functions}, default=str))
            except Exception as e:
                _broadcast_chat_response(conv_id, msg_id, {
                    "type": "error",
                    "content": f"Create failed: {e}",
                })

        threading.Thread(target=_do_create, daemon=True).start()
        return JSONResponse(content={"conv_id": conv["id"], "msg_id": msg_id})

    @app.post("/api/register")
    async def register_external(body: dict = None):
        """Register an external module's functions (for GUI/Research Agent Harness integration)."""
        if not body or "module" not in body:
            return JSONResponse(content={"error": "no module path"}, status_code=400)
        module_path = body["module"]
        try:
            mod = importlib.import_module(module_path)
            # Scan for @agentic_function decorated callables
            registered = []
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if callable(obj) and hasattr(obj, '_fn'):
                    registered.append(attr_name)
            return JSONResponse(content={
                "registered": True,
                "module": module_path,
                "functions": registered,
            })
        except ImportError as e:
            return JSONResponse(content={"error": f"Cannot import: {e}"}, status_code=400)

    return app


# ---------------------------------------------------------------------------
# Server runner (in background thread)
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start_server(port: int = 8765, open_browser: bool = True) -> threading.Thread:
    """
    Start the visualization server in a background daemon thread.

    Returns the thread object. The server runs until the process exits.
    """
    global _server_thread, _loop

    if _server_thread is not None and _server_thread.is_alive():
        print(f"Visualizer already running")
        return _server_thread

    # Restore saved sessions from disk
    _restore_sessions()

    # Register our event callback
    on_event(_on_context_event)

    def _run():
        global _loop
        try:
            import uvicorn
        except ImportError:
            raise ImportError(
                "uvicorn is required for the web UI. "
                "Install with: pip install openprogram[web]"
            )

        app = create_app()
        config = uvicorn.Config(
            app, host="0.0.0.0", port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(server.serve())

    _server_thread = threading.Thread(target=_run, daemon=True, name="openprogram-visualizer")
    _server_thread.start()

    url = f"http://localhost:{port}"
    print(f"Agentic Visualizer running at {url}")

    if open_browser:
        # Small delay to let the server start
        def _open():
            import time
            time.sleep(0.8)
            import webbrowser
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    return _server_thread


def stop_server():
    """Clean up event callbacks."""
    off_event(_on_context_event)
