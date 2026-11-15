"""Submitting one public runtime-control command for an execution.

Every interface (WebSocket, REST, the browser Page controls) resolves the
caller's authority from its authenticated transport state with
``trusted_runtime_actor`` and hands the command envelope to
``submit_execution_control``, which validates it, authorizes the exact
target and dispatches it to the runtime control service or the Job runner.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable


def trusted_runtime_actor(scope, *, surface: str | None = None) -> dict | None:
    """Resolve runtime-control authority from authenticated transport state."""
    from openprogram.state.authority import normalize_authority

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


PUBLIC_COMMAND_ACTIONS = {
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


#: What this Worker's runtime control does, announced to every socket on
#: connect (the ``worker_capabilities`` frame), so a client drops a fallback
#: only for a Worker that makes it needless:
#: ``cancel_ignores_version``  a stop is never refused as stale;
#: ``cancel_by_request``       a stop may name a send (session id and
#:                              ``client_request_id``) before its execution
#:                              id is known.
CONTROL_CAPABILITIES = ("cancel_ignores_version", "cancel_by_request")


STEER_ATTACHMENT_LIMIT = 20

# Saves a steer's attachments where the agent can read them and returns the
# payload the command stores: ``(session_id, payload) -> payload``. The
# transport passes it; the control plane never reaches into the interfaces.
SteerPayloadPreparer = Callable[[str, dict], Awaitable[dict]]


def steer_attachments_digest(attachments: list) -> str:
    """Identity of a steer's incoming attachments, kept with the command so a
    retried request with the same ``command_id`` stays idempotent."""
    import hashlib
    import json

    encoded = json.dumps(attachments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_execution_command_request(cmd: dict, operation: str) -> str | None:
    """Validate the one public command envelope before touching a runtime.

    Target identity, actor, session, project, lease, and capability data are
    server-owned.  A transport cannot supply any of them as a second control
    path.
    """
    if operation not in PUBLIC_COMMAND_ACTIONS or not isinstance(cmd, dict):
        return "invalid_command"
    if set(cmd) - {"type", "action", "command_id", "execution_id", "expected_version", "payload"}:
        return "invalid_command"
    if cmd.get("type") != "execution.command" or cmd.get("action") != PUBLIC_COMMAND_ACTIONS[operation]:
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
    if operation == "continue":
        return None if not payload or (set(payload) == {"code_change_policy"} and payload["code_change_policy"] in ("keep_original", "use_latest")) else "invalid_payload"
    if operation in {"pause", "step", "cancel"}:
        return None if not payload else "invalid_payload"
    if operation == "steer":
        message = payload.get("message")
        attachments = payload.get("attachments", [])
        return (
            None
            if set(payload) - {"attachments"} == {"message"} and isinstance(message, str)
            and len(message) <= 4096
            and isinstance(attachments, list) and len(attachments) <= STEER_ATTACHMENT_LIMIT
            and all(isinstance(item, dict) for item in attachments)
            and (message.strip() or attachments)
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


#: Fields of a stop that names a send (``session_id`` and the send's
#: ``client_request_id``) instead of an execution, for a client that has not
#: been told the execution id yet.
_REQUEST_TARGET_FIELDS = frozenset({
    "type", "action", "command_id", "session_id", "client_request_id",
    "expected_version", "payload",
})


def names_request(cmd: object) -> bool:
    """Whether ``cmd`` is a stop naming a send rather than an execution."""
    return (
        isinstance(cmd, dict) and "execution_id" not in cmd
        and "client_request_id" in cmd and "session_id" in cmd
    )


def validate_request_target(cmd: dict) -> str | None:
    """Validate a stop that names a send: only a cancel may, with no version
    needed, since a send has none before its execution exists."""
    if set(cmd) - _REQUEST_TARGET_FIELDS:
        return "invalid_command"
    if cmd.get("type") != "execution.command" or cmd.get("action") != PUBLIC_COMMAND_ACTIONS["cancel"]:
        return "invalid_command"
    command_id = cmd.get("command_id")
    session_id = cmd.get("session_id")
    request_id = cmd.get("client_request_id")
    expected_version = cmd.get("expected_version", 0)
    if (
        not isinstance(command_id, str) or not command_id or len(command_id) > 256
        or not isinstance(session_id, str) or not session_id or len(session_id) > 256
        or not isinstance(request_id, str) or not request_id or len(request_id) > 128
        or type(expected_version) is not int or expected_version < 0
    ):
        return "invalid_command"
    return None if not cmd.get("payload", {}) else "invalid_payload"


def stop_marked_command(cmd: dict) -> dict:
    """The answer to a stop of a send no execution was admitted for yet: the
    stop is kept as a mark, and the send's admission ends as stopped."""
    return {
        "command_id": str(cmd.get("command_id") or ""),
        "execution_id": "",
        "kind": "cancel",
        "status": "marked",
        "session_id": cmd.get("session_id"),
        "client_request_id": cmd.get("client_request_id"),
        "result_version": None,
        "rejection_code": None,
    }


