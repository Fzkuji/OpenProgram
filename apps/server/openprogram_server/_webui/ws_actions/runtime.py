"""Runtime / misc WS actions: list_models, switch_model, browser,
stats, sync. Mirrors several REST endpoints for ws-only clients (the
Ink CLI) plus the reconnect-sync handshake.
"""
from __future__ import annotations

import asyncio
import json
import time

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
            if conv is None:
                # A switch addressed to a session is a session-level
                # setting even when the session hasn't materialized yet
                # (the picker fires before the first message). Create it
                # now — silently falling through to the default-runtime
                # swap changed the model for EVERY session and left this
                # one on the old default.
                conv = await asyncio.to_thread(
                    _s._get_or_create_session, session_id)
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
                # The dispatcher reads the picker choice from
                # provider_override / model_override, and restart
                # restore only trusts those keys — provider_name alone
                # is treated as possibly-stale state. Without them the
                # turn re-resolves through the enabled list and the
                # session answers on a different model than the chip
                # shows (the routes/runtime.py sibling sets them; this
                # WS path never did).
                conv["provider_override"] = prov
                conv["model_override"] = bare_model
                # Persist straight to the session row. _save_session
                # skips message-less sessions (ghost guard), but spawned
                # turns (goal refine/decision, task agents) read the
                # override from the DB — a switch before the first
                # message must already be visible to them.
                def _persist_override():
                    from openprogram.agent.session_db import default_db
                    default_db().update_session(
                        session_id,
                        provider_override=prov,
                        model_override=bare_model,
                    )
                    _s._save_session(session_id)
                await asyncio.to_thread(_persist_override)
                info = _s._get_provider_info(session_id)
                _s._broadcast(json.dumps(
                    {"type": "provider_changed", "data": info},
                ))
                # New model, new window — re-estimate against it so the
                # ring's percentage tracks the switch immediately.
                _s.refresh_context_stats(session_id)
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
        # Same persistence as POST /api/model's global branch — a ws switch
        # must survive a restart too.
        from openprogram.providers.storage import save_default_model
        await asyncio.to_thread(
            save_default_model,
            target_provider or _s._runtime_management._default_provider,
            bare_model,
        )
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
        from openprogram.programs.tools.web.browser.browser import execute as _br_exec
        result = _br_exec(action=verb, **kwargs)
    except Exception as e:  # noqa: BLE001
        result = f"Error: {type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "browser_result",
        "data": {"verb": verb, "result": str(result)},
    }, default=str))


def _broadcast_execution(execution: dict) -> None:
    from openprogram.webui import server as _s
    _s._broadcast(json.dumps({
        "type": "execution.updated",
        "execution": execution,
    }, default=str))


def _trusted_runtime_actor(ws) -> dict | None:
    """Resolve runtime-control authority from the authenticated socket only."""
    from openprogram.agent.authority import normalize_authority

    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    authority = state.get("authority") if isinstance(state, dict) else None
    actor = normalize_authority(authority)
    if not actor or actor.get("authority_tier") != "owner":
        return None
    return actor


async def _send_command_update(ws, command, execution) -> None:
    command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
    execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
    await ws.send_text(json.dumps({
        "type": "execution.command.updated", "command": command_data,
        "data": {"command": command_data},
    }, default=str))
    await ws.send_text(json.dumps({
        "type": "execution.updated", "execution": execution_data,
        "data": {"execution": execution_data},
    }, default=str))
    _broadcast_execution(execution_data)


def _rejected_command(cmd: dict, code: str, latest_snapshot: dict | None = None) -> dict:
    value = {
        "command_id": str(cmd.get("command_id") or ""),
        "execution_id": str(cmd.get("execution_id") or ""),
        "status": "rejected", "result_version": None,
        "rejection_code": code,
    }
    if latest_snapshot is not None:
        value["latest_snapshot"] = latest_snapshot
    return value


