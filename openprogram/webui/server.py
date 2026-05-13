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
    register_cancel_event as _register_cancel_event,
    unregister_cancel_event as _unregister_cancel_event,
    has_active_runtime as _has_active_runtime,
    mark_context_cancelled as _mark_context_cancelled,
    set_current_session_id as _set_current_session_id,
    reset_current_session_id as _reset_current_session_id,
)
from openprogram.agentic_programming.function import CancelledError as _CancelledError
from openprogram.webui.messages import get_store as _get_message_store
from openprogram.webui._stream_bridge import StreamBridge


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_root_contexts: list[dict] = []
_root_contexts_lock = threading.Lock()
_ws_connections: list[Any] = []
_ws_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Module load timestamp — used by /healthz uptime calc.
_SERVER_START_TIME = time.time()

# Max session rows sent to the CLI Welcome panel. Catalog data such as
# tools, providers, functions, skills, agents, and channels is sent in full;
# the TUI decides how many rows fit for the current terminal size.
WELCOME_STATS_SESSION_LIMIT = 48

# Conversation storage (in-memory). The conv dict still owns
# runtime / root_context / function_trees / metadata, but the
# ``messages`` array is now a derived view of SessionDB's active
# branch — see _get_messages / _invalidate_messages below.
_sessions: dict[str, dict] = {}

# Last (provider, model) the user picked from the chat ModelBadge
# without an attached session — i.e. they picked a model on the
# welcome screen / before opening a chat. Captured globally so the
# next freshly-created conversation can inherit the choice.
# ``None`` until the user has picked at least once.
_user_pinned_provider: Optional[str] = None
_user_pinned_model: Optional[str] = None
_sessions_lock = threading.Lock()

# Active-branch message cache (session_id → list[dict]). Populated on
# demand by _get_messages, invalidated whenever advance_head /
# set_head / a fresh dispatcher turn writes to SessionDB.
#
# Why a cache: WS bootstrap + every chat-history broadcast reads the
# branch list multiple times. With a thousand-message session,
# walking the parent_id CTE every time costs ~5ms; cached it's free.
# Why bounded LRU: webui keeps tens to hundreds of conversations
# warm; a single un-bounded dict would creep into RAM. 64 sessions
# × ~1MB serialized chat = ~64MB — comfortable on any modern host.
import collections as _collections   # noqa: E402

_msg_cache_lock = threading.Lock()
_MSG_CACHE_CAP = 64
_msg_cache: "_collections.OrderedDict[str, list[dict]]" = _collections.OrderedDict()


def _get_messages(session_id: str) -> list[dict]:
    """Return the active-branch messages for a conversation.

    Reads from cache when warm, falls back to SessionDB.get_branch on
    miss. The cache contains COPIES — callers that mutate the list
    won't accidentally invalidate the cache, but they must call
    _invalidate_messages(session_id) afterwards if they wrote anything
    that should be visible.

    Returns ``[]`` for unknown session_ids — same as the dict-based
    reader's behavior, so existing call sites don't need null-guards.
    """
    with _msg_cache_lock:
        if session_id in _msg_cache:
            _msg_cache.move_to_end(session_id)
            return list(_msg_cache[session_id])
    # Cache miss — load from DB. Out of the lock so concurrent
    # different-conv reads don't serialize.
    try:
        from openprogram.agent.session_db import default_db
        msgs = default_db().get_branch(session_id)
    except Exception:
        msgs = []
    with _msg_cache_lock:
        _msg_cache[session_id] = msgs
        _msg_cache.move_to_end(session_id)
        while len(_msg_cache) > _MSG_CACHE_CAP:
            _msg_cache.popitem(last=False)
        return list(msgs)


def _invalidate_messages(session_id: str) -> None:
    """Drop ``session_id``'s cached branch list. Call after any write
    that should be visible to the next reader: append_message,
    set_head, retry/edit, deepest_leaf jumps."""
    with _msg_cache_lock:
        _msg_cache.pop(session_id, None)


def _hydrate_messages_from_db(session_id: str) -> list[dict]:
    """Force-refresh and return the active branch. Used by paths that
    just wrote to SessionDB and need the next read to be fresh."""
    _invalidate_messages(session_id)
    return _get_messages(session_id)


def _set_active_head(session_id: str, head_id: Optional[str]) -> None:
    """Switch the conversation's active branch leaf.

    Used by retry / edit / sibling-checkout / deepest-leaf jump UIs.
    Updates SessionDB.sessions.head_id (so cross-process readers and
    the dispatcher's next get_branch see the new head) and the
    in-memory ``conv["head_id"]`` mirror, then invalidates the
    messages cache so the next reader walks the new branch.
    """
    try:
        from openprogram.agent.session_db import default_db
        default_db().set_head(session_id, head_id)
    except Exception as e:
        _log(f"_set_active_head: SessionDB write failed for {session_id}: {e}")
    with _sessions_lock:
        conv = _sessions.get(session_id)
        if conv is not None:
            conv["head_id"] = head_id
    _invalidate_messages(session_id)


def _deepest_leaf_db(session_id: str, root_id: str) -> Optional[str]:
    """SessionDB-backed deepest_leaf — finds the tip of the subtree
    under ``root_id`` so sibling-checkout lands on the latest reply,
    not the fork point. Mirrors openprogram.contextgit.deepest_leaf
    but reads from SQL instead of an in-memory message list."""
    try:
        from openprogram.agent.session_db import default_db
        return default_db().get_deepest_leaf(session_id, root_id)
    except Exception:
        return None

# Global default providers (used when creating new conversations)
# (Provider state moved to openprogram.webui._runtime_management)

# Follow-up answer queues — keyed by conversation ID. When a function calls
# ask_user(), the handler puts the question on WebSocket and blocks on this
# queue. The frontend sends the answer back via WebSocket.
_follow_up_queues: dict = {}
_follow_up_lock = threading.Lock()

# Track running tasks so refresh can recover them
_running_tasks: dict = {}  # session_id → {msg_id, func_name, started_at, ...}
_running_tasks_lock = threading.Lock()



# ---------------------------------------------------------------------------
# Follow-up context manager — shared by run / edit / any command handler
# ---------------------------------------------------------------------------
from contextlib import contextmanager as _contextmanager