def _resolve_request_target(cmd: dict, raw_actor: dict, actor: dict | None,
                            bound_session: str | None, surface: str | None):
    """Turn a stop that names a send into a stop of its execution.

    Returns ``(cmd, None)`` with ``execution_id`` filled in once the send
    was admitted, or ``(None, answer)`` when the stop was refused or kept as
    a mark for the send's admission.
    """
    from openprogram.state.authority import has_capability
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.authorization import authorize_session_action
    from openprogram.runtime.execution.public import project_id_for_session

    session_id = cmd.get("session_id")
    placeholder = {"execution_id": "", "status_version": None}
    error = validate_request_target(cmd)
    if not actor or not has_capability(actor, "runtime.control") or error is not None:
        return None, (rejected_command(cmd, error or "unauthorized"), placeholder)
    try:
        if bound_session is not None and bound_session != session_id:
            raise PermissionError("session is not visible")
        authorization = authorize_session_action(
            raw_actor, PUBLIC_COMMAND_ACTIONS["cancel"],
            {"project_id": project_id_for_session(session_id), "session_id": session_id},
        )
    except Exception:
        return None, (rejected_command(cmd, "not_found"), placeholder)
    marked_by = dict(actor)
    marked_by["resolved_project_id"] = authorization.project_binding["project_id"]
    marked_by["resolved_session_id"] = authorization.project_binding["session_id"]
    marked_by["surface"] = surface if surface is not None else str(raw_actor.get("surface") or "runtime")
    from openprogram.runtime.execution.store import ExecutionConflict
    try:
        execution_id, _mark = default_store().stop_by_request(
            session_id=session_id,
            client_request_id=cmd["client_request_id"],
            command_id=cmd["command_id"],
            actor=marked_by,
            reason_code="cancel.user",
        )
    except ExecutionConflict as exc:
        return None, (rejected_command(cmd, exc.code), placeholder)
    if execution_id is None:
        return None, (stop_marked_command(cmd), {**placeholder, "session_id": session_id})
    return {
        "type": cmd["type"], "action": cmd["action"], "command_id": cmd["command_id"],
        "execution_id": execution_id, "expected_version": cmd.get("expected_version", 0),
        "payload": {},
    }, None


def authorize_execution_target(
    actor: dict | None,
    action: str,
    execution,
    *,
    bound_session: str | None = None,
) -> Any:
    """Authorize one exact target without exposing cross-scope existence."""
    from openprogram.runtime.execution.authorization import authorize_execution_action
    from openprogram.runtime.execution.public import project_id_for_session

    if bound_session is not None and bound_session != execution.session_id:
        from openprogram.runtime.execution.authorization import ExecutionAuthorizationError
        raise ExecutionAuthorizationError("execution is not visible")
    return authorize_execution_action(
        actor or {}, action, execution,
        {"project_id": project_id_for_session(execution.session_id),
         "session_id": execution.session_id},
    )


def already_terminal_command(cmd: dict, execution) -> dict:
    """The answer to a stop of an execution that had already ended: not a
    refusal, since nothing is left to stop, and no command was written."""
    return {
        "command_id": str(cmd.get("command_id") or ""),
        "execution_id": execution.execution_id,
        "kind": "cancel",
        "status": "already_terminal",
        "result_version": execution.status_version,
        "rejection_code": None,
        "latest_snapshot": execution.to_dict(),
    }


def rejected_command(cmd: dict, code: str, latest_snapshot: dict | None = None) -> dict:
    value = {
        "command_id": str(cmd.get("command_id") or ""),
        "execution_id": str(cmd.get("execution_id") or ""),
        "status": "rejected", "result_version": None,
        "rejection_code": code,
    }
    if latest_snapshot is not None:
        value["latest_snapshot"] = latest_snapshot
    return value