async def _handle_execution_control(ws, cmd: dict, operation: str) -> None:
    """Submit an exact durable runtime command; drivers never see WS input."""
    from openprogram.execution import default_control_service, default_store
    from openprogram.execution.store import ExecutionConflict, CommandConflict
    from openprogram.execution.attempts import AttemptConflict
    from openprogram.execution.state_machine import InvalidCommand

    actor = _trusted_runtime_actor(ws)
    execution_id = cmd.get("execution_id")
    command_id = cmd.get("command_id")
    expected_version = cmd.get("expected_version")
    if (
        actor is None or not isinstance(execution_id, str) or not execution_id
        or not isinstance(command_id, str) or not command_id
        or type(expected_version) is not int
    ):
        await _send_command_update(ws, _rejected_command(cmd, "invalid_command"), {
            "execution_id": execution_id or "", "status_version": None,
        })
        return
    store = default_store()
    service = default_control_service()
    execution = store.get_execution(execution_id)
    if execution is None:
        await _send_command_update(ws, _rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        })
        return
    # The authenticated session, when supplied by the websocket handshake,
    # must address its own execution.  Do not trust cmd.actor/session fields.
    state = ws.scope.get("state") if isinstance(getattr(ws, "scope", None), dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    if bound_session is not None and bound_session != execution.session_id:
        await _send_command_update(ws, _rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        })
        return
    try:
        if operation in {"pause", "continue", "step"}:
            from openprogram.execution.model import CommandKind
            required = {
                "pause": CommandKind.PAUSE,
                "continue": CommandKind.CONTINUE,
                "step": CommandKind.STEP,
            }[operation]
            if not getattr(execution.capabilities, {
                "pause": "pause", "continue": "pause", "step": "step",
            }[operation]):
                raise ExecutionConflict("unsupported", "execution does not support this control command")
        if operation in {"continue", "step"} and service.effects.list_unresolved(execution_id):
            if execution.current_attempt_id is not None:
                generation = execution.owner_lease.get("generation")
                if isinstance(generation, int):
                    service.recover_owner_loss(
                        execution_id, attempt_id=execution.current_attempt_id,
                        generation=generation,
                    )
            raise ExecutionConflict("unresolved_effect", "execution has an unresolved external effect")
        if operation == "pause":
            dispatch = await service.request_pause(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
            )
        elif operation == "cancel":
            # ``reason_code`` is server policy, not caller-controlled input.
            # The first accepted cancel command retains this exact reason if
            # another cancellation races with it.
            dispatch = await service.request_cancel(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                reason_code="cancel.user",
            )
        else:
            request = (
                service.request_continue if operation == "continue"
                else service.request_step
            )
            dispatch = await request(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
            )
        await _send_command_update(ws, dispatch.command, dispatch.execution)
    except (ExecutionConflict, CommandConflict, AttemptConflict, InvalidCommand) as exc:
        current = store.get_execution(execution_id)
        await _send_command_update(
            ws, _rejected_command(
                cmd,
                "unsupported_capability" if getattr(exc, "code", None) == "unsupported" else getattr(exc, "code", "command_rejected"),
                current.to_dict() if current is not None else None,
            ),
            current.to_dict() if current is not None else {
                "execution_id": execution_id, "status_version": None,
            },
        )


async def handle_execution_pause(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "pause")


async def handle_execution_continue(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "continue")


async def handle_execution_step(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "step")


async def handle_execution_cancel(ws, cmd: dict):
    """Submit an exact durable cancellation command."""
    await _handle_execution_control(ws, cmd, "cancel")


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
        from openprogram.skills import list_skills
        skills_count = len(list_skills())
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
        from openprogram.skills import list_skills as _ls
        top_skills = [{"name": s.name, "slug": s.leaf} for s in _ls()]
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
        from openprogram.programs import list_registered_agent_tools
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


async def handle_steer(ws, cmd: dict):
    """Reject steering until durable checkpoint support is implemented."""
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    message = cmd.get("message") or ""
    if not session_id:
        return
    try:
        await ws.send_text(json.dumps({
            "type": "steer_ack",
            "data": {
                "session_id": session_id,
                "request_id": cmd.get("request_id"),
                "result": "unsupported",
                "queued": False,
                "message": message.strip()[:200],
                "code": "unsupported_capability",
            },
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
    "execution.cancel": handle_execution_cancel,
    "execution.pause": handle_execution_pause,
    "execution.continue": handle_execution_continue,
    "execution.step": handle_execution_step,
    "stats": handle_stats,
    "steer": handle_steer,
    "set_attended": handle_set_attended,
}
