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
        from openprogram.tools.browser.browser import execute as _br_exec
        result = _br_exec(action=verb, **kwargs)
    except Exception as e:  # noqa: BLE001
        result = f"Error: {type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "browser_result",
        "data": {"verb": verb, "result": str(result)},
    }, default=str))


async def handle_stop(ws, cmd: dict):
    """Mirror /api/stop — cancel in-flight turn for a conv."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    if not session_id:
        return
    _s._mark_cancelled(session_id)
    _s.resume_execution()
    _s._kill_active_runtime(session_id)
    with _s._follow_up_lock:
        q = _s._follow_up_queues.get(session_id)
    if q is not None:
        try:
            q.put_nowait({"_cancelled": True})
        except Exception:
            pass
    _s._broadcast(json.dumps({
        "type": "status",
        "paused": False,
        "stopped": True,
        "session_id": session_id,
    }))


async def handle_stats(ws, cmd: dict):
    """Welcome-banner snapshot: agent, programs, skills, tools, providers, channels."""
    from openprogram.webui import server as _s
    try:
        from openprogram.agents import manager as _A
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
        from openprogram.tools import ALL_TOOLS as _ALL
        tools_count = len(_ALL)
        top_tools = list(_ALL)
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


ACTIONS = {
    "list_models": handle_list_models,
    "switch_model": handle_switch_model,
    "browser": handle_browser,
    "stop": handle_stop,
    "stats": handle_stats,
    "sync": handle_sync,
}
