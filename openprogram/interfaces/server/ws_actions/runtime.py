"""Runtime / misc WS actions: list_models, switch_model,
stats, sync. Mirrors several REST endpoints for ws-only clients (the
Ink CLI) plus the reconnect-sync handshake.
"""
from __future__ import annotations

import logging
import asyncio
import json

from openprogram.runtime.execution.control.submission import (  # noqa: F401
    PUBLIC_COMMAND_ACTIONS as _PUBLIC_COMMAND_ACTIONS,
    authorize_execution_target as _authorize_execution,
    rejected_command as _rejected_command,
    submit_execution_control,
    trusted_runtime_actor,
    validate_execution_command_request,
)
from openprogram.state.store.session.conversation_work import run_for_conversation

_log = logging.getLogger(__name__)

# WELCOME_STATS_SESSION_LIMIT lives on the server module — we read it lazily.


async def handle_list_models(ws, cmd: dict):
    from openprogram.interfaces.server import server as _s
    try:
        with _s.runtime_management._runtime_lock:
            if _s.runtime_management._default_provider is None:
                (_s.runtime_management._default_provider,
                 _s.runtime_management._default_runtime) = _s._detect_default_provider()
        provider = _s.runtime_management._default_provider or "none"
        runtime = _s.runtime_management._default_runtime
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
    from openprogram.interfaces.server import server as _s
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    try:
        model = (cmd.get("model") or "").strip()
        explicit_provider = (cmd.get("provider") or "").strip() or None
        session_id = cmd.get("session_id")
        from openprogram.interfaces.server.branch_settings import prepare_initial_guard, materialized_guard, commit_model, current_branch
        # Each branch check reads the conversation under its lock: off the loop.
        initial_branch_id = await run_for_conversation(session_id, prepare_initial_guard, session_id, cmd)
        if not model:
            await ws.send_text(json.dumps({
                "type": "error", "data": {"message": "Missing model"},
            }))
            return
        inferred_provider = None
        bare_model = model
        if explicit_provider is None and ":" in model:
            head, tail = model.split(":", 1)
            from openprogram.models.providers import get_providers as _get_providers
            known = set(_get_providers())
            known.update({"openai-codex", "gemini-cli",
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
                conv = await run_for_conversation(session_id, 
                    _s._get_or_create_session, session_id)
            if conv:
                from openprogram.interfaces.server.routes.execution.runtime import picks_other_branch
                old_rt = conv.get("runtime")
                cur_prov = conv.get(
                    "provider_name", _s.runtime_management._default_provider,
                )
                prov = target_provider or cur_prov
                # Another branch's model is stored, not run: build nothing.
                elsewhere = await run_for_conversation(session_id, picks_other_branch, session_id, cmd)
                need_new_rt = not elsewhere and (
                    (target_provider and target_provider != cur_prov)
                    or (old_rt is None)
                )
                if need_new_rt:
                    try:
                        new_rt = await _build_rt(prov)
                    except Exception as e:
                        _s._log(f"[switch_model] runtime build skipped for {prov}: {e}")
                        new_rt = None
                else:
                    new_rt = old_rt
                guarded_cmd = await run_for_conversation(session_id, materialized_guard, session_id, cmd, initial_branch_id)
                row = await run_for_conversation(session_id, commit_model, session_id, guarded_cmd, prov, bare_model, conv,
                                        runtime=None if elsewhere else new_rt,
                                        replace_runtime=bool(need_new_rt))
                prov, bare_model = row.get("provider_override"), row.get("model_override")
                from openprogram.interfaces.server.routes.execution.runtime import picked_reply
                reply = picked_reply(row)
                if row.get("branch_id") != row.get("active_branch_id"):
                    # Another branch's model: no broadcast moves the selected one's.
                    await ws.send_text(json.dumps({"type": "model_switched", "data": reply}, default=str))
                    return
                info = await run_for_conversation(session_id, _s._get_provider_info, session_id)
                _s._broadcast(json.dumps(
                    {"type": "provider_changed", "data": info},
                ))
                # New model, new window — re-estimate against it so the
                # ring's percentage tracks the switch immediately.
                await run_for_conversation(session_id, _s.refresh_context_stats, session_id)
                active_branch_id = await run_for_conversation(session_id, current_branch, session_id)
                await ws.send_text(json.dumps({
                    "type": "model_switched",
                    "data": {**reply, "active_branch_id": active_branch_id},
                }, default=str))
                return

        # No session_id → swap default runtime.
        if (
            target_provider
            and target_provider != _s.runtime_management._default_provider
        ):
            new_rt = await _build_rt(target_provider)
            if (
                _s.runtime_management._default_runtime
                and hasattr(_s.runtime_management._default_runtime, "close")
            ):
                try:
                    _s.runtime_management._default_runtime.close()
                except Exception:
                    # The old runtime is being replaced; a failed close must
                    # not block the switch.
                    _log.debug("closing replaced default runtime failed", exc_info=True)
            _s.runtime_management._default_runtime = new_rt
            _s.runtime_management._default_provider = target_provider
        elif _s.runtime_management._default_runtime:
            _s.runtime_management._default_runtime.model = bare_model
        else:
            await ws.send_text(json.dumps({
                "type": "error", "data": {"message": "No active runtime"},
            }))
            return
        # Same persistence as POST /api/model's global branch — a ws switch
        # must survive a restart too.
        from openprogram.models.providers.catalog.last_used import record_last_used_model
        await asyncio.to_thread(
            record_last_used_model,
            target_provider or _s.runtime_management._default_provider,
            bare_model,
        )
        info = _s._get_provider_info()
        _s._broadcast(json.dumps({"type": "provider_changed", "data": info}))
        await ws.send_text(json.dumps({
            "type": "model_switched",
            "data": {
                "provider": target_provider or _s.runtime_management._default_provider,
                "model": bare_model,
            },
        }))
    except BranchConfigConflict as exc:
        from openprogram.interfaces.server.routes.execution.runtime import model_conflict
        conflict = await run_for_conversation(cmd.get("session_id"), model_conflict, cmd.get("session_id"), exc)
        await ws.send_text(json.dumps({"type": "error", "data": {
            **conflict,
            "session_id": cmd.get("session_id"), "request_id": cmd.get("request_id"),
        }}))
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": str(e)},
        }))


