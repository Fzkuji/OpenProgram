"""Runtime / misc WS actions: list_models, switch_model, browser, stop,
stats, sync. Mirrors several REST endpoints for ws-only clients (the
Ink CLI) plus the reconnect-sync handshake.
"""
from __future__ import annotations

import asyncio
import json

# WELCOME_STATS_SESSION_LIMIT lives on the server module — we read it lazily.


async def handle_list_models(ws, cmd: dict):
    from openprogram.webui import server as _s
    try:
        with _s._runtime_management._runtime_lock:
            if _s._runtime_management._default_provider is None:
                (_s._runtime_management._default_provider,
                 _s._runtime_management._default_runtime) = _s._detect_default_provider()
        provider = _s._runtime_management._default_provider or "none"
        runtime = _s._runtime_management._default_runtime
        current = runtime.model if runtime else None
        models: list[str] = []
        if runtime and hasattr(runtime, "list_models"):
            try:
                models = list(runtime.list_models())
            except Exception:
                models = []
        if current and current not in models:
            models = [current] + models
    except Exception:
        provider, current, models = "none", None, []
    await ws.send_text(json.dumps({
        "type": "models_list",
        "data": {"provider": provider, "current": current, "models": models},
    }, default=str))


async def handle_switch_model(ws, cmd: dict):
    """Same logic as POST /api/model, but over ws."""
    from openprogram.webui import server as _s
    try:
        model = (cmd.get("model") or "").strip()
        explicit_provider = (cmd.get("provider") or "").strip() or None
        session_id = cmd.get("session_id")
        if not model:
            await ws.send_text(json.dumps({
                "type": "error", "data": {"message": "Missing model"},
            }))
            return
        inferred_provider = None
        bare_model = model
        if explicit_provider is None and ":" in model:
            head, tail = model.split(":", 1)
            from openprogram.providers import get_providers as _get_providers
            known = set(_get_providers())
            known.update({"claude-code", "openai-codex", "gemini-cli",
                          "anthropic", "openai", "gemini"})
            if head in known:
                inferred_provider = head
                bare_model = tail
        target_provider = explicit_provider or inferred_provider

        async def _build_rt(provider: str):
            return await asyncio.to_thread(
                _s._create_runtime_for_visualizer, provider, bare_model,
            )

        if session_id:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
            if conv:
                old_rt = conv.get("runtime")
                cur_prov = conv.get(
                    "provider_name", _s._runtime_management._default_provider,
                )
                prov = target_provider or cur_prov
                need_new_rt = (
                    (target_provider and target_provider != cur_prov)
                    or (old_rt is None)
                )
                if need_new_rt:
                    new_rt = await _build_rt(prov)
                    if old_rt and hasattr(old_rt, "close"):
                        try: old_rt.close()
                        except Exception: pass
                    conv["runtime"] = new_rt
                    conv["provider_name"] = prov
                else:
                    old_rt.model = bare_model
                info = _s._get_provider_info(session_id)
                _s._broadcast(json.dumps(
                    {"type": "provider_changed", "data": info},
                ))
                await ws.send_text(json.dumps({
                    "type": "model_switched",
                    "data": {"provider": prov, "model": bare_model},
                }))
                return

        # No session_id → swap default runtime.
        if (
            target_provider
            and target_provider != _s._runtime_management._default_provider
        ):
            new_rt = await _build_rt(target_provider)
            if (
                _s._runtime_management._default_runtime
                and hasattr(_s._runtime_management._default_runtime, "close")
            ):
                try: _s._runtime_management._default_runtime.close()
                except Exception: pass
            _s._runtime_management._default_runtime = new_rt
            _s._runtime_management._default_provider = target_provider
        elif _s._runtime_management._default_runtime:
            _s._runtime_management._default_runtime.model = bare_model
        else:
            await ws.send_text(json.dumps({
                "type": "error", "data": {"message": "No active runtime"},
            }))
            return
        info = _s._get_provider_info()
        _s._broadcast(json.dumps({"type": "provider_changed", "data": info}))
        await ws.send_text(json.dumps({
            "type": "model_switched",
            "data": {
                "provider": target_provider or _s._runtime_management._default_provider,
                "model": bare_model,
            },
        }))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }))


