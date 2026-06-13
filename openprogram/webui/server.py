"""
Visualization server — FastAPI + WebSocket for chat + DAG viewing
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

from openprogram.functions.agentics.ask_user import set_ask_user, ask_user
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
    set_current_session_id as _set_current_session_id,
    reset_current_session_id as _reset_current_session_id,
)
from openprogram.agentic_programming.function import CancelledError as _CancelledError
from openprogram.webui.messages import get_store as _get_message_store
from openprogram.webui._stream_bridge import StreamBridge
from openprogram.webui._exec_dag import (
    build_exec_dag, live_progress, reconcile_interrupted_runs,
)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_ws_connections: list[Any] = []
_ws_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Module load timestamp — used by /healthz uptime calc.
_SERVER_START_TIME = time.time()

# Max session rows sent to the CLI Welcome panel. Catalog data such as
# tools, providers, functions, skills, agents, and channels is sent in full;
# the TUI decides how many rows fit for the current terminal size.
WELCOME_STATS_SESSION_LIMIT = 48

# Conversation storage (in-memory). The conv dict owns runtime +
# metadata; the ``messages`` array is a derived view of SessionDB's
# active branch — see _get_messages / _invalidate_messages below.
# Execution traces are DAG nodes in SessionDB, not a conv field.
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


def _emit_running_task_event(session_id: str) -> None:
    """Broadcast the current running-task state for ``session_id``.

    Emits a ``running_task`` envelope if a task is active, or a
    ``running_task_clear`` envelope otherwise. The frontend uses these
    to drive the per-session composer state and the sidebar breathing
    indicator. Callers should invoke this immediately after mutating
    ``_running_tasks`` (still under the lock is fine — the actual
    socket send is queued).
    """
    try:
        with _running_tasks_lock:
            task = _running_tasks.get(session_id)
        if task:
            payload = {
                "type": "running_task",
                "data": {
                    "session_id": session_id,
                    "msg_id": task.get("msg_id"),
                    "func_name": task.get("func_name"),
                    "started_at": task.get("started_at"),
                    "display_params": task.get("display_params", ""),
                },
            }
        else:
            payload = {
                "type": "running_task_clear",
                "data": {"session_id": session_id},
            }
        _broadcast(json.dumps(payload, default=str))
    except Exception:
        # Broadcast is best-effort; never let it kill the turn.
        pass



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
            "context_tree": None,
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

            root_ctx = None  # tree Context retired — UI now reads DAG nodes

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
            # straight linear chain (see docs/design/context/contextgit.md).
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
    """Load config from ~/.openprogram/config.json.

    Delegates to the single canonical reader in ``openprogram.setup`` so the
    web and CLI never diverge on read/error-handling policy."""
    from openprogram import setup as _setup
    return _setup._read_config()


def _save_config(config: dict):
    """Save config to ~/.openprogram/config.json.

    Delegates to the canonical writer in ``openprogram.setup`` so there is one
    write path — and one place that enforces 0o600 on the secrets-bearing
    file."""
    from openprogram import setup as _setup
    _setup._write_config(config)


def _get_api_key(env_var: str) -> str:
    """Get a search/TTS key from the environment (injected from
    config.json ``api_keys`` by ``_apply_config_keys`` below). LLM
    provider keys do NOT resolve here — use ``_llm_is_configured`` /
    the AuthStore resolvers."""
    return os.environ.get(env_var) or ""


def _llm_is_configured(provider_id: str) -> bool:
    """AuthStore-backed configured check for an LLM provider."""
    from openprogram.providers.env_api_keys import is_configured
    return is_configured(provider_id)


def _apply_config_keys():
    """Inject config file API keys into environment (if not already set).

    This serves the web-search / TTS key flows: their settings UI saves
    into config.json ``api_keys`` and their runtimes read ``os.environ``
    (e.g. ``TAVILY_API_KEY``). LLM provider keys do NOT live here — they
    are stored in the AuthStore and resolved per-request."""
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
        # Default is :3456 (where meridian / claude-max-api-proxy listen)
        # — NOT :18109 which is openprogram's own backend port and would
        # always answer 200, masking proxy failure as "available".
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
        ("anthropic", "Anthropic API", lambda: _llm_is_configured("anthropic"), ["ANTHROPIC_API_KEY"]),
        ("openai", "OpenAI API", lambda: _llm_is_configured("openai"), ["OPENAI_API_KEY"]),
        ("gemini", "Gemini API", lambda: _llm_is_configured("google"), ["GOOGLE_API_KEY"]),
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
    """Webui server log line.

    Stdout print is gated on "are we actually running as the webui
    server right now?" — when ``start_server`` has booted, the
    ``_server_thread`` global is alive, and stdout is the server's
    terminal where logs belong. Without that guard, every CLI REPL
    call that just imports ``_runtime_management`` (which calls this
    via ``_log``) would pollute the chat transcript with "[probe] xxx
    unavailable", "[restore] ...", etc.

    Broadcast to ws clients always runs — when no clients are
    connected ``_broadcast`` is a no-op anyway.

    ``OPENPROGRAM_DEBUG_RUNTIME=1`` mirrors lines to stderr regardless
    of mode for devs tracing CLI startup.
    """
    if _server_thread is not None and _server_thread.is_alive():
        print(text)
    else:
        import os as _os
        if _os.environ.get("OPENPROGRAM_DEBUG_RUNTIME", "").strip() in ("1", "true", "yes"):
            import sys as _sys
            print(text, file=_sys.stderr, flush=True)
    try:
        msg = json.dumps({"type": "server_log", "text": text}, default=str)
        _broadcast(msg)
    except Exception:
        pass


def _cleanup_session_resources(session_id: str, conv: dict):
    """Clean up all resources associated with a deleted conversation."""
    # Clean up follow-up queues and running tasks
    _follow_up_queues.pop(session_id, None)
    with _running_tasks_lock:
        _running_tasks.pop(session_id, None)


from openprogram.webui._functions import (
    _discover_functions,
    _extract_input_meta,
    _extract_function_info,
    _extract_all_functions,
    _inject_runtime,
    _format_result,
    _FunctionStub,
    _make_stub_from_file,
    _load_function,
)


# ---------------------------------------------------------------------------
# (Function discovery & loading moved to agentic_web._functions)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Conversation management — each conversation is a DAG in SessionDB
# ---------------------------------------------------------------------------

def _get_or_create_session(session_id: str = None,
                                agent_id: str = None,
                                *,
                                channel: str = None,
                                account_id: str = None,
                                peer: str = None) -> dict:
    """Get or create a conversation with its own DAG session + Runtime.

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
                "root_context": None,  # tree Context retired
                "runtime": None,          # created lazily on first message
                "provider_name": ((_sess or {}).get("provider_name") if isinstance(_sess, dict) else None)
                                 or _inherit_prov,
                "provider_override": _inherit_prov,
                "model_override": _inherit_model,
                "messages": _hydrated,
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
            # Fire session.start so plugins can hook session lifecycle.
            # Defensive: never let a hook break session creation.
            try:
                from openprogram.plugins.hooks import dispatch_hook, HookEvent
                dispatch_hook(HookEvent.SESSION_START, {
                    "session_id": session_id,
                    "agent_id": resolved_agent,
                    "channel": channel,
                })
            except Exception:
                pass
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
    # Streaming-resume: if a placeholder with this id already lives
    # in ``conv["messages"]`` (e.g. ``run.py`` wrote a status=running
    # row before kicking off the function, and now we're back with
    # the final reply), update the existing entry in place instead of
    # appending a duplicate. The on-disk side handles its own
    # dedup — ``SessionStore.append_message`` is idempotent on id
    # and the final reply uses ``GraphStoreShim.update()`` to patch
    # the persisted node.
    _existing_idx = -1
    if msg.get("id"):
        for _i, _existing in enumerate(conv.get("messages") or []):
            if _existing.get("id") == msg["id"]:
                _existing_idx = _i
                break
    if _existing_idx >= 0:
        conv["messages"][_existing_idx] = {**conv["messages"][_existing_idx], **msg}
        conv["head_id"] = msg["id"]
    else:
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


