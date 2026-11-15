"""
Visualization server — FastAPI + WebSocket for chat + DAG viewing
and interactive chat-style function execution.

Runs in a background thread alongside user code. Streams tree updates to
connected browsers via WebSocket.
"""

from __future__ import annotations

import logging
import asyncio
import json
import os
import threading
import time
from typing import Any, Optional

# Names imported only to be read as ``server.<name>`` (``from ... import
# server as state`` / ``_s``) by the sibling Server modules, or patched here by
# tests, carry ``# noqa: F401 -- server namespace, read as server.<name>``.
from openprogram.programs.tools.interaction.ask_user import set_ask_user  # noqa: F401 -- server namespace, read as server.<name>

# Stop / cancel primitives live in openprogram.runtime.conversation.run_control
from openprogram.runtime.conversation.run_control import (  # noqa: F401 -- server namespace, read as server.<name>
    is_cancelled as _is_cancelled,
    clear_cancel as _clear_cancel,
    register_active_runtime as _register_active_runtime,
    unregister_active_runtime as _unregister_active_runtime,
    has_active_runtime as _has_active_runtime,
)
from openprogram.runtime.execution.run_control import (  # noqa: F401 -- server namespace, read as server.<name>
    set_current_session_id as _set_current_session_id,
    reset_current_session_id as _reset_current_session_id,
)
from openprogram.host.cancellation import CancelledError as _CancelledError  # noqa: F401 -- server namespace, read as server.<name>
from openprogram.runtime.execution.dag import reconcile_interrupted_runs

logger = logging.getLogger(__name__)


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
# walking the predecessor CTE every time costs ~5ms; cached it's free.
# Why bounded LRU: webui keeps tens to hundreds of conversations
# warm; a single un-bounded dict would creep into RAM. 64 sessions
# × ~1MB serialized chat = ~64MB — comfortable on any modern host.
import collections as _collections   # noqa: E402

_msg_cache_lock = threading.Lock()
_MSG_CACHE_CAP = 64
_msg_cache: "_collections.OrderedDict[str, list[dict]]" = _collections.OrderedDict()


_HEAD_UNSET = object()

from .server_runtime.session_persistence import (  # noqa: F401 -- server namespace, read as server.<name>
    _get_messages,
    _invalidate_messages,
    _hydrate_messages_from_db,
    _set_active_head,
    _emit_running_task_event,
    _emit_branch_running_task_event,
    _save_session,
    _default_agent_id,
)
from .server_runtime.broadcasts import (  # noqa: F401 -- server namespace, read as server.<name>
    _broadcast,
    _broadcast_to_principal,
    _log,
)
from .server_runtime.session_lifecycle import (  # noqa: F401 -- server namespace, read as server.<name>
    _cleanup_session_resources,
    _stop_session_work,
    _get_or_create_session,
    _canonical_foreground_task,
    _is_run_active,
    _is_selected_run_active,
    _side_branch_tasks,
    _any_branch_run_active,
    _branch_run_active,
    _running_branches,
    _try_reserve_branch_run,
    _set_branch_run_execution,
    _slot_location,
    _slot_branch,
    _emit_slot_state,
    _selected_branch,
    switch_branch,
    _run_identity,
    _try_reserve_run,
    reserve_operation,
    repeated_request_refusal,
    reserve_branch_operation,
    reserve_branches_operation,
    release_branches_operation,
    branches_through,
    running_branch_among,
    cancel_operation,
    _try_reserve_run_locked,
    _release_run_reservation,
    _activate_run_reservation,
    _finish_owned_run,
    _take_owned_run,
    _release_session_occupancy_for_execution,
)
from .server_runtime.context_stats import (  # noqa: F401 -- server namespace, read as server.<name>
    _append_msg,
    _broadcast_context_stats,
    _resolve_context_window,
    _build_context_occupancy,
    _conv_context_window,
    _conv_model,
    session_context_stats,
    refresh_context_stats,
    _broadcast_chat_response,
)
from .server_runtime.websocket_dispatch import (  # noqa: F401 -- server namespace, read as server.<name>
    _send_operation_error,
    _websocket_handler,
    _build_ws_action_registry,
    _valid_uuid_request_id,
    _validate_file_request,
    _handle_ws_command,
)


# Global default providers (used when creating new conversations)
# (Provider state moved to openprogram.interfaces.server.runtime_management)

# Follow-up answer queues — keyed by conversation ID. When a function calls
# ask_user(), the handler puts the question on WebSocket and blocks on this
# queue. The frontend sends the answer back via WebSocket.
_follow_up_queues: dict = {}
_follow_up_lock = threading.Lock()
_FOLLOW_UP_DISCONNECTED = object()

# The run slots of every conversation branch, one table keyed by
# (session_id, branch_key); each entry is the branch's foreground operation
# (server_runtime/run_slots.py). ``_running_tasks`` is its view by session
# (the selected branch) and ``_branch_running_tasks`` its view by
# (session_id, branch_id) for the other branches; guarded by
# ``_running_tasks_lock``.
from .server_runtime.run_slots import BranchSlots as _BranchSlots  # noqa: E402
from .server_runtime.run_slots import SelectedSlots as _SelectedSlots  # noqa: E402