async def handle_browser(ws, cmd: dict):
    """Proxy a single browser-tool verb (Ink CLI /browser command)."""
    verb = cmd.get("verb") or ""
    kwargs = cmd.get("args") or {}
    if not verb:
        await ws.send_text(json.dumps({
            "type": "browser_result",
            "data": {"verb": "", "result": "Error: `verb` is required."},
        }))
        return
    try:
        from openprogram.functions.tools.browser.browser import execute as _br_exec
        result = _br_exec(action=verb, **kwargs)
    except Exception as e:  # noqa: BLE001
        result = f"Error: {type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "browser_result",
        "data": {"verb": verb, "result": str(result)},
    }, default=str))


# Sessions for which a graceful stop has already been requested. A second
# stop click (or mode="force") on a still-running session escalates to a
# hard SIGKILL. Cleared when the turn ends.
_graceful_pending: set[str] = set()
_GRACEFUL_GRACE_S = 4.0  # how long a graceful stop has before auto-escalation


async def handle_stop(ws, cmd: dict):
    """Mirror /api/stop — stop the in-flight turn for a conv, two-stage.

    First stop = GRACEFUL: ask the @agentic_function subprocess to finish its
    current unit, save, and exit (so a long research/gui run keeps its
    partial artifacts + conclusion). Second stop on the same still-running
    session — or ``mode="force"`` — = HARD: SIGKILL the process group
    instantly (the escape hatch when the model hangs). A graceful stop that
    doesn't exit within the grace window auto-escalates to hard.

    Either way, subsequent chat-response broadcasts are gagged and any
    ``status=running`` rows are patched to ``cancelled``.
    """
    from openprogram.webui import server as _s
    # TUI sends conv_id, web composer sends session_id — accept both.
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    if not session_id:
        return

    force = (cmd.get("mode") == "force") or (session_id in _graceful_pending)

    if not force:
        # ---- Stage 1: graceful ----
        from openprogram.agent.process_runner import (
            request_graceful_stop, kill_active_subprocess,
            is_subprocess_alive,
        )
        asked = False
        try:
            asked = request_graceful_stop(session_id)
        except Exception:
            asked = False
        if asked:
            _graceful_pending.add(session_id)
            _s._broadcast(json.dumps({
                "type": "status", "paused": False, "stopping": True,
                "session_id": session_id,
            }))

            # Auto-escalate: if the child hasn't exited within the grace
            # window, hard-kill it (covers a wedged model that ignores the
            # cooperative checkpoints).
            async def _escalate():
                await asyncio.sleep(_GRACEFUL_GRACE_S)
                try:
                    if is_subprocess_alive(session_id):
                        kill_active_subprocess(session_id)
                        _s._kill_active_runtime(session_id)
                except Exception:
                    pass
                finally:
                    _graceful_pending.discard(session_id)
            try:
                asyncio.ensure_future(_escalate())
            except Exception:
                _graceful_pending.discard(session_id)
            return
        # No live subprocess to ask gracefully → fall through to hard stop
        # (e.g. a non-subprocess turn, or it already exited).

    # ---- Stage 2: hard ----
    _graceful_pending.discard(session_id)
    _s._mark_cancelled(session_id)
    _s.resume_execution()
    # SIGKILL the @agentic_function subprocess (if any) for this session
    # *before* signaling cooperative cancel paths. This is what makes
    # stop instantaneous for gui_agent / research_agent / wiki_agent —
    # the process group dies in milliseconds.
    try:
        from openprogram.agent.process_runner import kill_active_subprocess
        kill_active_subprocess(session_id)
    except Exception:
        pass
    _s._kill_active_runtime(session_id)
    with _s._follow_up_lock:
        q = _s._follow_up_queues.get(session_id)
    if q is not None:
        try:
            q.put_nowait({"_cancelled": True})
        except Exception:
            pass
    # 解除该 session 所有待答 runtime.ask 问题（按 declined 处理），
    # 否则阻塞在 ask 上的函数会一直等到 timeout。
    try:
        from openprogram.agent.questions import get_question_registry
        get_question_registry().cancel_session(session_id)
    except Exception:
        pass
    # Patch any ``status=running`` rows for this session to
    # ``cancelled`` so the chat doesn't show a stuck spinner after
    # refresh. Runs synchronously inside the stop handler — cheap,
    # ~10ms for a small session.
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.store import GraphStoreShim
        _db = default_db()
        _msgs = _db.get_messages(session_id) or []
        _shim: GraphStoreShim | None = None
        for _m in _msgs:
            if (_m.get("status") or "done") != "running":
                continue
            if _shim is None:
                _shim = GraphStoreShim(_db, session_id)
            _shim.update(
                _m["id"],
                metadata={
                    "status": "cancelled",
                    "last_update_at": time.time(),
                    "_cancelled_reason": "user_stop",
                },
            )
    except Exception:
        pass
    _s._broadcast(json.dumps({
        "type": "status",
        "paused": False,
        "stopped": True,
        "session_id": session_id,
    }))