def _execute_in_context(session_id: str, msg_id: str, action: str, **kwargs):
    """Legacy name kept for ws_actions/chat.py and _chat_routes.py callers.

    The real implementation lives in openprogram/webui/_execute/. This shim
    just forwards so existing import sites keep working.
    """
    from ._execute import execute_in_context
    return execute_in_context(session_id, msg_id, action, **kwargs)




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
    """Broadcast a chat response to all WebSocket clients.

    Post-stop suppression: when this session has been cancelled
    (``mark_cancelled`` flag is up), drop any further chat_response
    envelopes for it. The in-flight worker thread can keep producing
    output for up to ~1.2s after stop while cooperative cancel
    reaches a hook point; without this gate, the UI would keep
    receiving streaming text / tree updates / partial tool results
    after the user explicitly asked for silence. The DB writes
    continue underneath (so the partial state is preserved if the
    user comes back), only the WS broadcast is gagged. The cancel
    flag is cleared by the cleanup path so subsequent turns can
    broadcast normally.
    """
    if _is_cancelled(session_id):
        # Always let the explicit ``stopped`` status frame through —
        # that's how the UI flips its own state to stopped. Anything
        # else (stream_event / tree_update / result / status≠stopped)
        # is post-stop noise and gets dropped.
        if not (response.get("type") == "status"
                and response.get("stopped")):
            return
    response["session_id"] = session_id
    response["msg_id"] = msg_id
    response["timestamp"] = time.time()

    # No need to store in messages list — the DAG in SessionDB IS the storage
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
    """WebSocket endpoint for real-time chat streaming."""
    await ws.accept()

    # Install the global store→WS broadcaster on first connection. We can't
    # wire it at module import because the asyncio loop isn't running yet;
    # the broadcaster needs a live loop to schedule ws.send_text coroutines.
    _wire_message_store_broadcast()

    with _ws_lock:
        _ws_connections.append(ws)
    try:
        # Send current state on connect. ``full_tree`` is kept as an
        # empty payload for protocol compatibility — execution traces
        # are now DAG nodes stored in SessionDB.
        await ws.send_text(json.dumps(
            {"type": "full_tree", "data": []}, default=str
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

    except Exception:
        import logging
        # structured + carries the traceback; never dumps a raw trace to stdout
        logging.getLogger("openprogram.webui").exception("[ws] connection error")
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
        context_commits as _ws_commits,
        revert as _ws_revert,
        turn_files as _ws_turn_files,
        sub_agent as _ws_sub_agent,
        merge as _ws_merge,
        task as _ws_task,
        worktree as _ws_worktree,
        project as _ws_project,
        settings as _ws_settings,
    )
    table: dict = {}
    table.update(_ws_branch.ACTIONS)
    table.update(_ws_session.ACTIONS)
    table.update(_ws_agent.ACTIONS)
    table.update(_ws_channel.ACTIONS)
    table.update(_ws_runtime.ACTIONS)
    table.update(_ws_chat.ACTIONS)
    table.update(_ws_commits.ACTIONS)
    table.update(_ws_revert.ACTIONS)
    table.update(_ws_turn_files.ACTIONS)
    table.update(_ws_sub_agent.ACTIONS)
    table.update(_ws_merge.ACTIONS)
    table.update(_ws_task.ACTIONS)
    table.update(_ws_worktree.ACTIONS)
    table.update(_ws_project.ACTIONS)
    table.update(_ws_settings.ACTIONS)
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
    # at http://localhost:18100 — Next will proxy /api/* and /ws back to us.

    @app.on_event("startup")
    async def _capture_loop():
        global _loop
        _loop = asyncio.get_running_loop()

    @app.on_event("startup")
    async def _subscribe_event_bus():
        """webui 降级为总线订阅者（framework-evolution.md 步 4）。

        外部源（task runner / channels / worktree / functions watcher /
        sub_agent）不再 import 本模块的 _broadcast；它们 emit `ws.frame`
        事件，本订阅者把原始帧原样广播给前端——前端零改动。

        订阅在 _capture_loop 之后挂，确保 _broadcast 依赖的 _loop 已就位。
        emit 发生在源所在线程（可能是 worker），_broadcast 内部用
        run_coroutine_threadsafe 跨线程投递，安全。
        """
        try:
            from openprogram.agent.event_bus import get_event_bus, WS_FRAME_EVENT

            def _forward(event):
                try:
                    frame = event.payload.get("frame")
                    if frame is not None:
                        _broadcast(json.dumps(frame, default=str))
                except Exception:
                    pass

            get_event_bus().subscribe(_forward, types={WS_FRAME_EVENT})
        except Exception as e:  # noqa: BLE001
            _log(f"[startup] event-bus WS forwarder failed: {e}")

    @app.on_event("startup")
    async def _reconcile_interrupted_runs():
        """Flip DAG nodes frozen at status='running' (a previous worker
        was killed mid-run) to 'error'. See webui/_exec_dag.py."""
        try:
            n = reconcile_interrupted_runs()
            if n:
                _log(f"[startup] reconciled {n} interrupted run node(s)")
        except Exception as e:  # noqa: BLE001
            _log(f"[startup] reconcile_interrupted_runs failed: {e}")

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

    @app.on_event("startup")
    async def _start_mcp_servers():
        """Spawn every enabled MCP server from ``mcp_servers.json``.

        Each server's ``tools/list`` output is registered as AgentTool
        entries (namespaced ``{server}__{tool}``). Failures are non-
        fatal — a misconfigured server logs and the worker keeps
        booting.
        """
        try:
            from openprogram.mcp import load_mcp_servers
            await load_mcp_servers()
        except Exception as e:  # noqa: BLE001
            _log(f"[mcp] startup failed: {type(e).__name__}: {e}")

    @app.on_event("startup")
    async def _start_skills_watcher():
        """Watch the five skill source directories and push ``skills:changed``
        to all connected WS clients whenever a SKILL.md file is added, edited
        or removed. Falls back to 5-second polling if ``watchdog`` isn't
        installed."""
        try:
            from openprogram.skills.watcher import start_watcher
            def _broadcast_changed():
                # 事件层 tap（B 类：技能文件变了）。放 _broadcast 之前——
                # watcher 线程里 _broadcast 可能抛错被上层吞掉，emit 先行。
                try:
                    from openprogram.agent.event_bus import emit_safe
                    emit_safe("skills.changed", "system")
                except Exception:
                    pass
                _broadcast(json.dumps({"type": "skills:changed"}))
            start_watcher(on_change=_broadcast_changed)
        except Exception as e:  # noqa: BLE001
            _log(f"[skills-watcher] startup failed: {type(e).__name__}: {e}")

    @app.on_event("startup")
    async def _start_plugin_autoupdate():
        """Periodically poll PyPI / npm for newer versions of installed
        plugins. Result is broadcast over WS as ``plugins:update_available``
        so the Plugins UI can badge upgradable rows."""
        try:
            from openprogram.plugins import autoupdate as _au
            def _broadcast_updates(payload: dict):
                try:
                    _broadcast(json.dumps({
                        "type": "plugins:update_available",
                        "data": payload,
                    }))
                except Exception:
                    pass
                # 事件层 tap（B 类：插件有新版）
                try:
                    from openprogram.agent.event_bus import emit_safe
                    emit_safe("plugins.update_available", "system",
                              {"count": len(payload or {})})
                except Exception:
                    pass
            _au.register_callback(_broadcast_updates)
            _au.start()
        except Exception as e:  # noqa: BLE001
            _log(f"[plugin-autoupdate] startup failed: {type(e).__name__}: {e}")

    @app.on_event("shutdown")
    async def _stop_mcp_servers():
        try:
            from openprogram.mcp import shutdown_mcp_servers
            await shutdown_mcp_servers()
        except Exception as e:  # noqa: BLE001
            _log(f"[mcp] shutdown failed: {type(e).__name__}: {e}")

    # The previous boot-time refresh of `claude_models.json` relied on
    # the now-removed Claude Code CLI runtime to enumerate models. The
    # static catalog shipped with the repo is the source of truth now;
    # update it via `tools/scripts/refresh_claude_models.py` (offline)
    # if Anthropic ships a new model family.

    # No HTML routes — frontend lives in web/ (Next.js on :18100).

    # WebSocket — use Starlette's raw WebSocketRoute to avoid FastAPI routing issues
    from starlette.routing import WebSocketRoute
    app.routes.insert(0, WebSocketRoute("/ws", _websocket_handler))

    # REST endpoints
    # Read-only catalog routes (tree, functions, tokens, programs meta)
    from openprogram.webui.routes import tree as _routes_tree
    _routes_tree.register(app)

    # POST /api/programs/refresh — re-scan agentics/ for newly-installed
    # programs (manual "refresh" button; same core the watcher uses).
    from openprogram.webui.routes import programs as _routes_programs
    _routes_programs.register(app)

    # /api/chat, /api/chat/branch, /api/run/{name} — routes.chat
    from openprogram.webui.routes import chat as _routes_chat
    _routes_chat.register(app)

    # Retry / Edit / Checkout routes live in _chat_routes.py — see
    # docs/design/context/contextgit.md. Keeping them out of this module keeps
    # it under control.
    from ._chat_routes import router as _chat_router
    app.include_router(_chat_router)

    # Workdir picker, browse, history, canvas — registered from routes.workdir
    from openprogram.webui.routes import workdir as _routes_workdir
    _routes_workdir.register(app)

    # @file mention support — composer search + single-file read.
    from openprogram.webui.routes import file_search as _routes_file_search
    _routes_file_search.register(app)

    # Pause / Resume / Stop — routes.lifecycle
    from openprogram.webui.routes import lifecycle as _routes_lifecycle
    _routes_lifecycle.register(app)

    # /api/providers, /api/provider/{name}, /api/models — routes.runtime
    from openprogram.webui.routes import runtime as _routes_runtime
    _routes_runtime.register(app)

    # Model catalog (LobeChat-style settings) — routes.providers
    from openprogram.webui.routes import providers as _routes_providers
    _routes_providers.register(app)

    from openprogram.webui.routes import provider_login as _routes_provider_login
    _routes_provider_login.register(app)

    # Generic per-provider account management (/api/providers/{id}/accounts/*).
    # Registered AFTER providers.py so its literal /claude-code/accounts routes
    # match first; this module serves every other provider from the AuthStore.
    from openprogram.webui.routes import accounts as _routes_accounts
    _routes_accounts.register(app)

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

    # /api/agents — agent list (used by settings/channels binding picker)
    from openprogram.webui.routes import agents as _routes_agents
    _routes_agents.register(app)

    # /api/channels/{platform}/{account_id}/status — adapter heartbeat
    from openprogram.webui.routes import channels as _routes_channels
    _routes_channels.register(app)

    # /api/mcp/* — MCP server management (shared by webui / CLI / TUI)
    from openprogram.webui.routes import mcp as _routes_mcp
    _routes_mcp.register(app)

    # /api/skills/* — Skills management
    from openprogram.webui.routes import skills as _routes_skills
    _routes_skills.register(app)

    # /api/plugins/* — Plugins management
    from openprogram.webui.routes import plugins as _routes_plugins
    _routes_plugins.register(app)

    # /api/commands/* — Unified slash-command registry (Phase 1)
    from openprogram.webui.routes import commands as _routes_commands
    _routes_commands.register(app)

    return app


# ---------------------------------------------------------------------------
# Server runner (in background thread)
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start_server(port: int = 18109, open_browser: bool = True) -> threading.Thread:
    """
    Start the visualization server in a background daemon thread.

    Returns the thread object. The server runs until the process exits.
    """
    global _server_thread, _loop

    if _server_thread is not None and _server_thread.is_alive():
        print(f"Visualizer already running")
        return _server_thread

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
    """Reserved for future shutdown hooks (no-op for now)."""
    pass