_run_slots: dict = {}
_running_tasks = _SelectedSlots(_run_slots)  # session_id → {msg_id, op_kind, op_id, ...}
_branch_running_tasks = _BranchSlots(_run_slots)
_running_tasks_lock = threading.Lock()

# Every frame that reports whether a session is running carries a
# ``run_seq`` taken before it read that state. Frames reach a client out of
# order (a session load reads the state, then spends a while building the
# transcript while a clear is broadcast), so the client applies a frame only
# if its run_seq is newer than the last one it applied for that session.
# Microsecond wall time keeps the order across worker restarts and stays
# below 2**53, so the client's JSON numbers hold it exactly.
_run_seq_lock = threading.Lock()
_last_run_seq = 0


def _next_run_seq() -> int:
    global _last_run_seq
    with _run_seq_lock:
        _last_run_seq = max(_last_run_seq + 1, time.time_ns() // 1000)
        return _last_run_seq


# ---------------------------------------------------------------------------
# Follow-up context manager — shared by run / edit / any command handler
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Runtime / provider management lives in openprogram.interfaces.server.runtime_management
# ---------------------------------------------------------------------------
from openprogram.interfaces.server import runtime_management
from openprogram.interfaces.server.runtime_management import (  # noqa: F401 -- server namespace, read as server.<name>
    _create_runtime_for_visualizer,
    _detect_default_provider,
    _init_providers,
    _get_session_runtime,
    _switch_runtime,
    _get_provider_info,
)


from openprogram.interfaces.server import persistence as _persist  # noqa: F401 -- server namespace, read as server.<name>


def _load_config() -> dict:
    """Load config from ~/.openprogram/config.json.

    Delegates to the single canonical reader in ``openprogram.state.config`` so the
    web and CLI never diverge on read/error-handling policy."""
    from openprogram.state import config as user_config
    return user_config.read_config()


def _get_api_key(env_var: str) -> str:
    """Get a search/TTS key from the environment (injected from
    config.json ``api_keys`` by ``_apply_config_keys`` below). LLM
    provider keys do NOT resolve here — use ``_llm_is_configured`` /
    the AuthStore resolvers."""
    return os.environ.get(env_var) or ""


def _llm_is_configured(provider_id: str) -> bool:
    """AuthStore-backed configured check for an LLM provider."""
    from openprogram.models.providers.settings.env_api_keys import is_configured
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
    def _codex_available() -> bool:
        # The Codex provider needs OAuth credentials, NOT the `codex` CLI
        # binary itself. The binary is only used once for `codex login`,
        # after which OpenProgram reads ~/.codex/auth.json (or the
        # adopted copy in the active OpenProgram profile's auth store)
        # and talks directly to chatgpt.com/backend-api — no proxy, no
        # shell-out to `codex`.
        from pathlib import Path
        if (Path.home() / ".codex" / "auth.json").exists():
            return True
        from openprogram.state.paths import get_state_dir
        if (get_state_dir() / "auth" / "openai-codex" / "default.json").exists():
            return True
        return False

    checks = [
        # (name, label, available_check, env_keys_for_config_or_None_if_CLI)
        ("openai-codex", "OpenAI Codex", _codex_available, None),
        ("gemini-cli", "Gemini CLI", lambda: shutil.which("gemini") is not None, None),
        ("anthropic", "Anthropic API", lambda: _llm_is_configured("anthropic"), ["ANTHROPIC_API_KEY"]),
        ("openai", "OpenAI API", lambda: _llm_is_configured("openai"), ["OPENAI_API_KEY"]),
        ("gemini", "Gemini API", lambda: _llm_is_configured("google"), ["GOOGLE_API_KEY"]),
    ]
    for name, label, check, env_keys in checks:
        available = check()
        result.append({
            "name": name,
            "label": label,
            "available": available,
            "active": name == runtime_management._default_provider,
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
        from openprogram.runtime.agent_registry import manager as _A
        from openprogram.runtime.agent_registry.manager import sessions_dir
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


from openprogram.interfaces.server.function_catalog import (  # noqa: F401 -- server namespace, read as server.<name>
    _discover_functions,
    _extract_function_info,
    _load_function,
)


# ---------------------------------------------------------------------------
# Function discovery and loading live in openprogram.interfaces.server.function_catalog.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Conversation management — each conversation is a DAG in SessionDB
# ---------------------------------------------------------------------------


# One wording for every "you can't move HEAD right now" rejection, so
# retry / edit / fn-dispatch / checkout / delete / attach / merge /
# rewind all read identically in the UI.
RUN_ACTIVE_ERROR = (
    "a run is currently active in this session — wait for it to finish "
    "or stop it first"
)


# DAG helpers live in openprogram.runtime.context.git. We keep ``advance_head``
# as the in-memory mutation primitive but wrap it in ``_append_msg``
# below so every webui write also flows into SessionDB. That makes the
# dispatcher / channels worker / TUI see writes from the webui WS
# handlers without waiting for the next ``_save_session``.
from openprogram.runtime.context.git import (  # noqa: E402, F401 -- server namespace, read as server.<name>
    advance_head as _raw_advance_head,
    head_or_tip as _head_or_tip,
    linear_history as _linear_history,
)


# Thinking-effort picker configs + runtime apply helpers live in
# thinking_effort.py. Re-exported here for existing call sites.
from openprogram.interfaces.server.thinking_effort import (  # noqa: E402, F401 -- server namespace, read as server.<name>
    apply_thinking_effort as _apply_thinking_effort,
    get_thinking_config_for_model as _get_thinking_config_for_model,
)


from openprogram.interfaces.server.chat_input import (  # noqa: F401 -- server namespace, read as server.<name>
    parse_chat_input as _parse_chat_input,
)


# ---------------------------------------------------------------------------
# WebSocket handler (module-level to avoid FastAPI closure issues)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# WebSocket command handler (module-level so _websocket_handler can call it)
# ---------------------------------------------------------------------------


WS_ACTIONS: dict = _build_ws_action_registry()

# The Server shows the live sessions, so it is this process's interface link.
from openprogram.runtime import interface_link as _core_interface_link  # noqa: E402
from .server_runtime import interface_link as _interface_link  # noqa: E402

_core_interface_link.register(_interface_link)

# Turns the runtime starts on its own take the same run slot as chat.
from openprogram.runtime.conversation.run_control import run_slot as _run_slot  # noqa: E402
from .server_runtime.session_lifecycle import ServerRunSlot as _ServerRunSlot  # noqa: E402

_run_slot.register_run_slot(_ServerRunSlot())

# The chat channels and speech the Server serves register their core links
# (``openprogram.runtime.channel_link`` / ``speech_link``) on import.
import openprogram.interfaces.channels  # noqa: E402,F401
import openprogram.interfaces.speech  # noqa: E402,F401

_FILE_REQUEST_ACTIONS = frozenset({
    "project_file_tree", "project_file_search", "project_file_read",
    "project_file_info", "project_folder_size",
    "project_file_operation_status",
    "project_file_write", "project_file_create", "project_file_rename",
    "project_file_copy", "project_file_delete", "project_file_reveal",
    "review_scope", "review_file_diff", "review_revert_hunk",
    "turn_history_state", "revert_turn", "reapply_turn",
    "turn_operation_status",
})
_FILE_MUTATION_ACTIONS = frozenset({
    "project_file_write", "project_file_create", "project_file_rename",
    "project_file_copy", "project_file_delete", "revert_turn", "reapply_turn",
    "review_revert_hunk",
})


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def _web_config() -> dict:
    """Return the configured bind host and explicit browser origins."""
    bind_host, allowed_origins = "127.0.0.1", ()
    try:
        from openprogram.state.config import read_config
        web = read_config().get("web") or {}
        bind_host = str(web.get("host") or "127.0.0.1")
        raw = web.get("allowed_origins") or ()
        allowed_origins = (
            tuple(str(origin) for origin in raw)
            if isinstance(raw, (list, tuple))
            else ()
        )
    except Exception as e:  # noqa: BLE001 — an unreadable config must not
        _log(f"[web] config unreadable, defaulting to loopback: {e}")
    return {
        "bind_host": bind_host,
        "allowed_origins": allowed_origins,
    }


async def _recover_execution_control() -> None:
    """Recover executions and start applying their events."""
    from openprogram.runtime.execution import default_store, recover_execution_startup
    from openprogram.runtime.execution.event_effects import start_event_effects

    result = await asyncio.to_thread(
        recover_execution_startup,
    )
    if result.canonical:
        _log(f"[startup] recovered {len(result.canonical)} canonical execution(s)")
    start_event_effects(default_store())


async def _finish_open_permissions_migration() -> None:
    """Executions are recovered now, so an approval the new policy drops can resume."""
    from openprogram.operations.migrations.open_permissions import resume_open_approvals

    try:
        await resume_open_approvals()
    except Exception as exc:  # noqa: BLE001 - retried at the next start
        _log(f"[startup] open permissions migration not finished: {type(exc).__name__}: {exc}")


def create_app(*, owner_auth=None, port: int = 18100):
    """Create the FastAPI application with mandatory owner authentication."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    async def _capture_loop():
        global _loop
        _loop = asyncio.get_running_loop()

    async def _subscribe_event_bus():
        """webui 降级为总线订阅者（framework-evolution.md 步 4）。

        外部源（job runner / channels / worktree / functions watcher /
        sub_agent）不再 import 本模块的 _broadcast；它们 emit `ws.frame`
        事件，本订阅者把原始帧原样广播给前端——前端零改动。

        订阅在 _capture_loop 之后挂，确保 _broadcast 依赖的 _loop 已就位。
        emit 发生在源所在线程（可能是 worker），_broadcast 内部用
        run_coroutine_threadsafe 跨线程投递，安全。
        """
        try:
            from openprogram.runtime.events import get_event_bus, WS_FRAME_EVENT

            def _forward(event):
                try:
                    frame = event.payload.get("frame")
                    if frame is not None:
                        _broadcast(json.dumps(frame, default=str))
                except Exception:
                    # Bus subscriber: a raise here would reach the emitting
                    # source.
                    logger.debug("ws.frame forward failed", exc_info=True)

            get_event_bus().subscribe(_forward, types={WS_FRAME_EVENT})
        except Exception as e:  # noqa: BLE001
            _log(f"[startup] event-bus WS forwarder failed: {e}")
        try:
            from openprogram.interfaces.server.transcript import publisher as transcript_publisher
            from openprogram.interfaces.server.ws_delivery import send_to_connection

            transcript_publisher.start(lambda ws, payload: send_to_connection(ws, payload, _loop))
        except Exception as e:  # noqa: BLE001
            _log(f"[startup] transcript publisher failed: {e}")

    async def _reconcile_interrupted_runs():
        """Flip DAG nodes frozen at status='running' (a previous worker
        was killed mid-run) to 'error'. See webui/_exec_dag.py."""
        def _run_legacy_recovery() -> None:
            try:
                n = reconcile_interrupted_runs()
                if n:
                    _log(f"[startup] reconciled {n} interrupted run node(s)")
            except Exception as e:  # noqa: BLE001
                _log(f"[startup] reconcile_interrupted_runs failed: {e}")

        threading.Thread(
            target=_run_legacy_recovery,
            name="openprogram-legacy-dag-recovery",
            daemon=True,
        ).start()

        def _restore_channel_aliases() -> None:
            # Chats tied to a channel account before the chip wrote routing
            # aliases get theirs, so the account's messages reach them.
            try:
                from openprogram.interfaces.server.ws_actions.chat.conversation_channel import (
                    restore_channel_aliases,
                )
                from openprogram.interfaces.server import server as _server
                n = restore_channel_aliases(_server)
                if n:
                    _log(f"[startup] restored {n} channel routing alias(es)")
            except Exception as e:  # noqa: BLE001
                _log(f"[startup] restore_channel_aliases failed: {e}")

        threading.Thread(
            target=_restore_channel_aliases,
            name="openprogram-channel-alias-restore",
            daemon=True,
        ).start()

    mcp_startup_task = None

    async def _load_mcp_servers():
        """Spawn every enabled MCP server from ``mcp_servers.json``.

        Each server's ``tools/list`` output is registered as AgentTool
        entries (namespaced ``{server}__{tool}``). Failures are non-
        fatal — a misconfigured server logs and the worker keeps
        booting.
        """
        try:
            from openprogram.extensions.mcp import load_mcp_servers
            await load_mcp_servers()
        except Exception as e:  # noqa: BLE001
            _log(f"[mcp] startup failed: {type(e).__name__}: {e}")

    async def _start_mcp_servers():
        nonlocal mcp_startup_task
        from openprogram.operations.worker.runner import trial_mode
        if trial_mode():
            return
        mcp_startup_task = asyncio.create_task(
            _load_mcp_servers(), name="openprogram-mcp-startup",
        )

    async def _load_plugins():
        """Load every enabled plugin: its tools, hooks, prompt sections,
        MCP servers and Web modules come back after a restart. A plugin
        that fails stays enabled with its error on the Plugins page."""
        try:
            from openprogram.extensions.plugins import loader as plugin_loader
            from openprogram.programs._host.context import set_host_loop
            set_host_loop(asyncio.get_running_loop())
            failures = await asyncio.to_thread(plugin_loader.load_enabled)
            for name, error in failures.items():
                _log(f"[plugins] {name} failed to load: {error}")
        except Exception as e:  # noqa: BLE001
            _log(f"[plugins] startup failed: {type(e).__name__}: {e}")

    async def _retire_agent_workspace_files():
        """Per-agent AGENTS.md / SOUL.md / USER.md / TOOLS.md are no longer
        read: untouched templates go, edited ones move into system_prompt."""
        try:
            from openprogram.runtime.agent_registry.workspace import retire_all
            await asyncio.to_thread(retire_all)
        except Exception as e:  # noqa: BLE001
            _log(f"[agents] retiring workspace files failed: {type(e).__name__}: {e}")

    async def _start_skills_watcher():
        """Watch the five skill source directories and push ``skills:changed``
        to all connected WS clients whenever a SKILL.md file is added, edited
        or removed. Falls back to 5-second polling if ``watchdog`` isn't
        installed."""
        try:
            from openprogram.extensions.skills.watcher import start_watcher
            def _broadcast_changed():
                # 事件层 tap（B 类：技能文件变了）。放 _broadcast 之前——
                # watcher 线程里 _broadcast 可能抛错被上层吞掉，emit 先行。
                from openprogram.runtime.events import emit_safe
                emit_safe("skills.changed", "system")
                _broadcast(json.dumps({"type": "skills:changed"}))
            start_watcher(on_change=_broadcast_changed)
        except Exception as e:  # noqa: BLE001
            _log(f"[skills-watcher] startup failed: {type(e).__name__}: {e}")

    async def _start_plugin_autoupdate():
        """Periodically poll PyPI / npm for newer versions of installed
        plugins. Result is broadcast over WS as ``plugins:update_available``
        so the Plugins UI can badge upgradable rows."""
        from openprogram.operations.worker.runner import trial_mode
        if trial_mode():
            return
        try:
            from openprogram.extensions.plugins import autoupdate as _au
            def _broadcast_updates(payload: dict):
                try:
                    _broadcast(json.dumps({
                        "type": "plugins:update_available",
                        "data": payload,
                    }))
                except Exception:
                    # Watcher thread callback: an exception would die
                    # unobserved.
                    logger.debug(
                        "plugins:update_available broadcast failed", exc_info=True)
                # 事件层 tap（B 类：插件有新版）
                from openprogram.runtime.events import emit_safe
                emit_safe("plugins.update_available", "system",
                          {"count": len(payload or {})})
            _au.register_callback(_broadcast_updates)
            _au.start()
        except Exception as e:  # noqa: BLE001
            _log(f"[plugin-autoupdate] startup failed: {type(e).__name__}: {e}")

    async def _start_release_check():
        """Remember once a day whether a newer release exists."""
        from openprogram.operations.worker.runner import trial_mode
        if trial_mode():
            return
        try:
            from openprogram.operations.self_update.github_release import availability
            availability.start()
        except Exception as e:  # noqa: BLE001
            _log(f"[release-check] startup failed: {type(e).__name__}: {e}")

    async def _stop_mcp_servers():
        if mcp_startup_task is not None:
            if not mcp_startup_task.done():
                mcp_startup_task.cancel()
            try:
                await mcp_startup_task
            except asyncio.CancelledError:
                pass
        try:
            from openprogram.extensions.mcp import shutdown_mcp_servers
            await shutdown_mcp_servers()
        except Exception as e:  # noqa: BLE001
            _log(f"[mcp] shutdown failed: {type(e).__name__}: {e}")

    async def _stop_execution_event_effects():
        from openprogram.runtime.execution.event_effects import stop_event_effects

        stop_event_effects()

    # Startup runs in listed order, shutdown in reverse — same semantics
    # the eight @app.on_event handlers had before the deprecated API was
    # dropped. _capture_loop MUST stay first: everything downstream that
    # broadcasts depends on the module-global _loop it captures.
    async def _install_builtin_agents():
        # The built-in Agents (Assistant first, as the default) this
        # instance has never installed. The App never runs the CLI setup
        # wizard, so this is where a fresh install gets them.
        from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
        try:
            create_builtin_agents()
        except Exception:
            logger.warning("built-in Agents were not installed", exc_info=True)

    _STARTUP = (
        _capture_loop,
        _subscribe_event_bus,
        _install_builtin_agents,
        _recover_execution_control,
        _finish_open_permissions_migration,
        _reconcile_interrupted_runs,
        _start_mcp_servers,
        _load_plugins,
        _retire_agent_workspace_files,
        _start_skills_watcher,
        _start_plugin_autoupdate,
        _start_release_check,
    )
    _SHUTDOWN = (_stop_mcp_servers, _stop_execution_event_effects)

    @asynccontextmanager
    async def _lifespan(_app):
        for hook in _STARTUP:
            await hook()
        from openprogram.state.memory.checkpoints import run_checkpoints
        checkpoint_stop = asyncio.Event()
        checkpoint_task = asyncio.create_task(run_checkpoints(checkpoint_stop))
        from openprogram.state.store.project.discovery import run_discovery
        discovery_task = asyncio.create_task(run_discovery(
            checkpoint_stop, lambda: _broadcast(json.dumps({"type": "projects_changed", "data": {}}))))
        from openprogram.runtime.conversation.process_notices import run_exit_notices
        notices_task = asyncio.create_task(run_exit_notices(checkpoint_stop))
        from openprogram.runtime.jobs.notices import run_job_notices
        job_notices_task = asyncio.create_task(run_job_notices(checkpoint_stop))
        from openprogram.interfaces.server.frontend import watch_frontend
        frontend_task = asyncio.create_task(watch_frontend(
            checkpoint_stop, lambda build: _broadcast(json.dumps({"type": "frontend_build", "data": {"id": build}}))))
        try:
            yield
        finally:
            checkpoint_stop.set()
            await checkpoint_task
            await discovery_task
            await notices_task
            await job_notices_task
            await frontend_task
            for hook in reversed(_SHUTDOWN):
                await hook()

    app = FastAPI(
        title="Agentic Visualizer",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.interfaces.server.routes.files.office_assets import office_editor
    if owner_auth is None:
        import secrets
        from openprogram.runtime.permissions.authority import owner_principal_id

        _web_cfg = _web_config()
        owner_auth = OwnerAuthState.from_raw_token(
            secrets.token_bytes(32),
            owner_principal_id=owner_principal_id(),
            bind_host=_web_cfg["bind_host"],
            port=port,
            allowed_origins=_web_cfg["allowed_origins"],
        )
    app.state.owner_auth = owner_auth
    _office = office_editor()
    if _office is not None:
        _office.load()  # verify the installed Office pack once, at startup
    app.add_middleware(
        OwnerAuthMiddleware,
        auth_state=owner_auth,
        office=office_editor,
    )

    # Auth v2 REST + SSE routes. Kept in a dedicated module so server.py
    # doesn't accumulate more authentication state than it already has.
    from openprogram.interfaces.server.routes.identity.credentials import router as _auth_router
    app.include_router(_auth_router)

    # Frontend: the Next.js static export (apps/web/out/) is served by this
    # same process — mount_frontend() is called LAST below so every API
    # route registered here wins over the SPA catch-all.

    # The previous boot-time refresh of `claude_models.json` relied on
    # the now-removed Claude Code CLI runtime to enumerate models. The
    # static catalog shipped with the repo is the source of truth now;
    # update it via `tools/scripts/refresh_claude_models.py` (offline)
    # if Anthropic ships a new model family.

    # No HTML routes — frontend lives in web/ (Next.js on :18100).

    # WebSocket — use Starlette's raw WebSocketRoute to avoid FastAPI routing issues
    from starlette.routing import WebSocketRoute
    app.routes.insert(0, WebSocketRoute("/ws", _websocket_handler))

    # GET /files/raw — raw project-file bytes (images / downloads) for the
    # files panel. Same _resolve guard as the project_file_* WS actions.
    from fastapi.responses import FileResponse, Response as _PlainResponse

    @app.get("/files/raw")
    async def _files_raw(project_id: str = "", path: str = ""):
        import mimetypes
        from openprogram.interfaces.server.ws_actions.files import _resolve
        target, error = _resolve(project_id, path)
        if target is None:
            status = 403 if error == "path escapes project root" else 404
            return _PlainResponse(error, status_code=status, media_type="text/plain")
        if not os.path.isfile(target):
            return _PlainResponse("not found", status_code=404, media_type="text/plain")
        # Untrusted repo bytes: never let the browser sniff or execute them.
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        }
        guessed = mimetypes.guess_type(target)[0]
        if guessed and guessed.startswith("image/"):
            # Real type kept so the panel's <img> works; CSP sandbox still
            # neuters the file if it's opened as a top-level document.
            return FileResponse(target, media_type=guessed, headers=headers)
        if guessed == "application/pdf":
            # Inline, real content-type, and deliberately NO CSP sandbox
            # header: a sandboxed response blocks the browser's built-in
            # PDF viewer. The PDF renders in the browser's isolated
            # viewer, not the page DOM, so sandboxing buys nothing here.
            # No Content-Disposition either — attachment would force a
            # download instead of the <iframe> render.
            return FileResponse(target, media_type=guessed,
                                headers={"X-Content-Type-Options": "nosniff"})
        # Everything else is a download, never rendered by the browser.
        return FileResponse(target, media_type="application/octet-stream",
                            filename=os.path.basename(target), headers=headers)

    # REST endpoints
    # Read-only catalog routes (tree, functions, tokens, programs meta)
    from openprogram.interfaces.server.routes.files import tree as _routes_tree
    _routes_tree.register(app)

    # POST /api/programs/refresh — re-scan agentics/ for newly-installed
    # programs (manual "refresh" button; same core the watcher uses).
    from openprogram.interfaces.server.routes.catalog import programs as _routes_programs
    _routes_programs.register(app)

    # /api/chat/branch, /api/function/{name} — routes.chat
    from openprogram.interfaces.server.routes import chat as _routes_chat
    _routes_chat.register(app)
    from openprogram.interfaces.server.routes import visual_frame as _routes_visual_frame
    _routes_visual_frame.register(app)
    # /v1/models, /v1/chat/completions: an Agent as a model for other tools.
    from openprogram.interfaces.server.routes import openai_compatible as _routes_openai
    _routes_openai.register(app)

    # Retry / Edit / Checkout routes live in routes/chat_branches.py — see
    # docs/design/context/overview.md. Keeping them out of this module keeps
    # it under control.
    from openprogram.interfaces.server.routes.chat_branches import router as _chat_router
    app.include_router(_chat_router)

    # Workdir picker, browse, history, canvas — registered from routes.workdir
    from openprogram.interfaces.server.routes.files import workdir as _routes_workdir
    _routes_workdir.register(app)

    # @file mention support — composer search + single-file read.
    from openprogram.interfaces.server.routes.files import file_search as _routes_file_search
    _routes_file_search.register(app)
    from openprogram.interfaces.server.routes.files import archives as _routes_archives
    _routes_archives.register(app)

    # Pause / Resume / Stop — routes.lifecycle
    from openprogram.interfaces.server.routes.execution import lifecycle as _routes_lifecycle
    _routes_lifecycle.register(app)

    # Activity's second level: a branch's runs and what one run did.
    from openprogram.interfaces.server.routes.execution import activity_runs as _routes_activity_runs
    _routes_activity_runs.register(app)

    # Durable Goal projection and pause/edit/resume/cancel actions.
    from openprogram.interfaces.server.routes.execution import goal as _routes_goal
    _routes_goal.register(app)

    # Pending user-input questions list/reply/reject — routes.questions
    # (REST parity for runtime.ask; WS is the live path). Reconnect recovery.
    from openprogram.interfaces.server.routes.execution import questions as _routes_questions
    _routes_questions.register(app)

    # /api/providers, /api/provider/{name}, /api/models — routes.runtime
    from openprogram.interfaces.server.routes.execution import runtime as _routes_runtime
    _routes_runtime.register(app)

    # Model catalog (LobeChat-style settings) — routes.providers
    from openprogram.interfaces.server.routes.identity import providers as _routes_providers
    _routes_providers.register(app)

    from openprogram.interfaces.server.routes.settings import usage as _routes_usage
    _routes_usage.register(app)

    from openprogram.interfaces.server.routes.settings import routing as _routes_routing
    _routes_routing.register(app)

    # Voice input: speech to text for the composer microphone.
    from openprogram.interfaces.server.routes.settings import voice as _routes_voice
    _routes_voice.register(app)

    # Recording a demonstration: start, stop, narration and screenshots.
    from openprogram.interfaces.server.routes.execution import recordings as _routes_recordings
    _routes_recordings.register(app)

    # Meetings: call audio from the App, transcribed and summarized.
    from openprogram.interfaces.server.routes.execution import meetings as _routes_meetings
    _routes_meetings.register(app)

    # Global running-work snapshot for the right-sidebar Running panel.
    from openprogram.interfaces.server.routes.execution import running as _routes_running
    _routes_running.register(app)

    from openprogram.interfaces.server.routes import self_updates as _routes_self_updates
    _routes_self_updates.register(app)

    from openprogram.interfaces.server.routes.settings import versions as _routes_versions
    _routes_versions.register(app)

    from openprogram.interfaces.server.routes.identity import provider_login as _routes_provider_login
    _routes_provider_login.register(app)

    # Generic per-provider account management (/api/providers/{id}/accounts/*).
    # Registered AFTER providers.py so its literal /claude-code/accounts routes
    # match first; this module serves every other provider from the AuthStore.
    from openprogram.interfaces.server.routes.identity import accounts as _routes_accounts
    _routes_accounts.register(app)

    # /api/config GET/POST registered from routes.config
    from openprogram.interfaces.server.routes.settings import config as _routes_config
    _routes_config.register(app)

    # Function source / editor + node lookup — routes.functions
    from openprogram.interfaces.server.routes.catalog import functions as _routes_functions
    _routes_functions.register(app)

    # Memory API — routes registered from openprogram.interfaces.server.routes.settings.memory
    from openprogram.interfaces.server.routes.settings import memory as _routes_memory
    _routes_memory.register(app)

    from openprogram.interfaces.server.routes.files import documents as _routes_documents
    _routes_documents.register(app)

    # POST /api/files/upload — files dropped into the file tree
    from openprogram.interfaces.server.routes.files import upload as _routes_upload
    _routes_upload.register(app)

    # Scheduler task CRUD — independent from Memory; tasks may hold read-only
    # MemoryRefs but their lifecycle is owned here.
    from openprogram.interfaces.server.routes.execution import scheduler as _routes_scheduler
    _routes_scheduler.register(app)

    # A conversation's always-on state, open work and routines (Activity panel).
    from openprogram.interfaces.server.routes.execution import conversation_work as _routes_conversation_work
    _routes_conversation_work.register(app)
    # The Agent a conversation uses (composer Agent menu).
    from openprogram.interfaces.server.routes.execution import session_agent as _routes_session_agent
    _routes_session_agent.register(app)

    # /api/sessions/{id}/export — download a session as Markdown / HTML
    from openprogram.interfaces.server.routes.files import export as _routes_export
    _routes_export.register(app)

    from openprogram.interfaces.server.routes.settings import misc as _routes_misc
    _routes_misc.register(app)

    # /api/agents — agent list (used by settings/channels binding picker)
    from openprogram.interfaces.server.routes.catalog import agents as _routes_agents
    _routes_agents.register(app)

    # /api/channels/{platform}/{account_id}/status — adapter heartbeat
    from openprogram.interfaces.server.routes.settings import channels as _routes_channels
    _routes_channels.register(app)

    # /api/mcp/* — MCP server management (shared by webui / CLI / TUI)
    from openprogram.interfaces.server.routes.catalog import mcp as _routes_mcp
    _routes_mcp.register(app)

    # /api/skills/* — Skills management
    from openprogram.interfaces.server.routes.catalog import skills as _routes_skills
    _routes_skills.register(app)

    # /api/plugins/* — Plugins management
    from openprogram.interfaces.server.routes.catalog import plugins as _routes_plugins
    _routes_plugins.register(app)
    from openprogram.interfaces.server.routes.catalog import applications as _routes_applications
    _routes_applications.register(app)
    from openprogram.interfaces.server.routes.control import resources, framework
    resources.register(app)
    framework.register(app)
    from openprogram.interfaces.server.routes.files import office_assets as _routes_office_assets
    _routes_office_assets.register(app)

    # /api/commands/* — Unified slash-command registry (Phase 1)
    from openprogram.interfaces.server.routes.catalog import commands as _routes_commands
    _routes_commands.register(app)

    # /docs — static design-documentation site (built into docs/_site/)
    from openprogram.interfaces.server.routes import docs as _routes_docs
    _routes_docs.register(app)

    # Frontend static export + SPA fallback — must stay the LAST
    # registration so its catch-all GET never shadows an API route.
    from openprogram.interfaces.server.frontend import mount_frontend
    mount_frontend(app)

    return app


# ---------------------------------------------------------------------------
# Server runner (in background thread)
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None
_uvicorn_server = None
_server_stopping = threading.Event()
_owner_auth_state = None


def start_server(port: int = 18100, open_browser: bool = False) -> threading.Thread:
    """
    Start the visualization server in a background daemon thread.

    Returns the thread object. The server runs until the process exits.
    """
    global _server_thread, _loop, _owner_auth_state

    if _server_thread is not None and _server_thread.is_alive():
        print("Visualizer already running")  # noqa: T201 - terminal status
        return _server_thread

    _server_stopping.clear()
    from openprogram.models.providers.catalog.initialization import initialize_provider_runtime

    initialize_provider_runtime()

    from openprogram.state.paths import get_state_dir
    from openprogram.interfaces.server.owner_auth import OwnerAuthState

    _web_cfg = _web_config()
    _owner_auth_state = OwnerAuthState.start(
        state_dir=get_state_dir(),
        bind_host=_web_cfg["bind_host"],
        port=port,
        allowed_origins=_web_cfg["allowed_origins"],
    )

    # No conversation is read at start-up: each is read when first asked
    # for (``_get_or_create_session``), so opening one never waits behind a
    # walk of every other conversation's log.

    def _run():
        global _loop, _uvicorn_server
        try:
            import uvicorn
        except ImportError:
            raise ImportError(
                "uvicorn is required for the web UI. "
                "Reinstall the complete OpenProgram release."
            )

        try:
            app = create_app(owner_auth=_owner_auth_state, port=port)
            _host = _owner_auth_state.bind_host
            config = uvicorn.Config(
                app, host=_host, port=port,
                log_level="warning",
                access_log=False,
                # Up to 64 MiB of saved attachments plus 20 MiB of image
                # send versions, base64 encoded; the chat handler enforces
                # the decoded per-file and per-turn budgets.
                ws_max_size=128 * 1024 * 1024,
                proxy_headers=False,
                timeout_graceful_shutdown=1.0,
            )
            server = uvicorn.Server(config)
            _uvicorn_server = server
            if _server_stopping.is_set():
                server.should_exit = True
            _loop = asyncio.new_event_loop()
            from openprogram.host._platform.asyncio import install_asyncio_exception_handler

            install_asyncio_exception_handler(_loop)
            asyncio.set_event_loop(_loop)
            _loop.run_until_complete(server.serve())
        finally:
            if _loop is not None and not _loop.is_closed():
                pending = asyncio.all_tasks(_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    _loop.run_until_complete(asyncio.wait(pending, timeout=0.5))
                _loop.run_until_complete(_loop.shutdown_asyncgens())
                _loop.close()
            _uvicorn_server = None
            if _owner_auth_state is not None:
                _owner_auth_state.close()

    _server_thread = threading.Thread(target=_run, daemon=True, name="openprogram-visualizer")
    _server_thread.start()

    url = f"http://localhost:{port}"
    from urllib.parse import urlsplit
    from openprogram.host.security.backend_endpoint import is_loopback_host

    binding_scope = (
        "loopback"
        if is_loopback_host(_owner_auth_state.bind_host)
        else "external"
    )
    print(  # noqa: T201 - startup status
        f"OpenProgram API bind={_owner_auth_state.bind_host}:{port} "
        f"binding_scope={binding_scope} "
        f"allowed_origins={sorted(_owner_auth_state.effective_origins)} "
        "forwarded_proto_trust=loopback-peer-only "
        f"token_fingerprint={_owner_auth_state.fingerprint}"
    )
    plaintext_remote_origins = [
        origin
        for origin in sorted(_owner_auth_state.effective_origins)
        if urlsplit(origin).scheme == "http"
        and not is_loopback_host(urlsplit(origin).hostname or "")
    ]
    if plaintext_remote_origins:
        logger.warning("owner token traffic uses unencrypted HTTP for %s",
                       ", ".join(plaintext_remote_origins))

    if open_browser:
        ui_url = (
            url
            if url in _owner_auth_state.effective_origins
            else sorted(_owner_auth_state.effective_origins)[0]
        )

        def _open():
            import time

            from openprogram.host._platform.environment import open_browser_url
            from openprogram.operations.worker.ports import backend_accepts_owner_challenge
            from openprogram.host.security.backend_endpoint import build_owner_auth_url

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if backend_accepts_owner_challenge(port):
                    opened = open_browser_url(build_owner_auth_url(
                        ui_url,
                        token=_owner_auth_state.token,
                        effective_origins=_owner_auth_state.effective_origins,
                    ))
                    if not opened:
                        print(  # noqa: T201 - terminal status
                            "Browser not opened: no graphical browser is available"
                        )
                    return
                time.sleep(0.2)
            print("Browser not opened: Web listener ownership was not verified")  # noqa: T201 - terminal status
        threading.Thread(target=_open, daemon=True).start()

    return _server_thread


def stop_server(timeout: float = 2.0) -> bool:
    """Stop admitting work and drain the server before the worker exits."""
    _server_stopping.set()
    server, loop, thread = _uvicorn_server, _loop, _server_thread
    if server is not None and loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(setattr, server, "should_exit", True)
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    return thread is None or not thread.is_alive()