async def handle_stats(ws, cmd: dict):
    """Welcome-banner data: agent, programs, skills, tools, providers, channels."""
    from openprogram.webui import server as _s
    try:
        from openprogram.agent.management import manager as _A
        agents = _A.list_all()
        default_agent = next((a for a in agents if getattr(a, "default", False)), None)
        if default_agent is None and agents:
            default_agent = agents[0]
        agent_summary = None
        if default_agent is not None:
            d = default_agent.to_dict()
            model = d.get("model")
            model_str = (
                model.get("id") if isinstance(model, dict)
                else (str(model) if model else None)
            )
            agent_summary = {
                "id": d.get("id"),
                "name": d.get("name") or d.get("id"),
                "model": model_str,
            }
    except Exception:
        agents = []
        agent_summary = None

    try:
        programs = _s._discover_functions()
        non_meta = [p for p in programs if p.get("category") not in ("meta",)]
        programs_count = len(non_meta)
        functions_only = [
            p for p in non_meta if p.get("category") in ("builtin", "external")
        ]
        applications_only = [p for p in non_meta if p.get("category") == "app"]
        top_functions = [
            {"name": p.get("name"), "category": p.get("category")}
            for p in functions_only if p.get("name")
        ]
        top_applications = [
            {"name": p.get("name"), "category": p.get("category")}
            for p in applications_only if p.get("name")
        ]
    except Exception:
        programs_count = 0
        functions_only = []
        applications_only = []
        top_functions = []
        top_applications = []

    try:
        from openprogram.agentic_programming.skills import (
            default_skill_dirs, load_skills,
        )
        skills_count = len(load_skills(default_skill_dirs()))
    except Exception:
        skills_count = 0

    try:
        from openprogram.agent.session_db import default_db as _session_db
        session_db = _session_db()
        conversations_count = session_db.count_sessions()
        session_rows = session_db.list_sessions(limit=_s.WELCOME_STATS_SESSION_LIMIT)
    except Exception:
        conversations_count = 0
        session_rows = []

    try:
        from openprogram.agentic_programming.skills import (
            default_skill_dirs as _ds, load_skills as _ls,
        )
        top_skills = [{"name": s.name, "slug": s.slug} for s in _ls(_ds())]
    except Exception:
        top_skills = []

    try:
        top_agents = [
            {"name": a.to_dict().get("name") or a.id, "id": a.id}
            for a in agents
        ] if agents else []
    except Exception:
        top_agents = []

    try:
        top_sessions = []
        for row in session_rows:
            session_id = row.get("id") or ""
            title = row.get("title") or session_id
            top_sessions.append({
                "id": session_id,
                "title": str(title)[:40],
            })
    except Exception:
        top_sessions = []

    try:
        from openprogram.functions import list_registered_agent_tools
        top_tools = list_registered_agent_tools()
        tools_count = len(top_tools)
    except Exception:
        tools_count = 0
        top_tools = []

    try:
        from openprogram.providers import get_providers as _gp
        providers_list = list(_gp())
        providers_count = len(providers_list)
        top_providers = providers_list
    except Exception:
        providers_count = 0
        top_providers = []

    try:
        from openprogram.channels import accounts as _acc
        top_channels = []
        for ch in _acc.SUPPORTED_CHANNELS:
            for acc in _acc.list_for_channel(ch):
                top_channels.append({
                    "channel": ch,
                    "id": getattr(acc, "id", None) or acc.account_id,
                })
        channels_count = len(top_channels)
    except Exception:
        channels_count = 0
        top_channels = []

    await ws.send_text(json.dumps({
        "type": "stats",
        "data": {
            "agent": agent_summary,
            "agents_count": len(agents) if agents else 0,
            "programs_count": programs_count,
            "functions_count": len(functions_only),
            "applications_count": len(applications_only),
            "skills_count": skills_count,
            "conversations_count": conversations_count,
            "tools_count": tools_count,
            "providers_count": providers_count,
            "channels_count": channels_count,
            "top_functions": top_functions,
            "top_applications": top_applications,
            "top_skills": top_skills,
            "top_agents": top_agents,
            "top_sessions": top_sessions,
            "top_tools": top_tools,
            "top_providers": top_providers,
            "top_channels": top_channels,
        },
    }, default=str))