def _authorized_target(store, cmd: dict, operation: str, raw_actor: dict, actor: dict,
                       bound_session: str | None, surface: str | None,
                       conversation_session_id: str | None):
    """Read and authorize the command's execution, in a worker thread.

    Returns ``(execution, actor, None)``, or ``(None, None, answer)`` when
    the command is refused. ``actor`` gains the server-resolved binding:
    command and audit records retain only transport-trusted control
    metadata, and the execution binding is stored explicitly so later audit
    readers can reconstruct the exact authorization decision without
    trusting command input."""
    execution_id = cmd.get("execution_id")
    execution = store.get_execution(execution_id)
    if execution is None:
        return None, None, (rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        })
    try:
        if conversation_session_id:
            from openprogram.runtime.execution.conversation_scope import authorize_conversation_execution

            authorization = authorize_conversation_execution(
                raw_actor, PUBLIC_COMMAND_ACTIONS[operation], execution,
                store=store, session_id=conversation_session_id, bound_session=None,
            )
        else:
            authorization = authorize_execution_target(
                raw_actor, PUBLIC_COMMAND_ACTIONS[operation], execution,
                bound_session=bound_session,
            )
        if actor:
            for field in ("project_ids", "session_ids", "execution_actions"):
                value = raw_actor.get(field)
                if isinstance(value, (list, tuple, frozenset, set)):
                    actor[field] = tuple(str(item) for item in value)
            actor["resolved_project_id"] = authorization.project_binding["project_id"]
            actor["resolved_session_id"] = authorization.project_binding["session_id"]
            actor["surface"] = surface if surface is not None else str(raw_actor.get("surface") or "runtime")
    except Exception:
        return None, None, (rejected_command(cmd, "not_found"), {
            "execution_id": execution_id, "status_version": None,
        })
    return execution, actor, None


def _job_runner_for(store, execution_id: str):
    """``(job_runner, is_job)``: the Job runner owns its resource saga and
    canonical control hooks, including transport-neutral resource release.
    A Job runner exists only where the Jobs package is loaded."""
    from .. import hooks

    job_runner = hooks.job_runner(store)
    is_job = job_runner is not None and store.get_job_agent_input(execution_id) is not None
    return job_runner, is_job


def _check_resume(store, service, execution, command_id: str, operation: str) -> None:
    """Refuse a new continue or step while an external effect is unresolved.

    A retry of an already persisted resume command must remain a pure
    idempotent read.  During a step, its tool effect is legitimately
    ``dispatched`` before the safe point commits it.  Recovering the owner
    for the duplicate transport request would fence the live attempt while
    it is still executing, so its safe-point commit would fail with
    ``stale_attempt`` and force reconciliation."""
    from openprogram.runtime.execution.store import ExecutionConflict

    execution_id = execution.execution_id
    if store.get_command(command_id) is not None:
        return
    if not service.effects.list_unresolved(execution_id):
        return
    if execution.current_attempt_id is not None:
        generation = execution.owner_lease.get("generation")
        if isinstance(generation, int):
            service.recover_owner_loss(
                execution_id, attempt_id=execution.current_attempt_id,
                generation=generation,
            )
    raise ExecutionConflict("unresolved_effect", "execution has an unresolved external effect")