def _broadcast_execution(execution: dict, event_cursor: dict) -> None:
    from openprogram.runtime.execution.public import execution_update_frame
    from openprogram.interfaces.server import server as _s
    _s._broadcast(json.dumps(
        execution_update_frame(execution, event_cursor), default=str,
    ))


def _trusted_runtime_actor(ws) -> dict | None:
    return trusted_runtime_actor(getattr(ws, "scope", None), surface="ws")


from openprogram.runtime.execution.public import public_event as _public_event


def _public_execution_snapshot(execution, *, event_sequence: int | None = None) -> tuple[dict, dict]:
    """Return the one snapshot shape shared by command and reconnect paths."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.public import execution_snapshot

    execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
    resource = None
    job = None
    try:
        from openprogram.runtime.jobs.runner import runner_for_execution_store

        runner = runner_for_execution_store(default_store())
        view = runner.get_job_resource_view(execution_data["execution_id"]) if runner else None
        if view is not None:
            # ExecutionSnapshot.resource contains only the resource
            # projection.  JobResourceDTO remains the enclosing job view on
            # dedicated job surfaces.
            resource = view.resource
            job = runner.get_job(execution_data["execution_id"])
    except Exception:
        # The job runner is optional; the snapshot omits resource/job without
        # it.
        _log.debug(
            "job view unavailable for execution %s", execution_data.get("execution_id"), exc_info=True)
    # Dict inputs are rejection placeholders, not authorized records.  Never
    # resolve them through the store here: doing so would turn an unauthorized
    # command into a readable execution snapshot.
    record = execution if hasattr(execution, "execution_id") else None
    if record is not None:
        execution_data = execution_snapshot(
            record, store=default_store(), resource=resource,
            job_id=getattr(job, "id", None), job=job, event_sequence=event_sequence,
        ).to_dict()
    cursor = {
        "execution_id": execution_data.get("execution_id"),
        "next_sequence": int(execution_data.get("event_sequence") or 0) + 1,
        "snapshot_status_version": execution_data.get("status_version"),
    }
    return execution_data, cursor


def _settled_command_snapshot(command, execution):
    """``(command_data, execution, execution_data, cursor)`` once the command's status is stable.

    Job activation is asynchronous and may finish between command acceptance
    and transport serialization.  Refresh the command and execution together
    until the command status is stable, so a response never combines an old
    accepted command with a newer terminal resource snapshot.  The snapshot's
    job view reads the conversation's records under its lock, so callers on
    the event loop run this in a worker thread.
    """
    command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
    execution_data: dict = {}
    cursor: dict = {}
    if hasattr(execution, "execution_id"):
        execution_data, cursor = _public_execution_snapshot(execution)
        for _ in range(3):
            try:
                from openprogram.runtime.execution import default_store

                store = default_store()
                latest_command = store.get_command(command_data.get("command_id", ""))
                latest_execution = store.get_execution(command_data.get("execution_id", ""))
                if latest_command is not None and command_data.get("status") != "rejected":
                    command = latest_command
                    command_data = latest_command.to_dict()
                if latest_execution is not None:
                    execution = latest_execution
            except Exception:
                break
            execution_data, cursor = _public_execution_snapshot(execution)
            current = store.get_command(command_data.get("command_id", ""))
            if current is None or current.to_dict() == command_data:
                break
            command = current
        else:
            execution_data, cursor = _public_execution_snapshot(execution)
    return command_data, execution, execution_data, cursor


async def _send_command_update(ws, command, execution) -> None:
    has_public_snapshot = hasattr(execution, "execution_id")
    command_data, execution, execution_data, cursor = await run_for_conversation(getattr(execution, "session_id", None), 
        _settled_command_snapshot, command, execution,
    )
    if command_data.get("kind") == "execution.step":
        checkpoint_id = getattr(execution, "checkpoint_head_id", None)
        if checkpoint_id:
            try:
                from openprogram.runtime.execution import ExecutionCheckpointStore, default_store

                checkpoint = ExecutionCheckpointStore(default_store()).get(checkpoint_id)
                command_data["managed_action_count"] = len(
                    checkpoint.completed_actions if checkpoint is not None else (),
                )
            except Exception:
                command_data["managed_action_count"] = 0
        else:
            command_data["managed_action_count"] = 0
    update = {
        "type": "execution.command.updated", "command": command_data,
    }
    if has_public_snapshot:
        update.update({"execution": execution_data, "event_cursor": cursor})
    update["data"] = {
        key: value for key, value in update.items() if key != "type"
    }
    await ws.send_text(json.dumps(update, default=str))
    if not has_public_snapshot:
        return
    from openprogram.runtime.execution.public import execution_update_frame
    await ws.send_text(json.dumps(
        execution_update_frame(execution_data, cursor), default=str,
    ))
    _broadcast_execution(execution_data, cursor)


async def _handle_execution_control(ws, cmd: dict, operation: str) -> None:
    """Submit an exact durable runtime command; drivers never see WS input."""
    from openprogram.interfaces.server.ws_actions.chat.attachments import prepare_steer_payload

    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    command, execution = await submit_execution_control(
        cmd,
        operation,
        actor=_trusted_runtime_actor(ws),
        bound_session=bound_session if isinstance(bound_session, str) else None,
        surface="ws",
        prepare_steer=prepare_steer_payload,
    )
    await _send_command_update(ws, command, execution)
    execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
    if operation == "cancel" and execution_data.get("status") in {"cancelling", "cancelled"}:
        from openprogram.interfaces.server import server as _s
        # Releasing reads the conversation's records under its lock: off the loop.
        await run_for_conversation(execution_data.get("session_id"), _s._release_session_occupancy_for_execution, execution_data)


async def handle_execution_pause(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "pause")


async def handle_execution_continue(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "continue")


async def handle_execution_step(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "step")


async def handle_execution_steer(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "steer")


async def handle_execution_fork(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "fork")


async def handle_execution_retry(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "retry")


async def handle_execution_cancel(ws, cmd: dict):
    """Submit an exact durable cancellation command."""
    await _handle_execution_control(ws, cmd, "cancel")


async def handle_execution_replay(ws, cmd: dict) -> None:
    """Replay one authorized execution after a persisted local cursor."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.store import ExecutionConflict

    execution_id = cmd.get("execution_id")
    after_sequence = cmd.get("after_sequence")
    snapshot_only = cmd.get("snapshot_only", False)
    if (not isinstance(execution_id, str) or not execution_id
            or type(after_sequence) is not int or type(snapshot_only) is not bool):
        await ws.send_text(json.dumps({"type": "execution.replay", "error": "invalid_command"}))
        return
    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    actor = _trusted_runtime_actor(ws)

    def read():
        # The job view reads the conversation's records under its lock: the
        # whole read runs off the event loop.
        store = default_store()
        execution = store.get_execution(execution_id)
        if execution is None:
            raise ExecutionConflict("not_found", "execution is not visible")
        _authorize_execution(
            actor, "execution.events", execution,
            bound_session=bound_session if isinstance(bound_session, str) else None,
        )
        replay = store.read_event_replay(
            execution_id, after_sequence=after_sequence, include_events=not snapshot_only,
        )
        return replay, _public_execution_snapshot(
            replay.execution, event_sequence=replay.cursor.next_sequence - 1,
        )

    try:
        replay, (snapshot, cursor) = await run_for_conversation(execution_id, read)
    except Exception:
        await ws.send_text(json.dumps({"type": "execution.replay", "error": "not_found", "execution_id": execution_id}))
        return
    await ws.send_text(json.dumps({
        "type": "execution.replay",
        "execution_id": execution_id,
        "events": [_public_event(event) for event in replay.events],
        "event_cursor": replay.cursor.to_dict(),
        "recovery": replay.recovery,
        "snapshot": snapshot,
        "data": {
            "execution_id": execution_id,
            "events": [_public_event(event) for event in replay.events],
            "event_cursor": cursor,
            "recovery": replay.recovery,
            "snapshot": snapshot,
        },
    }, default=str))