async def handle_sync(ws, cmd: dict):
    """Reconnect handshake: catch up via MessageStore.sync."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    known_seqs = cmd.get("known_seqs") or {}
    if not session_id:
        return
    store = _s._get_message_store()
    for frame in store.sync(session_id, known_seqs):
        envelope = {"type": "chat_response", "data": dict(frame)}
        envelope["data"]["session_id"] = session_id
        try:
            await ws.send_text(json.dumps(envelope, default=str))
        except Exception:
            break
    # G1: a client joining mid-run needs the CURRENT running-task indicator
    # (sync replays messages, not the live "is this session busy" state). Only
    # emit when there's actually a running task — for an idle/unknown session
    # sync stays a no-op (a client defaults to idle anyway).
    try:
        with _s._running_tasks_lock:
            task = dict(_s._running_tasks.get(session_id) or {})
        if task:
            await ws.send_text(json.dumps(
                {"type": "running_task",
                 "data": {"session_id": session_id, **task}},
                default=str))
    except Exception:
        pass


async def handle_steer(ws, cmd: dict):
    """Mid-run steering from TUI/web: drop a course-correction into a live run.

    Writes to the per-session steering inbox (a file dir under the session),
    which the running research_agent loop drains at its next step boundary —
    the same inbox the CLI ``steer`` subcommand uses, so it works whether the
    run is in-process or in a worker subprocess (files cross processes)."""
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    message = cmd.get("message") or ""
    if not session_id or not message.strip():
        return
    ok = False
    try:
        from research_harness import steering
        ok = steering.push(session_id, message)
    except Exception:
        ok = False
    from openprogram.webui import server as _s
    try:
        _s._broadcast(json.dumps({
            "type": "steer_ack",
            "data": {"session_id": session_id, "queued": ok,
                     "message": message.strip()[:200]},
        }, default=str))
    except Exception:
        pass


async def handle_set_attended(ws, cmd: dict):
    """Set whether the agent may ask the user, for this session (TUI/web
    toggle). Broadcasts the new mode so all surfaces show it in sync."""
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    if not session_id:
        return
    attended = bool(cmd.get("attended"))
    try:
        from openprogram.agent.attended import set_attended
        set_attended(attended, session_id)
    except Exception:
        return
    from openprogram.webui import server as _s
    try:
        _s._broadcast(json.dumps({
            "type": "attended_changed",
            "data": {"session_id": session_id, "attended": attended},
        }, default=str))
    except Exception:
        pass


ACTIONS = {
    "list_models": handle_list_models,
    "switch_model": handle_switch_model,
    "browser": handle_browser,
    "stop": handle_stop,
    "stats": handle_stats,
    "sync": handle_sync,
    "steer": handle_steer,
    "set_attended": handle_set_attended,
}