@_contextmanager
def _web_follow_up(session_id: str, msg_id: str, func_name: str, tree_cb=None):
    """Set up follow-up question support for a web UI command execution.

    Registers a global ask_user handler that sends follow-up questions to
    the browser via WebSocket and blocks until the user answers.

    Args:
        session_id:   Conversation ID (for routing the answer back).
        msg_id:    Message ID (for associating with the right chat message).
        func_name: Function name (for display in the frontend).
        tree_cb:   Optional tree event callback to trigger on follow-up.
    """
    fq = queue.Queue()
    with _follow_up_lock:
        _follow_up_queues[session_id] = fq

    def _handler(question: str) -> str:
        _broadcast_chat_response(session_id, msg_id, {
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
            _follow_up_queues.pop(session_id, None)



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
    _get_session_runtime,
    _get_exec_runtime,
    _switch_runtime,
    _get_provider_info,
)



# Use the centralized path helper so --profile / OPENPROGRAM_PROFILE
# reroutes config reads. str() so the callers that pass it to open()
# get a plain path string.
from openprogram.paths import get_config_path as _get_config_path
def _CONFIG_PATH() -> str:  # noqa: N802  (keeping legacy name)
    return str(_get_config_path())

from openprogram.webui import persistence as _persist


def _save_session(session_id: str):
    """Persist one conversation's meta + messages under its agent.

    Per-function execution trees are written incrementally by
    append_tree_event in the tree event callback — we do not rewrite
    them here. An empty conversation (no messages yet, no session row
    in SessionDB) is skipped entirely so the user doesn't see "ghost"
    history rows for chats they never typed in.
    """
    if not session_id:
        return
    with _sessions_lock:
        conv = _sessions.get(session_id)
        if conv is None:
            return
        # Skip persistence for brand-new conversations the user hasn't
        # actually used. Once _append_msg lands the first message it
        # creates the session row, and from that point on this guard
        # passes (db.get_session is non-None) and we save normally.
        if not conv.get("messages"):
            try:
                from openprogram.agent.session_db import default_db
                if default_db().get_session(session_id) is None:
                    return
            except Exception:
                pass
        root_ctx = conv.get("root_context")
        runtime = conv.get("runtime")
        agent_id = conv.get("agent_id") or _default_agent_id()
        meta = {
            "id": session_id,
            "agent_id": agent_id,
            "title": conv.get("title", "Untitled"),
            "provider_name": conv.get("provider_name"),
            "provider_override": conv.get("provider_override"),
            "model_override": conv.get("model_override"),
            "session_id": getattr(runtime, "_session_id", None),
            "model": getattr(runtime, "model", None),
            "created_at": conv.get("created_at"),
            "context_tree": root_ctx._to_dict() if root_ctx is not None else None,
            "_chat_usage": conv.get("_chat_usage"),
            "_last_context_stats": conv.get("_last_context_stats"),
            "_titled": conv.get("_titled", False),
            "_last_exec_session": conv.get("_last_exec_session"),
            "_last_exec_cumulative_usage": conv.get("_last_exec_cumulative_usage"),
            "head_id": conv.get("head_id"),
            # Channel-bound sessions carry these from dispatch_inbound;
            # persist them so outbound routing still works after reload.
            "channel": conv.get("channel"),
            "account_id": conv.get("account_id"),
            "peer": conv.get("peer"),
            "peer_display": conv.get("peer_display"),
            "tools_enabled": conv.get("tools_enabled"),
            "tools_override": conv.get("tools_override"),
            "thinking_effort": conv.get("thinking_effort"),
            "permission_mode": conv.get("permission_mode"),
        }
        messages = list(conv.get("messages", []))
    try:
        _persist.save_meta(agent_id, session_id, meta)
        _persist.save_messages(agent_id, session_id, messages)
    except Exception as e:
        _log(f"[save_conversation] {session_id} error: {e}")


def _default_agent_id() -> str:
    """Which agent does a new conversation land in when the client
    didn't specify one? Falls back to the registry default."""
    try:
        from openprogram.agents import manager as _A
        spec = _A.get_default()
        if spec is not None:
            return spec.id
    except Exception:
        pass
    return "main"


def _delete_session_files(session_id: str):
    """Look up which agent owns this conv then delete its dir."""
    try:
        with _sessions_lock:
            conv = _sessions.get(session_id)
            agent_id = (conv or {}).get("agent_id") if conv else None
        if not agent_id:
            agent_id = _persist.resolve_agent_for_conv(session_id)
        if agent_id:
            _persist.delete_session(agent_id, session_id)
    except Exception as e:
        _log(f"[delete_session_files] {session_id} error: {e}")


def _restore_sessions():
    """Walk every agent's sessions dir and hydrate _sessions."""
    for agent_id, session_id in _persist.list_sessions():
        try:
            data = _persist.load_session(agent_id, session_id)
            if data is None:
                continue

            root_ctx = None
            ct = data.get("context_tree")
            if ct:
                root_ctx = Context.from_dict(ct)
                root_ctx.status = "idle"

            provider_name = data.get("provider_name")
            provider_override = data.get("provider_override")
            model_override = data.get("model_override")
            # The "session_id" inside meta is the LLM runtime's own
            # session identifier (Claude Code, etc.) — separate from
            # session_id in this loop, which is the SessionDB primary
            # key. Use a different local name to keep them apart.
            runtime_session_id = data.get("session_id") or data.get("llm_session_id")
            model = data.get("model")

            # Skip eager runtime restore unless this session was
            # explicitly switched (provider_override). Without an
            # override we can't tell whether the persisted
            # ``provider_name`` reflects a user choice or stale state
            # written by the old auto-default-on-create path; letting
            # ``_get_session_runtime`` build the runtime lazily from agent
            # config is the only way old buggy sessions escape the
            # legacy claude-code default.
            runtime = None
            if provider_override:
                try:
                    runtime = _create_runtime_for_visualizer(
                        provider_override, model=model_override or model
                    )
                    if runtime_session_id and hasattr(runtime, "_session_id"):
                        runtime._session_id = runtime_session_id
                        runtime._turn_count = 1
                        runtime.has_session = True
                except Exception:
                    runtime = None

            # ContextGit migration: backfill parent_id on legacy
            # messages and pick a head_id. Old conversations become a
            # straight linear chain (see docs/design/contextgit.md).
            from openprogram.contextgit import (
                normalize_parent_pointers,
                head_or_tip,
            )
            msgs = data.get("messages", [])
            normalize_parent_pointers(msgs)
            head_id = data.get("head_id") or head_or_tip({}, msgs)

            with _sessions_lock:
                _sessions[session_id] = {
                    "id": session_id,
                    "agent_id": agent_id,
                    "title": data.get("title", "Untitled"),
                    "root_context": root_ctx,
                    "runtime": runtime,
                    "provider_name": provider_override or None,
                    "provider_override": provider_override,
                    "model_override": model_override,
                    "messages": msgs,
                    "function_trees": data.get("function_trees", []),
                    "created_at": data.get("created_at", time.time()),
                    "_titled": data.get("_titled", True),
                    "_chat_usage": data.get("_chat_usage"),
                    "_last_context_stats": data.get("_last_context_stats"),
                    "_last_exec_session": data.get("_last_exec_session"),
                    "_last_exec_cumulative_usage": data.get("_last_exec_cumulative_usage"),
                    "head_id": head_id,
                    "run_active": False,
                    "channel": data.get("channel"),
                    "account_id": data.get("account_id"),
                    "peer": data.get("peer"),
                    "peer_display": data.get("peer_display"),
                }
            _log(f"[restore] agent={agent_id} session={session_id}: "
                 f"{data.get('title')} (runtime_session={runtime_session_id})")
        except Exception as e:
            _log(f"[restore] failed for {session_id}: {e}")


def _load_config() -> dict:
    """Load config from ~/.agentic/config.json."""
    try:
        with open(_CONFIG_PATH()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(config: dict):
    """Save config to ~/.agentic/config.json."""
    os.makedirs(os.path.dirname(_CONFIG_PATH()), exist_ok=True)
    with open(_CONFIG_PATH(), "w") as f:
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
    import urllib.request, urllib.error
    def _proxy_alive() -> bool:
        # Default is :3456 (where claude-max-api-proxy listens) — NOT :8110
        # which is openprogram's own backend port and would always answer
        # 200, masking proxy failure as "available".
        url = os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456"
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=0.5):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            return False

    def _codex_available() -> bool:
        # The Codex provider needs OAuth credentials, NOT the `codex` CLI
        # binary itself. The binary is only used once for `codex login`,
        # after which OpenProgram reads ~/.codex/auth.json (or the
        # adopted copy at ~/.openprogram/auth/openai-codex/default.json)
        # and talks directly to chatgpt.com/backend-api — no proxy, no
        # shell-out to `codex`.
        import os as _os
        from pathlib import Path
        if (Path.home() / ".codex" / "auth.json").exists():
            return True
        if (Path.home() / ".openprogram" / "auth" / "openai-codex" /
                "default.json").exists():
            return True
        return False

    checks = [
        # (name, label, available_check, env_keys_for_config_or_None_if_CLI)
        ("openai-codex", "OpenAI Codex", _codex_available, None),
        ("gemini-cli", "Gemini CLI", lambda: shutil.which("gemini") is not None, None),
        ("anthropic", "Anthropic API", lambda: bool(_get_api_key("ANTHROPIC_API_KEY")), ["ANTHROPIC_API_KEY"]),
        ("openai", "OpenAI API", lambda: bool(_get_api_key("OPENAI_API_KEY")), ["OPENAI_API_KEY"]),
        ("gemini", "Gemini API", lambda: bool(_get_api_key("GOOGLE_API_KEY") or _get_api_key("GOOGLE_GENERATIVE_AI_API_KEY")), ["GOOGLE_API_KEY"]),
        ("claude-code", "Claude Code", _proxy_alive, ["CLAUDE_MAX_PROXY_URL"]),
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
        from openprogram.webui._pause_stop import _current_session_id as _cv
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


def _load_agent_session_meta(session_key: str) -> Optional[dict]:
    """Find a channel-bound agent session's meta.json by session_key.

    Walks every agent's sessions/ dir once. Returns the parsed meta
    dict (with channel/account_id/peer/etc.) or None if the session
    key isn't owned by any agent.
    """
    try:
        import json as _json
        from openprogram.agents import manager as _A
        from openprogram.agents.manager import sessions_dir
        for agent in _A.list_all():
            meta_p = sessions_dir(agent.id) / session_key / "meta.json"
            if meta_p.exists():
                try:
                    return _json.loads(meta_p.read_text(encoding="utf-8"))
                except Exception:
                    return None
    except Exception:
        return None
    return None


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


def _cleanup_session_resources(session_id: str, conv: dict):
    """Clean up all resources associated with a deleted conversation."""
    # Remove root_contexts entries — match by the conversation's root_context name
    root_ctx = conv.get("root_context")
    if root_ctx:
        root_name = getattr(root_ctx, 'name', None) or (root_ctx.get("name") if isinstance(root_ctx, dict) else None)
        if root_name:
            with _root_contexts_lock:
                _root_contexts[:] = [r for r in _root_contexts if r.get("name") != root_name]
    # Clean up follow-up queues and running tasks
    _follow_up_queues.pop(session_id, None)
    with _running_tasks_lock:
        _running_tasks.pop(session_id, None)


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

def _get_or_create_session(session_id: str = None,
                                agent_id: str = None,
                                *,
                                channel: str = None,
                                account_id: str = None,
                                peer: str = None) -> dict:
    """Get or create a conversation with its own Context tree + Runtime.

    If ``agent_id`` is provided the new conversation is bound to that
    agent; otherwise it lands in the registry's default agent. Existing
    conversations keep whatever agent they were created under — we
    never rebind on lookup.

    The optional ``channel`` / ``account_id`` / ``peer`` triple binds the
    new conversation to a chat channel (e.g. ``wechat`` + ``baby``).
    Ignored on lookup of existing conversations — call
    ``set_conversation_channel`` to change them after creation.
    """
    if session_id is None:
        session_id = "local_" + uuid.uuid4().hex[:10]
    with _sessions_lock:
        if session_id not in _sessions:
            resolved_agent = agent_id or _default_agent_id()
            # Hydrate the active branch from SessionDB so a webui
            # restart / fresh worker process sees the same messages
            # the dispatcher and channels worker have been writing.
            # Empty list for brand-new conversations.
            try:
                from openprogram.agent.session_db import default_db
                _db = default_db()
                _hydrated = _db.get_branch(session_id) or []
                _sess = _db.get_session(session_id)
                _hydrated_head = _sess.get("head_id") if _sess else None
            except Exception:
                _hydrated = []
                _sess = None
                _hydrated_head = None
            resolved_agent = (
                agent_id
                or ((_sess or {}).get("agent_id") if isinstance(_sess, dict) else None)
                or resolved_agent
            )
            # Inherit a user-pinned (provider, model) from the most
            # recent picker click that didn't have a session attached.
            # Lets the welcome-page flow "pick Opus, then start a chat"
            # actually run Opus — otherwise the new conv falls back to
            # the agent profile's default model.
            _inherit_prov = _user_pinned_provider
            _inherit_model = _user_pinned_model
            _log(
                f"[_get_or_create_session] creating {session_id!r} "
                f"inherit_prov={_inherit_prov!r} inherit_model={_inherit_model!r}"
            )
            _sessions[session_id] = {
                "id": session_id,
                "agent_id": resolved_agent,
                "title": ((_sess or {}).get("title") if isinstance(_sess, dict) else None)
                         or "New conversation",
                "root_context": Context(name="chat_session", status="idle", start_time=time.time()),
                "runtime": None,          # created lazily on first message
                "provider_name": ((_sess or {}).get("provider_name") if isinstance(_sess, dict) else None)
                                 or _inherit_prov,
                "provider_override": _inherit_prov,
                "model_override": _inherit_model,
                "messages": _hydrated,
                "function_trees": [],
                "created_at": ((_sess or {}).get("created_at") if isinstance(_sess, dict) else None)
                              or time.time(),
                "head_id": _hydrated_head,
                "run_active": False,
                "source": ((_sess or {}).get("source") if isinstance(_sess, dict) else None),
                "channel": channel
                          if channel is not None
                          else ((_sess or {}).get("channel") if isinstance(_sess, dict) else None),
                "account_id": account_id
                              if account_id is not None
                              else ((_sess or {}).get("account_id") if isinstance(_sess, dict) else None),
                "peer": peer
                        if peer is not None
                        else ((_sess or {}).get("peer") if isinstance(_sess, dict) else None),
                "peer_display": ((_sess or {}).get("peer_display") if isinstance(_sess, dict) else None),
                "tools_enabled": ((_sess or {}).get("tools_enabled") if isinstance(_sess, dict) else None),
                "tools_override": ((_sess or {}).get("tools_override") if isinstance(_sess, dict) else None),
                "thinking_effort": ((_sess or {}).get("thinking_effort") if isinstance(_sess, dict) else None),
                "permission_mode": ((_sess or {}).get("permission_mode") if isinstance(_sess, dict) else None),
            }
        return _sessions[session_id]


def _is_run_active(session_id: str) -> bool:
    """Is there an in-flight agent run for this conversation?

    Single source of truth for UI gating (Edit / Retry buttons go grey
    while a run is active). Driven off ``_running_tasks`` — the same
    dict we use for pause / stop, so we can't drift out of sync.
    """
    with _running_tasks_lock:
        if session_id not in _running_tasks:
            return False
    # Zombie entry (no live runtime registered) → not actually running.
    # Drop it so subsequent calls don't keep blocking Edit/Retry/etc.
    if not _has_active_runtime(session_id):
        with _running_tasks_lock:
            _running_tasks.pop(session_id, None)
        return False
    return True


# DAG helpers live in openprogram.contextgit. We keep ``advance_head``
# as the in-memory mutation primitive but wrap it in ``_append_msg``
# below so every webui write also flows into SessionDB. That makes the
# dispatcher / channels worker / TUI see writes from the webui WS
# handlers without waiting for the next ``_save_session``.
from openprogram.contextgit import (  # noqa: E402
    advance_head as _raw_advance_head,
    head_or_tip as _head_or_tip,
    linear_history as _linear_history,
)


def _append_msg(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv``: in-memory mirror + SessionDB.

    Single source of truth path for non-dispatcher webui writes (run /
    create / error / system messages). Dispatcher already writes
    user+assistant rows itself; this helper covers everything else.

    Order matters:
      1. ``_raw_advance_head`` mutates ``conv["messages"]`` and
         ``conv["head_id"]`` so existing readers see it immediately.
      2. SessionDB.append_message persists for cross-process readers.
      3. SessionDB.set_head bumps the active leaf — without this,
         a fresh ``_get_messages`` cache miss would walk back to the
         old head and miss the just-appended row.
      4. Cache invalidation is last so step 3 is visible.

    Failures in steps 2-4 are logged but non-fatal; the in-memory
    mirror is still consistent and the next ``_save_session``
    will sync the row through ``save_messages`` (idempotent).
    """
    _raw_advance_head(conv, msg)
    cid = conv.get("id")
    msg_id = msg.get("id")
    if not cid or not msg_id:
        return
    try:
        from openprogram.agent.session_db import default_db
        db = default_db()
        if db.get_session(cid) is None:
            create_kwargs = {}
            # Channel binding + presentational fields.
            for fld in ("channel", "account_id", "peer", "peer_display", "source", "title"):
                v = conv.get(fld)
                if v:
                    create_kwargs[fld] = v
            # Per-session run config — these used to be written via
            # save_session_run_config which create_session'd a ghost row
            # even when the user never sent a real message. Now folded
            # into the same create_session call as the first message so
            # SessionDB only ever holds rows for sessions with content.
            for fld in ("tools_enabled", "tools_override", "thinking_effort", "permission_mode"):
                v = conv.get(fld)
                if v is not None:
                    create_kwargs[fld] = v
            db.create_session(cid, conv.get("agent_id") or _default_agent_id(), **create_kwargs)
        db.append_message(cid, msg)
        db.set_head(cid, msg_id)
    except Exception as e:
        _log(f"_append_msg: SessionDB write failed for {cid}/{msg_id}: {e}")
    _invalidate_messages(cid)


# Thinking-effort picker configs + runtime apply helpers live in
# _thinking.py. Re-exported here for existing call sites.
from ._thinking import (  # noqa: E402
    THINKING_CONFIGS as _THINKING_CONFIGS,
    apply_thinking_effort as _apply_thinking_effort,
    default_effort_for as _default_effort_for,
    get_thinking_config as _get_thinking_config,
    get_thinking_config_for_model as _get_thinking_config_for_model,
    resolve_effort as _resolve_effort,
)


def _execute_in_context(session_id: str, msg_id: str, action: str,
                        func_name: str = None, kwargs: dict = None, query: str = None,
                        thinking_effort: str = None, exec_thinking_effort: str = None,
                        tools_flag=None, permission_mode: str = None,
                        attachments: list = None):
    """Execute a chat query or function call within the conversation's Context tree.

    This is the core execution engine. Everything runs under the conversation's
    root Context, so summarize() automatically provides conversation history.
    """
    _conv_token = _set_current_session_id(session_id)
    try:
        conv = _get_or_create_session(session_id)
        # Resolve the owning agent once so every persist call in this
        # function uses a stable id even if the caller later rebinds
        # the conv dict.
        _agent_id = conv.get("agent_id") or _default_agent_id()
        runtime = _get_session_runtime(session_id, msg_id=msg_id)
        from openprogram.agent.session_config import (
            load_session_run_config,
            permission_from_config,
            save_session_run_config,
            tools_override_from_config,
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
        _apply_thinking_effort(runtime, effective_thinking)

        try:
            if action == "query":
                # Direct chat — include conversation history for context
                _log(f"[exec] query: {query[:80]}... (thinking={effective_thinking})")
                with _running_tasks_lock:
                    _running_tasks[session_id] = {
                        "msg_id": msg_id,
                        "func_name": "_chat",
                        "started_at": time.time(),
                        "last_event_at": time.time(),
                        "display_params": "",
                        "loaded_func_ref": None,
                        "stream_events": [],
                    }
                _broadcast_chat_response(session_id, msg_id, {
                    "type": "status", "content": "Thinking...",
                })

                # Build conversation context from history
                # Rough token estimate: ~4 chars per token, keep under 80k tokens
                _MAX_CONTEXT_CHARS = 320_000
                history_parts = []
                total_chars = 0
                # Build history from the ACTIVE BRANCH only. conv["messages"]
                # is a flat DAG store containing every sibling branch; if we
                # iterate it raw after a retry/edit, the model sees content
                # from the branch we forked away from, which both pollutes
                # the prompt and inflates token counts. linear_history walks
                # the parent chain from HEAD, so retries are isolated.
                _all_msgs = conv.get("messages", [])
                _head = _head_or_tip(conv, _all_msgs)
                messages = _linear_history(_all_msgs, _head) if _head else _all_msgs
                # Drop the in-flight placeholder so the query isn't duplicated
                # (assistant placeholder has empty content; retry of a user
                # turn has msg_id == HEAD with content == query).
                messages = [m for m in messages if m.get("id") != msg_id]
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

                # Resolve tools from the session-level setting. When
                # unset, dispatcher falls back to the agent profile.
                resolved_tools_override = tools_override_from_config(run_cfg)

                # Hand the turn to the unified dispatcher. The user
                # message is already persisted by the WS handler that
                # spawned us, so we set user_already_persisted=True and
                # pass the same msg_id frontend used for chat_ack.
                #
                # The dispatcher emits chat_response envelopes that we
                # forward to the existing _broadcast_chat_response so
                # the WS contract stays unchanged.
                from openprogram.agent.dispatcher import (
                    TurnRequest as _TurnRequest,
                    process_user_turn as _process_user_turn,
                )

                _register_active_runtime(session_id, runtime)
                _chat_cancel_event = threading.Event()
                _register_cancel_event(session_id, _chat_cancel_event)
                if _is_cancelled(session_id):
                    _chat_cancel_event.set()

                tool_calls_collected: list[dict] = []
                # Live block accumulator. Each tool_use opens a new
                # block keyed by tool_call_id; the matching tool_result
                # fills in `result` / `is_error`. The final list is
                # shipped on the result envelope so the frontend can
                # store it on the in-memory message dict (and so the
                # immediate post-run render matches the after-refresh
                # render that pulls msg.blocks from DB).
                tool_blocks_collected: list[dict] = []
                tool_blocks_by_id: dict[str, dict] = {}

                def _on_dispatcher_event(env: dict) -> None:
                    et = env.get("type")
                    if et != "chat_response":
                        return
                    payload = env.get("data") or {}
                    # Track stream events on the running_tasks entry
                    # so the existing reconnect/replay logic works.
                    if payload.get("type") == "stream_event":
                        evt = payload.get("event") or {}
                        with _running_tasks_lock:
                            ti = _running_tasks.get(session_id)
                            if ti and "stream_events" in ti:
                                ti["stream_events"].append(evt)
                                if len(ti["stream_events"]) > 200:
                                    ti["stream_events"] = ti["stream_events"][-200:]
                                ti["last_event_at"] = time.time()
                        if evt.get("type") == "tool_use":
                            _tid = evt.get("tool_call_id")
                            blk = {
                                "type": "tool",
                                "tool": evt.get("tool"),
                                "tool_call_id": _tid,
                                "input": evt.get("input"),
                                "result": None,
                                "is_error": False,
                            }
                            tool_blocks_collected.append(blk)
                            if _tid:
                                tool_blocks_by_id[_tid] = blk
                        if evt.get("type") == "tool_result":
                            _tid = evt.get("tool_call_id")
                            blk = tool_blocks_by_id.get(_tid)
                            if blk is None:
                                # Result without prior tool_use (rare,
                                # but degrade gracefully so the user
                                # still sees something).
                                blk = {
                                    "type": "tool",
                                    "tool": evt.get("tool"),
                                    "tool_call_id": _tid,
                                    "input": None,
                                    "result": None,
                                    "is_error": False,
                                }
                                tool_blocks_collected.append(blk)
                                if _tid:
                                    tool_blocks_by_id[_tid] = blk
                            blk["result"] = evt.get("result")
                            blk["is_error"] = bool(evt.get("is_error"))
                            tool_calls_collected.append({
                                "tool": evt.get("tool"),
                                "result": evt.get("result"),
                                "is_error": evt.get("is_error"),
                            })
                        # Fan out to WS clients with the same envelope
                        # shape the legacy on_stream hook used.
                        _broadcast_chat_response(session_id, msg_id, {
                            "type": "stream_event",
                            "event": evt,
                            "function": "_chat",
                        })
                    elif payload.get("type") in ("result", "error"):
                        # Final-result / error envelopes arrive last;
                        # we surface them after our own context_stats
                        # broadcast below, so swallow here.
                        pass

                # Carry the conversation's picker choice (if any) into
                # the dispatcher so it doesn't fall back to the agent
                # profile's default model. Without this the model
                # picker only updates `conv["runtime"]`, but the
                # dispatcher re-resolves through `_resolve_model` and
                # silently routes back to the agent default — that's
                # the "I picked Opus but it answers as Sonnet" bug.
                _conv_now = _sessions.get(session_id) or {}
                _picker_provider = _conv_now.get("provider_override")
                _picker_model = _conv_now.get("model_override")
                _model_override = None
                if _picker_provider and _picker_model:
                    _model_override = f"{_picker_provider}/{_picker_model}"
                elif _picker_model:
                    _model_override = _picker_model
                _log(
                    f"[model resolve] session={session_id!r} "
                    f"provider_override={_picker_provider!r} "
                    f"model_override={_picker_model!r} "
                    f"agent_model={_conv_now.get('agent_id')!r}/profile "
                    f"resolved={_model_override!r}"
                )

                req_obj = _TurnRequest(
                    session_id=session_id,
                    user_text=query,
                    agent_id=_agent_id,
                    source="web",
                    permission_mode=effective_permission,
                    tools_override=resolved_tools_override,
                    thinking_effort=effective_thinking,
                    user_msg_id=msg_id,
                    user_already_persisted=True,
                    model_override=_model_override,
                    attachments=attachments,
                )

                try:
                    turn_result = _process_user_turn(
                        req_obj, on_event=_on_dispatcher_event,
                        cancel_event=_chat_cancel_event,
                    )
                finally:
                    with _running_tasks_lock:
                        _running_tasks.pop(session_id, None)
                    _unregister_active_runtime(session_id)
                    _unregister_cancel_event(session_id)

                if turn_result.failed:
                    _broadcast_chat_response(session_id, msg_id, {
                        "type": "error",
                        "content": turn_result.error or "(unknown error)",
                    })
                    return

                result = turn_result.final_text
                _log(f"[exec] query completed, result length: {len(str(result))}")

                # Dispatcher persisted the assistant message itself
                # (with id=msg_id+'_a'). Hydrate the in-memory mirror
                # from SessionDB so subsequent webui readers
                # (load_session, retry, etc.) see it.
                _hydrate_messages_from_db(session_id)
                with _sessions_lock:
                    refreshed = _sessions.get(session_id)
                    if refreshed is not None:
                        try:
                            from openprogram.agent.session_db import default_db
                            refreshed["messages"] = default_db().get_branch(session_id) or []
                            sess = default_db().get_session(session_id)
                            if sess:
                                refreshed["head_id"] = sess.get("head_id")
                        except Exception:
                            pass

                # Blocks: dispatcher persists them in extra; we also
                # ship them on the result envelope so the in-memory
                # transcript carries the same collapsible scaffold as
                # the after-refresh DB-rebuilt view (otherwise the
                # user sees rich tool bubbles during streaming, then
                # plain text once we stamp the message, then the
                # rebuilt scaffold after refresh — three different
                # renders for the same turn).
                _broadcast_chat_response(session_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                    "tool_calls": tool_calls_collected,
                    "blocks": tool_blocks_collected,
                })
                _broadcast_context_stats(session_id, msg_id, chat_runtime=runtime)

                # If this is a channel-bound agent session (WeChat /
                # Telegram / etc.), push the web-side reply back out to
                # the external user so their phone sees it too. The
                # session meta carries channel + account_id + peer — we
                # look it up from disk because the webui's in-memory
                # conversation dict doesn't always carry these fields
                # yet.
                try:
                    from openprogram.channels.outbound import send as _send
                    meta = _load_agent_session_meta(session_id)
                    if meta and meta.get("channel") and meta.get("account_id"):
                        peer_id = (meta.get("peer") or {}).get("id") or ""
                        if peer_id:
                            _send(
                                meta["channel"],
                                meta["account_id"],
                                str(peer_id),
                                str(result),
                            )
                except Exception as e:  # noqa: BLE001
                    _log(f"[channel outbound] skipped: "
                         f"{type(e).__name__}: {e}")

            elif action == "run":
                # Validate create() description
                if func_name == "create" and kwargs and "description" in kwargs:
                    desc = kwargs["description"].strip()
                    if len(desc) < 5:
                        _broadcast_chat_response(session_id, msg_id, {
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
                            _broadcast_chat_response(session_id, msg_id, {
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
                    _running_tasks[session_id] = {
                        "msg_id": msg_id,
                        "func_name": func_name,
                        "started_at": time.time(),
                        "display_params": _display_params,
                        "loaded_func_ref": None,  # set after load
                        "stream_events": [],  # buffered for refresh recovery
                    }
                _broadcast_chat_response(session_id, msg_id, {
                    "type": "status",
                    "content": f"Running {func_name}...",
                })

                loaded_func = _load_function(func_name)
                if loaded_func is None:
                    _broadcast_chat_response(session_id, msg_id, {"type": "error", "content": f"Function '{func_name}' not found."})
                    return
                with _running_tasks_lock:
                    if session_id in _running_tasks:
                        _running_tasks[session_id]["loaded_func_ref"] = loaded_func
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
                _register_active_runtime(session_id, exec_rt)
                _inject_runtime(loaded_func, call_kwargs, exec_rt)

                # Register streaming callback for real-time LLM output
                def _on_stream(event: dict):
                    # Buffer for refresh recovery (keep last 200 events)
                    with _running_tasks_lock:
                        ti = _running_tasks.get(session_id)
                        if ti and "stream_events" in ti:
                            ti["stream_events"].append(event)
                            if len(ti["stream_events"]) > 200:
                                ti["stream_events"] = ti["stream_events"][-200:]
                    _broadcast_chat_response(session_id, msg_id, {
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
                _persist.init_tree(_agent_id, session_id, _run_func_idx, _run_attempt_idx)
                _save_session(session_id)

                # Register event-driven tree updates: append each node event
                # to the JSONL file and broadcast a full partial tree.
                def _tree_event_callback(event_type: str, data: dict):
                    try:
                        if event_type == "node_created":
                            _persist.append_tree_event(
                                _agent_id, session_id, _run_func_idx, _run_attempt_idx,
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
                                _agent_id, session_id, _run_func_idx, _run_attempt_idx,
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
                            _broadcast_chat_response(session_id, msg_id, {
                                "type": "tree_update",
                                "tree": partial_tree,
                                "function": func_name,
                            })
                    except Exception:
                        pass

                on_event(_tree_event_callback)

                # Follow-up support + execution
                with _web_follow_up(session_id, msg_id, func_name, tree_cb=_tree_event_callback):
                    try:
                        result = _format_result(loaded_func(**call_kwargs), action=func_name)
                    finally:
                        off_event(_tree_event_callback)
                        with _running_tasks_lock:
                            _running_tasks.pop(session_id, None)
                        _unregister_active_runtime(session_id)
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
                    "parent_id": msg_id,  # child of this run's user turn
                }
                _append_msg(conv, reply_msg)
                _broadcast_chat_response(session_id, msg_id, {
                    "type": "result",
                    "content": str(result),
                    "function": func_name,
                    "display": "runtime",
                    "context_tree": tree_dict,
                    "attempts": reply_msg["attempts"],
                    "current_attempt": 0,
                    "usage": _func_usage,
                })
                _broadcast_context_stats(session_id, msg_id, exec_runtime=exec_rt)

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
        _save_session(session_id)

    except (Exception, _CancelledError) as e:
        with _running_tasks_lock:
            _running_tasks.pop(session_id, None)
        _unregister_active_runtime(session_id)

        # Cancellation path — either the exception came from /api/stop killing
        # the subprocess, or a CancelledError was raised by the cancel hook
        # (e.g. loops between exec calls). Mark any still-running tree nodes
        # as cancelled and emit a "stopped" result instead of an error message.
        if _is_cancelled(session_id) or isinstance(e, _CancelledError):
            _clear_cancel(session_id)
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
                                    _agent_id, session_id, _fidx, _aidx,
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
                    _broadcast_chat_response(session_id, msg_id, {
                        "type": "tree_update",
                        "tree": ctx._to_dict(),
                        "function": func_name,
                    })
                except Exception:
                    pass
            try:
                conv = _get_or_create_session(session_id)
                now = time.time()
                _append_msg(conv, {
                    "role": "assistant",
                    "type": "cancelled",
                    "id": msg_id + "_reply",
                    "parent_id": msg_id,
                    "content": "Execution stopped by user.",
                    "function": func_name,
                    "display": "runtime",
                    "timestamp": now,
                })
                _save_session(session_id)
            except Exception:
                pass
            _broadcast_chat_response(session_id, msg_id, {
                "type": "result",
                "content": "Execution stopped by user.",
                "function": func_name,
                "cancelled": True,
                "context_tree": ctx._to_dict() if ctx is not None else None,
            })
            return

        error_content = f"Error: {e}\n\n{traceback.format_exc()}"
        # Plain chat errors (action="query", no function) should be shown as
        # chat messages with a retry button, not as runtime blocks.
        error_display = "runtime" if func_name else "chat"
        try:
            conv = _get_or_create_session(session_id)
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
            _append_msg(conv, error_msg)
            _save_session(session_id)
        except Exception:
            pass
        _broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": error_content,
            "function": func_name,
            "display": error_display,
            "retry_query": query if not func_name else None,
        })
    finally:
        _reset_current_session_id(_conv_token)


def _broadcast_context_stats(session_id: str, msg_id: str, chat_runtime=None, exec_runtime=None):
    """Broadcast chat & exec token usage stats to frontend.

    Chat usage: use the provider's latest reported value directly.
      - CLI providers report usage that already reflects the full session context.
      - API providers report usage that includes the full conversation in input_tokens.
      - No accumulation — provider knows best about its own usage.
    Exec usage: per-function execution, read from exec_runtime.last_usage.
    """
    conv = _sessions.get(session_id)
    if not conv:
        return

    _zero = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0}

    # --- Chat usage: use last_usage (per-call = current context window size) ---
    # NOT session_usage (cumulative across all API calls, inflated for Codex).
    # last_usage.input_tokens = total tokens sent in the last call ≈ context size.
    if chat_runtime:
        usage = getattr(chat_runtime, 'last_usage', None)
        if usage and (usage.get("input_tokens") or usage.get("output_tokens") or usage.get("cache_read") or usage.get("cache_create")):
            conv["_chat_usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read": usage.get("cache_read", 0),
                "cache_create": usage.get("cache_create", 0),
            }

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

    # Best-effort context window for the current model — frontend uses this
    # to render the input/output % bar. Falls back to None on unknown.
    context_window = None
    if chat_runtime:
        try:
            context_window = getattr(chat_runtime, "_context_window_tokens", None)
        except Exception:
            context_window = None

    chat_model = getattr(chat_runtime, "model", None) if chat_runtime else None

    stats = {
        "type": "context_stats",
        "chat": conv.get("_chat_usage", dict(_zero)),
        "exec": exec_stats,
        "provider": provider_name,
        "model": chat_model,
        "context_window": context_window,
    }
    conv["_last_context_stats"] = stats
    _broadcast_chat_response(session_id, msg_id, stats)


def _broadcast_chat_response(session_id: str, msg_id: str, response: dict):
    """Broadcast a chat response to all WebSocket clients."""
    response["session_id"] = session_id
    response["msg_id"] = msg_id
    response["timestamp"] = time.time()

    # No need to store in messages list — Context tree IS the storage
    msg = json.dumps({"type": "chat_response", "data": response}, default=str)
    _broadcast(msg)


# ---------------------------------------------------------------------------
# MessageStore → WebSocket bridge (v2 streaming protocol)
# ---------------------------------------------------------------------------
# Every frame the store emits is wrapped in the same `chat_response` envelope
# the rest of the chat traffic uses, so the frontend has one dispatcher to
# route everything. Frames carry their own session_id so clients filter.

from openprogram.webui._chat_helpers import (
    wire_message_store_broadcast as _wire_message_store_broadcast,
    parse_chat_input as _parse_chat_input,
)


# ---------------------------------------------------------------------------
# WebSocket handler (module-level to avoid FastAPI closure issues)
# ---------------------------------------------------------------------------

async def _websocket_handler(ws):
    """WebSocket endpoint for real-time Context tree updates and chat."""
    await ws.accept()

    # Install the global store→WS broadcaster on first connection. We can't
    # wire it at module import because the asyncio loop isn't running yet;
    # the broadcaster needs a live loop to schedule ws.send_text coroutines.
    _wire_message_store_broadcast()

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
        with _sessions_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
                for c in _sessions.values()
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

def _build_ws_action_registry() -> dict:
    """Lazy-build the action → handler dispatch table.

    Done at module import time but populated from ws_actions/* modules
    that internally `from openprogram.webui import server as _s` — safe
    because lookup only happens when an action fires at WS-message time,
    well after server.py has finished loading.
    """
    from openprogram.webui.ws_actions import (
        agent as _ws_agent,
        branch as _ws_branch,
        channel as _ws_channel,
        chat as _ws_chat,
        runtime as _ws_runtime,
        session as _ws_session,
    )
    table: dict = {}
    table.update(_ws_branch.ACTIONS)
    table.update(_ws_session.ACTIONS)
    table.update(_ws_agent.ACTIONS)
    table.update(_ws_channel.ACTIONS)
    table.update(_ws_runtime.ACTIONS)
    table.update(_ws_chat.ACTIONS)
    return table


WS_ACTIONS: dict = _build_ws_action_registry()


async def _handle_ws_command(ws, cmd: dict):
    """Handle a WebSocket command from the client."""
    action = cmd.get("action")
    print(f"[ws] command received: action={action}")

    # Fast path: action handled by an extracted module.
    h = WS_ACTIONS.get(action)
    if h is not None:
        await h(ws, cmd)
        return




# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def create_app():
    """Create and return the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Agentic Visualizer", docs_url=None, redoc_url=None)

    # Auth v2 REST + SSE routes. Kept in a dedicated module so server.py
    # doesn't accumulate more authentication state than it already has.
    from ._auth_routes import router as _auth_router
    app.include_router(_auth_router)

    # Frontend is served separately from web/ (Next.js). This process only
    # serves /api/* and /ws. Run `cd web && npm run dev` and point the browser
    # at http://localhost:3000 — Next will proxy /api/* and /ws back to us.

    @app.on_event("startup")
    async def _capture_loop():
        global _loop
        _loop = asyncio.get_running_loop()

    @app.on_event("startup")
    async def _rehydrate_message_store():
        """Pick up v2 messages.jsonl from disk on startup.

        ``_restore_sessions`` already handles the v1 ``messages.json``
        layout via persistence.py. This callback does the v2 side —
        MessageStore scans its persist dir for per-conv ``messages.jsonl``
        files and loads them back into memory so reconnecting clients
        get the right state even if the server just restarted.
        """
        try:
            loaded = _get_message_store().load_all()
            if loaded:
                _log(f"[v2-restore] rehydrated {len(loaded)} conversation(s) from JSONL")
        except Exception as e:
            _log(f"[v2-restore] failed: {e}")

    # The previous boot-time refresh of `claude_models.json` relied on
    # the now-removed Claude Code CLI runtime to enumerate models. The
    # static catalog shipped with the repo is the source of truth now;
    # update it via `tools/scripts/refresh_claude_models.py` (offline)
    # if Anthropic ships a new model family.

    # No HTML routes — frontend lives in web/ (Next.js on :3000).

    # WebSocket — use Starlette's raw WebSocketRoute to avoid FastAPI routing issues
    from starlette.routing import WebSocketRoute
    app.routes.insert(0, WebSocketRoute("/ws", _websocket_handler))

    # REST endpoints
    # Read-only catalog routes (tree, functions, tokens, programs meta)
    from openprogram.webui.routes import tree as _routes_tree
    _routes_tree.register(app)

    # /api/chat, /api/chat/branch, /api/run/{name} — routes.chat
    from openprogram.webui.routes import chat as _routes_chat
    _routes_chat.register(app)

    # Retry / Edit / Checkout routes live in _chat_routes.py — see
    # docs/design/contextgit.md. Keeping them out of this module keeps
    # it under control.
    from ._chat_routes import router as _chat_router
    app.include_router(_chat_router)

    # Workdir picker, browse, history, canvas — registered from routes.workdir
    from openprogram.webui.routes import workdir as _routes_workdir
    _routes_workdir.register(app)

    # Pause / Resume / Stop — routes.lifecycle
    from openprogram.webui.routes import lifecycle as _routes_lifecycle
    _routes_lifecycle.register(app)

    # /api/providers, /api/provider/{name}, /api/models — routes.runtime
    from openprogram.webui.routes import runtime as _routes_runtime
    _routes_runtime.register(app)

    # Model catalog (LobeChat-style settings) — routes.providers
    from openprogram.webui.routes import providers as _routes_providers
    _routes_providers.register(app)

    # /api/config GET/POST registered from routes.config
    from openprogram.webui.routes import config as _routes_config
    _routes_config.register(app)

    # Function source / editor + node lookup — routes.functions
    from openprogram.webui.routes import functions as _routes_functions
    _routes_functions.register(app)

    # Memory API — routes registered from openprogram.webui.routes.memory
    from openprogram.webui.routes import memory as _routes_memory
    _routes_memory.register(app)

    from openprogram.webui.routes import misc as _routes_misc
    _routes_misc.register(app)

    return app


# ---------------------------------------------------------------------------
# Server runner (in background thread)
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start_server(port: int = 8110, open_browser: bool = True) -> threading.Thread:
    """
    Start the visualization server in a background daemon thread.

    Returns the thread object. The server runs until the process exits.
    """
    global _server_thread, _loop

    if _server_thread is not None and _server_thread.is_alive():
        print(f"Visualizer already running")
        return _server_thread

    # Register our event callback (cheap)
    on_event(_on_context_event)

    # Session restore is disk-bound and can take ~200–800ms on a busy
    # transcript dir. Defer it into a background thread so the uvicorn
    # socket comes up first — the CLI can connect while restore is still
    # walking files. /resume queries pull straight from disk anyway.
    #
    # Eagerly import provider registry on the main thread BEFORE
    # spawning the restore thread. Two daemons (this restore thread and
    # the worker's provider warm-up) used to race into the same
    # provider module imports, occasionally tripping Python's import
    # lock with `_DeadlockError`. When that fired, _restore_sessions
    # died silently and every load_session afterwards returned an empty
    # envelope (no head, no messages, "正在等待" forever). Forcing the
    # provider import here makes the module lock cold by the time the
    # threads start, so the deadlock can't form.
    try:
        import openprogram.providers  # noqa: F401
    except Exception as _e:
        _log(f"[startup] provider preload failed: {_e}")
    threading.Thread(
        target=_restore_sessions,
        name="openprogram-session-restore",
        daemon=True,
    ).start()

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