async def handle_execution_wait_answer(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "wait_answer")


async def handle_execution_wait_decline(ws, cmd: dict):
    await _handle_execution_control(ws, cmd, "wait_decline")


async def _handle_revision_control(ws, cmd: dict, action: str) -> None:
    """Serve revision editor actions without granting a second control path."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.revision_public import (
        RevisionPublicError,
        submit_revision_request,
    )

    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    try:
        result = submit_revision_request(
            default_store(), cmd, action, actor=_trusted_runtime_actor(ws),
            bound_session=bound_session if isinstance(bound_session, str) else None,
            surface="ws",
        )
    except RevisionPublicError as exc:
        result = {"type": "revision.draft.rejected", "error": exc.code}
    await ws.send_text(json.dumps({**result, "data": result}, default=str))


async def handle_revision_draft_create(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.draft.create")


async def handle_revision_draft_get(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.draft.get")


async def handle_revision_draft_replace(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.draft.replace")


async def handle_revision_draft_discard(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.draft.discard")


async def handle_revision_validate(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.validate")


async def handle_revision_approve(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.approve")


async def handle_revision_publish(ws, cmd: dict):
    await _handle_revision_control(ws, cmd, "revision.publish")


async def handle_stats(ws, cmd: dict):
    """Welcome-banner data: agent, programs, skills, tools, providers, channels."""
    from openprogram.interfaces.server import server as _s
    try:
        from openprogram.runtime.agent_registry import manager as _A
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
        programs = await asyncio.to_thread(_s._discover_functions)
        non_meta = [p for p in programs if p.get("category") not in ("meta",)]
        programs_count = len(non_meta)
        functions_only = [p for p in non_meta if p.get("category") == "tool"]
        applications_only = [p for p in non_meta if p.get("category") == "application"]
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
        from openprogram.extensions.skills import list_skills
        skills_count = len(list_skills())
    except Exception:
        skills_count = 0

    try:
        from openprogram.runtime.context.session_db import default_db as _session_db
        session_db = _session_db()
        conversations_count = session_db.count_sessions()
        session_rows = session_db.list_sessions(limit=_s.WELCOME_STATS_SESSION_LIMIT)
    except Exception:
        conversations_count = 0
        session_rows = []

    try:
        from openprogram.extensions.skills import list_skills as _ls
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
        from openprogram.models.providers import get_providers as _gp
        providers_list = list(_gp())
        providers_count = len(providers_list)
        top_providers = providers_list
    except Exception:
        providers_count = 0
        top_providers = []

    try:
        from openprogram.interfaces.channels import accounts as _acc
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


async def handle_set_attended(ws, cmd: dict):
    """Set whether the agent may ask the user, for this session (TUI/web
    toggle). Broadcasts the new mode so all surfaces show it in sync."""
    session_id = cmd.get("session_id") or cmd.get("conv_id")
    if not session_id:
        return
    attended = bool(cmd.get("attended"))
    try:
        from openprogram.runtime.permissions.attended import set_attended
        set_attended(attended, session_id)
    except Exception:
        return
    from openprogram.interfaces.server import server as _s
    try:
        _s._broadcast(json.dumps({
            "type": "attended_changed",
            "data": {"session_id": session_id, "attended": attended},
        }, default=str))
    except Exception:
        # The mode is saved; other surfaces show it on their next load.
        _log.debug(
            "attended_changed not broadcast for session %s", session_id, exc_info=True)



async def handle_subscribe_execution_stream(ws, cmd: dict):
    """Serve a consistent snapshot for execution_stream.v1 recovery.

    Looks up the live CallStreamState owner when present; otherwise
    reconstructs a bounded snapshot from Call.metadata.stream checkpoint.
    """
    from openprogram.runtime.agent.runtime.execution_stream.protocol import (
        SCHEMA_VERSION,
        build_chat_response_envelope,
    )
    from openprogram.runtime.agent.runtime.execution_stream.transport import (
        lookup_owner,
    )

    session_id = str(cmd.get("session_id") or "")
    execution_id = str(cmd.get("execution_id") or "")
    node_ids = cmd.get("node_ids") or []
    if isinstance(node_ids, str):
        node_ids = [node_ids]
    subscription_id = str(cmd.get("subscription_id") or "")
    if not session_id or not execution_id or not node_ids:
        await ws.send_text(json.dumps({
            "type": "action_error",
            "data": {"code": "invalid_request", "message": "subscribe_execution_stream requires session/execution/node_ids"},
        }))
        return

    # WS connections are owner-authenticated at accept time; still require
    # the session to exist so forged ids cannot probe arbitrary node streams.
    try:
        from openprogram.runtime.context.session_db import default_db
        # Read under the conversation's lock: in its lane, off the loop.
        if await run_for_conversation(session_id, default_db().get_session, session_id) is None:
            await ws.send_text(json.dumps({
                "type": "action_error",
                "data": {"code": "forbidden", "message": "unknown session"},
            }))
            return
    except Exception:
        await ws.send_text(json.dumps({
            "type": "action_error",
            "data": {"code": "forbidden", "message": "session access denied"},
        }))
        return

    for node_id in node_ids:
        node_id = str(node_id)
        owner = lookup_owner(session_id, execution_id, node_id)
        if owner is not None:
            snap = owner.emit_snapshot(durability="live")
            # emit_snapshot already broadcasts; also send directly to requester.
            payload = {
                "type": "execution_stream",
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "execution_id": execution_id,
                "node_id": node_id,
                "generation": owner.identity.generation,
                "display_msg_id": owner.identity.display_msg_id,
                "op": "snapshot",
                "revision": owner.revision,
                "checkpoint_revision": owner.checkpoint_revision,
                "durability": "live",
                "phase": owner.phase,
                "snapshot": snap,
                "subscription_id": subscription_id,
            }
            await ws.send_text(json.dumps(
                build_chat_response_envelope(payload), default=str,
            ))
            continue
        # Checkpoint fallback from DAG metadata.
        stream_meta = None
        node = None
        try:
            from openprogram.runtime.context.session_db import default_db
            nodes = await run_for_conversation(session_id, default_db().get_nodes, session_id)
            node = next((n for n in nodes if n.id == node_id), None)
            if node is not None:
                stream_meta = (node.metadata or {}).get("stream")
        except Exception:
            stream_meta = None
        if not isinstance(stream_meta, dict):
            payload = {
                "type": "execution_stream",
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "execution_id": execution_id,
                "node_id": node_id,
                "generation": int(cmd.get("generation") or 0),
                "op": "sync_required",
                "reason": "owner_unavailable",
                "revision": int(cmd.get("revision") or 0),
                "subscription_id": subscription_id,
            }
            await ws.send_text(json.dumps(
                build_chat_response_envelope(payload), default=str,
            ))
            continue
        gen = int(stream_meta.get("generation") or 1)
        rev = int(stream_meta.get("revision") or 0)
        stored_snapshot = stream_meta.get("snapshot")
        if isinstance(stored_snapshot, dict):
            # The durable snapshot already contains the ordered attempts and
            # blocks. Mark the copy as a checkpoint so the client can expose
            # owner-gone recovery without treating it as a live stream.
            snap = dict(stored_snapshot)
            snap["durability"] = "checkpoint"
            snap["recovered_from_checkpoint"] = True
            snap["source_checkpoint"] = {"generation": gen, "revision": rev}
        else:
            # Legacy records had only summaries and metadata.blocks. Keep the
            # old data visible as one attempt rather than dropping tool rows.
            legacy_blocks = (node.metadata or {}).get("blocks") if node else None
            attempts = stream_meta.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
            if isinstance(legacy_blocks, list) and legacy_blocks:
                attempts = [{
                    "attempt_id": stream_meta.get("current_attempt_id") or "legacy",
                    "attempt_index": 0,
                    "reason": "legacy",
                    "status": "completed" if not stream_meta.get("partial") else "running",
                    "validation": "pending",
                    "blocks": legacy_blocks,
                }]
            snap = {
                "revision": rev,
                "checkpoint_revision": stream_meta.get("checkpoint_revision", rev),
                "generation": gen,
                "phase": stream_meta.get("phase") or "running",
                "durability": "checkpoint",
                "current_attempt_id": stream_meta.get("current_attempt_id"),
                "selected_attempt_id": stream_meta.get("selected_attempt_id"),
                "attempts": attempts,
                "preview_text": stream_meta.get("preview_text") or "",
                "preview_reasoning": stream_meta.get("preview_reasoning") or "",
                "recovered_from_checkpoint": True,
                "source_checkpoint": {"generation": gen, "revision": rev},
            }
        payload = {
            "type": "execution_stream",
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "execution_id": execution_id,
            "node_id": node_id,
            "generation": gen,
            "op": "snapshot",
            "revision": rev,
            "checkpoint_revision": snap.get("checkpoint_revision", rev),
            "durability": "checkpoint",
            "phase": snap.get("phase") or stream_meta.get("phase") or "running",
            "snapshot": snap,
            "subscription_id": subscription_id,
        }
        await ws.send_text(json.dumps(
            build_chat_response_envelope(payload), default=str,
        ))



ACTIONS = {
    "list_models": handle_list_models,
    "subscribe_execution_stream": handle_subscribe_execution_stream,
    "switch_model": handle_switch_model,
    "execution.cancel": handle_execution_cancel,
    "execution.pause": handle_execution_pause,
    "execution.continue": handle_execution_continue,
    "execution.step": handle_execution_step,
    "execution.steer": handle_execution_steer,
    "execution.fork": handle_execution_fork,
    "execution.retry": handle_execution_retry,
    "execution.replay": handle_execution_replay,
    "execution.wait.answer": handle_execution_wait_answer,
    "execution.wait.decline": handle_execution_wait_decline,
    "revision.draft.create": handle_revision_draft_create,
    "revision.draft.get": handle_revision_draft_get,
    "revision.draft.replace": handle_revision_draft_replace,
    "revision.draft.discard": handle_revision_draft_discard,
    "revision.validate": handle_revision_validate,
    "revision.approve": handle_revision_approve,
    "revision.publish": handle_revision_publish,
    "stats": handle_stats,
    "set_attended": handle_set_attended,
}
