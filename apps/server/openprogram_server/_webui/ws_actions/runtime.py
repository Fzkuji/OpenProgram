"""Runtime / misc WS actions: list_models, switch_model, browser,
stats, sync. Mirrors several REST endpoints for ws-only clients (the
Ink CLI) plus the reconnect-sync handshake.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

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


def trusted_runtime_actor(scope, *, surface: str | None = None) -> dict | None:
    """Resolve runtime-control authority from authenticated transport state."""
    from openprogram.agent.authority import normalize_authority

    state = scope.get("state") if isinstance(scope, dict) else None
    authority = state.get("authority") if isinstance(state, dict) else None
    actor = normalize_authority(authority)
    if not actor or actor.get("authority_tier") != "owner":
        return None
    if isinstance(authority, dict):
        for field in ("project_ids", "session_ids", "execution_actions"):
            value = authority.get(field)
            if isinstance(value, (list, tuple, frozenset, set)):
                actor[field] = tuple(str(item) for item in value)
    if surface is not None:
        actor["surface"] = surface
    return actor


def _trusted_runtime_actor(ws) -> dict | None:
    return trusted_runtime_actor(getattr(ws, "scope", None), surface="ws")


_PUBLIC_COMMAND_ACTIONS = {
    "pause": "execution.pause",
    "continue": "execution.continue",
    "step": "execution.step",
    "steer": "execution.steer",
    "cancel": "execution.cancel",
    "fork": "execution.fork",
    "retry": "execution.retry",
    "wait_answer": "execution.wait.answer",
    "wait_decline": "execution.wait.decline",
}


def validate_execution_command_request(cmd: dict, operation: str) -> str | None:
    """Validate the one public command envelope before touching a runtime.

    Target identity, actor, session, project, lease, and capability data are
    server-owned.  A transport cannot supply any of them as a second control
    path.
    """
    if operation not in _PUBLIC_COMMAND_ACTIONS or not isinstance(cmd, dict):
        return "invalid_command"
    if set(cmd) - {"type", "action", "command_id", "execution_id", "expected_version", "payload"}:
        return "invalid_command"
    if cmd.get("type") != "execution.command" or cmd.get("action") != _PUBLIC_COMMAND_ACTIONS[operation]:
        return "invalid_command"
    command_id = cmd.get("command_id")
    execution_id = cmd.get("execution_id")
    expected_version = cmd.get("expected_version")
    payload = cmd.get("payload", {})
    if (
        not isinstance(command_id, str) or not command_id or len(command_id) > 256
        or not isinstance(execution_id, str) or not execution_id or len(execution_id) > 256
        or type(expected_version) is not int or expected_version < 0
        or not isinstance(payload, dict)
    ):
        return "invalid_command"
    if operation in {"pause", "continue", "step", "cancel"}:
        return None if not payload else "invalid_payload"
    if operation == "steer":
        message = payload.get("message")
        return (
            None
            if set(payload) == {"message"} and isinstance(message, str)
            and message.strip() and len(message) <= 4096
            else "invalid_payload"
        )
    if operation == "retry":
        checkpoint_id = payload.get("checkpoint_id")
        return (
            None
            if set(payload).issubset({"checkpoint_id"})
            and (checkpoint_id is None or isinstance(checkpoint_id, str) and checkpoint_id)
            else "invalid_payload"
        )
    if operation == "fork":
        return (
            None
            if set(payload) == {"manifest_id", "checkpoint_id", "proof_hash"}
            and isinstance(payload.get("manifest_id"), str) and payload["manifest_id"]
            and isinstance(payload.get("checkpoint_id"), str) and payload["checkpoint_id"]
            and isinstance(payload.get("proof_hash"), str) and payload["proof_hash"]
            else "invalid_payload"
        )
    if operation == "wait_answer":
        return (
            None
            if set(payload) == {"wait_id", "generation", "answer"}
            and isinstance(payload.get("wait_id"), str) and payload["wait_id"]
            and type(payload.get("generation")) is int
            else "invalid_payload"
        )
    if operation == "wait_decline":
        return (
            None
            if set(payload).issubset({"wait_id", "generation", "reason"})
            and {"wait_id", "generation"}.issubset(payload)
            and isinstance(payload.get("wait_id"), str) and payload["wait_id"]
            and type(payload.get("generation")) is int
            and (payload.get("reason") is None or isinstance(payload.get("reason"), str))
            else "invalid_payload"
        )
    return "invalid_command"


def _authorize_execution(
    actor: dict | None,
    action: str,
    execution,
    *,
    bound_session: str | None = None,
) -> Any:
    """Authorize one exact target without exposing cross-scope existence."""
    from openprogram.execution.authorization import authorize_execution_action
    from openprogram.execution.public import project_id_for_session

    if bound_session is not None and bound_session != execution.session_id:
        from openprogram.execution.authorization import ExecutionAuthorizationError
        raise ExecutionAuthorizationError("execution is not visible")
    return authorize_execution_action(
        actor or {}, action, execution,
        {"project_id": project_id_for_session(execution.session_id),
         "session_id": execution.session_id},
    )


def _public_event(event) -> dict:
    """The reconnect transport never exposes raw prompt/output payloads."""
    from openprogram.execution.audit import redact_audit_payload

    return {
        "sequence": event.execution_sequence,
        "execution_id": event.execution_id,
        "kind": event.kind,
        "payload": redact_audit_payload(event.payload),
        "execution_version": event.execution_version,
        "command_id": event.command_id,
    }


def _public_execution_snapshot(execution) -> tuple[dict, dict]:
    """Return the one snapshot shape shared by command and reconnect paths."""
    from openprogram.execution import default_store
    from openprogram.execution.public import execution_snapshot

    execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
    resource = None
    job = None
    try:
        from openprogram.agent.job.runner import runner_for_execution_store

        runner = runner_for_execution_store(default_store())
        view = runner.get_job_resource_view(execution_data["execution_id"]) if runner else None
        if view is not None:
            resource = view.to_dict()
            job = runner.get_job(execution_data["execution_id"])
    except Exception:
        pass
    record = execution if hasattr(execution, "execution_id") else default_store().get_execution(execution_data["execution_id"])
    if record is not None:
        execution_data = execution_snapshot(
            record, store=default_store(), resource=resource,
            job_id=getattr(job, "id", None), job=job,
        ).to_dict()
    cursor = {
        "execution_id": execution_data.get("execution_id"),
        "next_sequence": int(execution_data.get("event_sequence") or 0) + 1,
        "snapshot_status_version": execution_data.get("status_version"),
    }
    return execution_data, cursor


async def _send_command_update(ws, command, execution) -> None:
    # Job activation is asynchronous and may finish between command acceptance
    # and transport serialization.  Refresh the command and execution together
    # until the command status is stable, so a response never combines an old
    # accepted command with a newer terminal resource snapshot.
    command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
    execution_data, cursor = _public_execution_snapshot(execution)
    for _ in range(3):
        try:
            from openprogram.execution import default_store

            store = default_store()
            latest_command = store.get_command(command_data.get("command_id", ""))
            latest_execution = store.get_execution(command_data.get("execution_id", ""))
            if latest_command is not None:
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
    if command_data.get("kind") == "execution.step":
        checkpoint_id = getattr(execution, "checkpoint_head_id", None)
        if checkpoint_id:
            try:
                from openprogram.execution import ExecutionCheckpointStore, default_store

                checkpoint = ExecutionCheckpointStore(default_store()).get(checkpoint_id)
                command_data["managed_action_count"] = len(
                    checkpoint.completed_actions if checkpoint is not None else (),
                )
            except Exception:
                command_data["managed_action_count"] = 0
        else:
            command_data["managed_action_count"] = 0
    await ws.send_text(json.dumps({
        "type": "execution.command.updated", "command": command_data,
        "execution": execution_data, "event_cursor": cursor,
        "data": {"command": command_data, "execution": execution_data,
                 "event_cursor": cursor},
    }, default=str))
    await ws.send_text(json.dumps({
        "type": "execution.updated", "execution": execution_data,
        "event_cursor": cursor,
        "data": {"execution": execution_data, "event_cursor": cursor},
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


async def submit_execution_control(
    cmd: dict,
    operation: str,
    *,
    actor: dict | None,
    bound_session: str | None = None,
    surface: str | None = None,
):
    """Submit one authenticated exact command through RuntimeControlService."""
    from openprogram.execution import default_control_service, default_store
    from openprogram.execution.store import ExecutionConflict, CommandConflict
    from openprogram.execution.attempts import AttemptConflict
    from openprogram.execution.state_machine import InvalidCommand

    from openprogram.agent.authority import has_capability, normalize_authority

    raw_actor = dict(actor) if isinstance(actor, dict) else {}
    actor = normalize_authority(raw_actor)
    validation_error = validate_execution_command_request(cmd, operation)
    execution_id = cmd.get("execution_id")
    command_id = cmd.get("command_id")
    expected_version = cmd.get("expected_version")
    if not actor or not has_capability(actor, "runtime.control") or validation_error is not None:
        return _rejected_command(cmd, validation_error or "unauthorized"), {
            "execution_id": execution_id or "", "status_version": None,
        }
    store = default_store()
    service = default_control_service()
    execution = store.get_execution(execution_id)
    if execution is None:
        return _rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        }
    try:
        authorization = _authorize_execution(
            raw_actor, _PUBLIC_COMMAND_ACTIONS[operation], execution,
            bound_session=bound_session,
        )
        # Command and audit records retain only transport-trusted control
        # metadata.  The execution binding is resolved by the server and is
        # stored explicitly so later audit readers can reconstruct the exact
        # authorization decision without trusting command input.
        if actor:
            for field in ("project_ids", "session_ids", "execution_actions"):
                value = raw_actor.get(field)
                if isinstance(value, (list, tuple, frozenset, set)):
                    actor[field] = tuple(str(item) for item in value)
            actor["resolved_project_id"] = authorization.project_binding["project_id"]
            actor["resolved_session_id"] = authorization.project_binding["session_id"]
            actor["surface"] = surface if surface is not None else str(raw_actor.get("surface") or "runtime")
    except Exception:
        return _rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        }
    try:
        from openprogram.agent.job.runner import runner_for_execution_store

        job_runner = runner_for_execution_store(store)
        is_job = job_runner is not None and store.get_job_agent_input(execution_id) is not None
        if is_job:
            # The Job runner owns its resource saga and canonical control
            # hooks, including transport-neutral resource release.
            service = job_runner._execution_control
        if operation in {"wait_answer", "wait_decline"}:
            payload = cmd.get("payload")
            if not isinstance(payload, dict):
                raise ExecutionConflict("invalid_wait", "wait command requires a payload")
            allowed = (
                {"wait_id", "generation", "answer"}
                if operation == "wait_answer" else {"wait_id", "generation", "reason"}
            )
            if set(payload) - allowed or not isinstance(payload.get("wait_id"), str) or not payload["wait_id"] or type(payload.get("generation")) is not int:
                raise ExecutionConflict("invalid_wait", "wait command payload is invalid")
            request = service.request_wait_answer if operation == "wait_answer" else service.request_wait_decline
            dispatch = await request(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                wait_id=payload["wait_id"], generation=payload["generation"],
                **({"answer": payload.get("answer")} if operation == "wait_answer" else {"reason": payload.get("reason")}),
            )
            return dispatch.command, dispatch.execution
        if operation in {"pause", "continue", "step", "steer", "fork", "retry"}:
            from openprogram.execution.model import CommandKind
            required = {
                "pause": CommandKind.PAUSE,
                "continue": CommandKind.CONTINUE,
                "step": CommandKind.STEP,
                "steer": CommandKind.STEER,
                "fork": CommandKind.FORK,
                "retry": CommandKind.RETRY,
            }[operation]
            if not getattr(execution.capabilities, {
                "pause": "pause", "continue": "pause", "step": "step",
                "steer": "steer", "fork": "fork", "retry": "retry",
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
        if is_job and operation in {"continue", "step"}:
            command, latest = job_runner.queue_job_resume(
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                actor=actor,
                step=operation == "step",
            )
            return command, latest
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
        elif operation == "steer":
            dispatch = service.request_steer(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                payload=dict(cmd["payload"]),
            )
        elif operation == "fork":
            branch = service.request_fork(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                manifest_id=cmd["payload"]["manifest_id"],
                checkpoint_id=cmd["payload"]["checkpoint_id"],
                proof_hash=cmd["payload"]["proof_hash"],
            )
            return branch.command, branch.execution
        elif operation == "retry":
            branch = service.request_retry(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                checkpoint_id=cmd["payload"].get("checkpoint_id"),
            )
            return branch.command, branch.execution
        else:
            request = (
                service.request_continue if operation == "continue"
                else service.request_step
            )
            dispatch = await request(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
            )
        return dispatch.command, dispatch.execution
    except (ExecutionConflict, CommandConflict, AttemptConflict, InvalidCommand) as exc:
        current = store.get_execution(execution_id)
        return _rejected_command(
            cmd,
            "unsupported_capability" if getattr(exc, "code", None) == "unsupported" else getattr(exc, "code", "command_rejected"),
            current.to_dict() if current is not None else None,
        ), current if current is not None else {
            "execution_id": execution_id, "status_version": None,
        }


async def _handle_execution_control(ws, cmd: dict, operation: str) -> None:
    """Submit an exact durable runtime command; drivers never see WS input."""
    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    command, execution = await submit_execution_control(
        cmd,
        operation,
        actor=_trusted_runtime_actor(ws),
        bound_session=bound_session if isinstance(bound_session, str) else None,
        surface="ws",
    )
    await _send_command_update(ws, command, execution)
    execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
    if operation == "cancel" and execution_data.get("status") in {"cancelling", "cancelled"}:
        from openprogram.webui import server as _s
        _s._release_session_occupancy_for_execution(execution_data)


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
    from openprogram.execution import default_store
    from openprogram.execution.store import ExecutionConflict

    execution_id = cmd.get("execution_id")
    after_sequence = cmd.get("after_sequence")
    if not isinstance(execution_id, str) or not execution_id or type(after_sequence) is not int:
        await ws.send_text(json.dumps({"type": "execution.replay", "error": "invalid_command"}))
        return
    store = default_store()
    execution = store.get_execution(execution_id)
    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    try:
        if execution is None:
            raise ExecutionConflict("not_found", "execution is not visible")
        _authorize_execution(
            _trusted_runtime_actor(ws), "execution.events", execution,
            bound_session=bound_session if isinstance(bound_session, str) else None,
        )
        replay = store.read_event_replay(execution_id, after_sequence=after_sequence)
    except Exception:
        await ws.send_text(json.dumps({"type": "execution.replay", "error": "not_found", "execution_id": execution_id}))
        return
    snapshot, cursor = _public_execution_snapshot(execution)
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
    from openprogram.execution import default_store
    from openprogram.execution.revision_public import (
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