async def submit_execution_control(
    cmd: dict,
    operation: str,
    *,
    actor: dict | None,
    bound_session: str | None = None,
    surface: str | None = None,
    conversation_session_id: str | None = None,
    prepare_steer: SteerPayloadPreparer | None = None,
):
    """Submit one authenticated exact command through RuntimeControlService.

    Awaited on the caller's event loop. Every read and write of the
    execution's records (they live under its conversation's lock) runs in a
    worker thread of the execution's lane (``control.shared.off_loop``), in
    the order the command needs them; only the owner's signal, an
    activation and a steer's attachments are awaited here. A held
    conversation therefore delays this command, never the loop, and a stop
    still reaches a running owner as soon as its command is written.
    """
    from openprogram.runtime.execution import default_control_service, default_store
    from openprogram.runtime.execution.store import ExecutionConflict, CommandConflict
    from openprogram.runtime.execution.attempts import AttemptConflict
    from openprogram.runtime.execution.state_machine import InvalidCommand

    from openprogram.state.authority import has_capability, normalize_authority

    from .shared import off_loop

    raw_actor = dict(actor) if isinstance(actor, dict) else {}
    actor = normalize_authority(raw_actor)
    if operation == "cancel" and names_request(cmd):
        # A stop sent before the client learnt the execution id names the
        # send; it becomes a stop of that execution, or a mark the send's
        # admission takes.
        resolved, answer = await off_loop(
            f"request:{cmd.get('session_id')}:{cmd.get('client_request_id')}",
            _resolve_request_target, cmd, raw_actor, actor, bound_session, surface,
        )
        if answer is not None:
            return answer
        cmd = resolved
    validation_error = validate_execution_command_request(cmd, operation)
    execution_id = cmd.get("execution_id")
    command_id = cmd.get("command_id")
    expected_version = cmd.get("expected_version")
    if not actor or not has_capability(actor, "runtime.control") or validation_error is not None:
        return rejected_command(cmd, validation_error or "unauthorized"), {
            "execution_id": execution_id or "", "status_version": None,
        }
    store = default_store()
    service = default_control_service()
    execution, actor, refusal = await off_loop(
        execution_id, _authorized_target, store, cmd, operation, raw_actor, actor,
        bound_session, surface, conversation_session_id,
    )
    if refusal is not None:
        return refusal
    try:
        job_runner, is_job = await off_loop(execution_id, _job_runner_for, store, execution_id)
        if is_job:
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
            if not getattr(execution.capabilities, {
                "pause": "pause", "continue": "pause", "step": "step",
                "steer": "steer", "fork": "fork", "retry": "retry",
            }[operation]):
                raise ExecutionConflict("unsupported", "execution does not support this control command")
        if operation in {"continue", "step"}:
            await off_loop(execution_id, _check_resume, store, service, execution, command_id, operation)
        if is_job and operation in {"continue", "step"}:
            command, latest = await off_loop(
                execution_id, job_runner.queue_job_resume,
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
            # A stop names its execution; the version is only recorded.
            # ``reason_code`` is server policy, not caller-controlled input.
            # What the turn waits on (a synchronous sub-agent) stops with it.
            from openprogram.runtime.execution.run_control.cancellation import (
                stop_with_descendants,
            )

            dispatch = await stop_with_descendants(
                execution_id,
                lambda: service.request_cancel(
                    command_id=command_id, execution_id=execution_id,
                    expected_version=expected_version, actor=actor,
                    reason_code="cancel.user",
                ),
                waited_on_only=True,
            )
            if dispatch.command is None:
                return already_terminal_command(cmd, dispatch.execution), dispatch.execution
        elif operation == "steer":
            payload = dict(cmd["payload"])
            if payload.get("attachments"):
                digest = steer_attachments_digest(payload["attachments"])
                existing = await off_loop(execution_id, store.get_command, command_id)
                if existing is not None:
                    # A retry: the files were saved the first time. A
                    # different batch under the same id fails the fingerprint.
                    payload = dict(existing.payload)
                    if payload.get("attachments_digest") != digest:
                        raise CommandConflict(
                            "idempotency_collision",
                            f"command_id was already used for a different request: {command_id}",
                        )
                elif prepare_steer is None:
                    raise ExecutionConflict("unsupported", "this transport cannot deliver attachments")
                else:
                    try:
                        payload = await prepare_steer(execution.session_id, payload)
                    except ValueError as exc:
                        raise ExecutionConflict("invalid_attachment", str(exc)) from exc
                    payload["attachments_digest"] = digest
            else:
                payload.pop("attachments", None)
            dispatch = await off_loop(
                execution_id, service.request_steer,
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                payload=payload,
            )
        elif operation == "fork":
            branch = await off_loop(
                execution_id, service.request_fork,
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor,
                manifest_id=cmd["payload"]["manifest_id"],
                checkpoint_id=cmd["payload"]["checkpoint_id"],
                proof_hash=cmd["payload"]["proof_hash"],
            )
            return branch.command, branch.execution
        elif operation == "retry":
            branch = await off_loop(
                execution_id, service.request_retry,
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
            extra = {"code_change_policy": cmd.get("payload", {}).get("code_change_policy")} if operation == "continue" and cmd.get("payload") else {}
            dispatch = await request(
                command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, actor=actor, **extra,
            )
        return dispatch.command, dispatch.execution
    except (ExecutionConflict, CommandConflict, AttemptConflict, InvalidCommand) as exc:
        current = await off_loop(execution_id, store.get_execution, execution_id)
        return rejected_command(
            cmd,
            "unsupported_capability" if getattr(exc, "code", None) == "unsupported" else getattr(exc, "code", "command_rejected"),
            current.to_dict() if current is not None else None,
        ), current if current is not None else {
            "execution_id": execution_id, "status_version": None,
        }
