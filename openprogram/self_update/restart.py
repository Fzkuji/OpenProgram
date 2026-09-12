"""Pause ownership and restart recovery through canonical execution commands."""

from __future__ import annotations

import asyncio
import logging

from .maintenance import load_maintenance
from .projection import _optional, _records
from .store import SelfUpdateStore
from .types import UpdatePhase, is_terminal
from .verification_channel import _digest

_log = logging.getLogger(__name__)


def _command_id(update_id, execution_id, operation):
    return f"self-update:{update_id}:{operation}:{_digest(execution_id)[:32]}"


def _load(store, record):
    path = store.root / record.request.update_id / "restart.json"
    value = _optional(path)
    if value is None:
        return path, dict(schema=1, update_id=record.request.update_id, executions={})
    if (
        set(value) != {"schema", "update_id", "executions"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["update_id"] != record.request.update_id
        or not isinstance(value["executions"], dict)
    ):
        raise ValueError("invalid self-update restart record")
    for execution_id, item in value["executions"].items():
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or not isinstance(item, dict)
            or set(item) != {"version", "status", "error"}
            or type(item["version"]) is not int
            or item["version"] < 1
            or item["status"]
            not in {"pending", "resuming", "resumed", "replanned", "skipped", "failed"}
            or not isinstance(item["error"], str)
        ):
            raise ValueError("invalid self-update restart execution")
    return path, value


def reconcile(runner) -> None:
    """Worker-owned reconciliation; call only after runner initialization.

    Never activate code while holding the self-update lock. The admission
    guard remains responsible for races with a subsequent update.
    """
    store = SelfUpdateStore()
    if not store.root.exists():
        return
    with store._locked():
        marker = load_maintenance(store)
        records = (
            [store._load_unlocked(marker["update_id"])]
            if marker
            else list(_records(store))
        )
    for record in records:
        try:
            if marker and record.state.phase is UpdatePhase.READY:
                _pause(store, record, runner)
            elif marker is None and is_terminal(record.state.phase):
                _resume(store, record, runner)
        except Exception:
            _log.exception(
                "self-update execution recovery failed for %s", record.request.update_id
            )


def _service(runner, execution_id):
    if runner._execution_store.get_job_agent_input(execution_id) is not None:
        return runner._execution_control
    from openprogram.execution.control import default_control_service

    return default_control_service()


def _pause(store, record, runner):
    from openprogram.execution.model import ExecutionStatus

    executions = runner._execution_store
    with store._locked():
        marker = load_maintenance(store)
        if marker is None or marker["update_id"] != record.request.update_id:
            return
        if (
            store._load_unlocked(record.request.update_id).state.phase
            is not UpdatePhase.READY
        ):
            return
        path, manifest = _load(store, record)
        for execution in executions.list_nonterminal():
            if (
                execution.status is ExecutionStatus.RUNNING
                and execution.capabilities.pause
            ):
                manifest["executions"].setdefault(
                    execution.execution_id,
                    dict(version=execution.status_version, status="pending", error=""),
                )
        # Intent precedes command acceptance, including a crash between them.
        store._write_json(path, manifest)
        for execution_id, item in manifest["executions"].items():
            if item["status"] != "pending":
                continue
            command_id = _command_id(record.request.update_id, execution_id, "pause")
            if executions.get_command(command_id) is not None:
                continue
            execution = executions.get_execution(execution_id)
            if execution is None or execution.status_version != item["version"]:
                item.update(status="skipped", error="execution changed before pause")
                continue
            try:
                asyncio.run(
                    _service(runner, execution_id).request_pause(
                        command_id=command_id,
                        execution_id=execution_id,
                        expected_version=item["version"],
                        actor={
                            "surface": "self-update",
                            "update_id": record.request.update_id,
                        },
                    )
                )
            except Exception as exc:
                # A durable command may already exist; the next pass observes it.
                if executions.get_command(command_id) is None:
                    item.update(status="failed", error=type(exc).__name__)
        store._write_json(path, manifest)


def _resume(store, record, runner):
    from openprogram.execution.model import CommandStatus, ExecutionStatus

    executions = runner._execution_store
    with store._locked():
        path, manifest = _load(store, record)
        if not path.exists() or load_maintenance(store) is not None:
            return
    for execution_id, item in manifest["executions"].items():
        if item["status"] not in {"pending", "resuming", "failed"}:
            continue
        pause = executions.get_command(
            _command_id(record.request.update_id, execution_id, "pause")
        )
        execution = executions.get_execution(execution_id)
        resume_id = _command_id(record.request.update_id, execution_id, "continue")
        resumed = executions.get_command(resume_id)
        if resumed is not None:
            item.update(
                status=(
                    "failed"
                    if resumed.status is CommandStatus.REJECTED
                    else "resumed"
                    if resumed.status is CommandStatus.APPLIED
                    else "resuming"
                ),
                error=resumed.rejection_code or "",
            )
            if (
                item["status"] == "failed"
                and execution is not None
                and execution.status is ExecutionStatus.PAUSED
                and execution.reason_code == "continuation_contract_mismatch"
            ):
                if _replan(store, record, runner, execution):
                    item.update(
                        status="replanned", error="continuation_contract_mismatch"
                    )
        elif pause is None or execution is None:
            item.update(status="skipped", error="pause was not accepted")
        elif pause.status in {CommandStatus.ACCEPTED, CommandStatus.APPLYING}:
            continue  # An aborted update may still have an in-flight pause.
        elif (
            pause.status is not CommandStatus.APPLIED
            or execution.status is not ExecutionStatus.PAUSED
            or execution.status_version != pause.result_version
        ):
            item.update(status="skipped", error="execution changed after update pause")
        else:
            try:
                if executions.get_job_agent_input(execution_id) is not None:
                    runner.queue_job_resume(
                        command_id=resume_id,
                        execution_id=execution_id,
                        expected_version=execution.status_version,
                        actor={
                            "surface": "self-update",
                            "update_id": record.request.update_id,
                        },
                        step=False,
                    )
                else:
                    asyncio.run(
                        _service(runner, execution_id).request_continue(
                            command_id=resume_id,
                            execution_id=execution_id,
                            expected_version=execution.status_version,
                            actor={
                                "surface": "self-update",
                                "update_id": record.request.update_id,
                            },
                        )
                    )
                command = executions.get_command(resume_id)
                item.update(
                    status=(
                        "failed"
                        if command and command.status is CommandStatus.REJECTED
                        else "resumed"
                        if command and command.status is CommandStatus.APPLIED
                        else "resuming"
                    ),
                    error=command.rejection_code or "" if command else "",
                )
            except Exception as exc:
                item["error"] = getattr(exc, "code", type(exc).__name__)
                # Keep retryable persistence/activation failures pending; canonical
                # command state decides whether any attempt may be repeated.
                _log.warning(
                    "self-update continue %s failed: %s", execution_id, item["error"]
                )
        with store._locked():
            current_path, current = _load(store, record)
            current["executions"][execution_id] = item
            store._write_json(current_path, current)


def _replan_path(store, update_id, execution_id):
    return store.root / update_id / f"replan-{_digest(execution_id)[:32]}.json"


def _replan(store, record, runner, execution):
    """Start a new planning turn, never replay a mismatched pending operation."""
    from openprogram.agent.authority import normalize_authority
    from openprogram.agent.session_db import default_db
    from .continuation import blocking_executions
    from copy import deepcopy
    import json

    executions = runner._execution_store
    job_id = _command_id(record.request.update_id, execution.execution_id, "replan")
    if runner.get_job(job_id) is not None:
        return True
    payload = executions.get_job_agent_input(
        execution.execution_id
    ) or executions.get_agent_turn_input(execution.execution_id)
    if payload is None or payload.get("kind") not in {"chat", "job_agent"}:
        return False
    from openprogram.execution.model import _thaw_json

    payload = _thaw_json(payload)
    values = (
        payload["turn_request"]
        if payload["kind"] == "job_agent"
        else payload["request"]
    )
    authority = normalize_authority(values)
    if not authority or _service(
        runner, execution.execution_id
    ).effects.list_unresolved(execution.execution_id):
        return False
    authority.update(speaker_kind="agent", interaction="background")
    source = executions.get_execution_input(execution.execution_id)
    if source is None:
        return False
    with store._locked():
        if load_maintenance(store) is not None:
            return False
        current = executions.get_execution(execution.execution_id)
        if current is None or current.status_version != execution.status_version:
            return False
        db = default_db()
        with db._session_lock(execution.session_id):
            pair = db._open(execution.session_id)
            if pair is None:
                return False
            # A different active turn owns this conversation. Paused failures
            # from this update do not suppress their own recovery planning.
            if blocking_executions(store, record, executions, execution.session_id):
                return False
            parent = source.assistant_message_id or source.user_message_id
            if parent not in pair[1].nodes_by_id:
                return False
            path = _replan_path(store, record.request.update_id, execution.execution_id)
            config = _optional(path)
            if config is None:
                prompt = (
                    "Continue the interrupted task under the updated runtime. The old checkpoint was not "
                    "replayed because its tool contract changed (continuation_contract_mismatch). "
                    "Use the saved conversation to identify completed work and plan the remaining work afresh. "
                    "Do not assume a pending tool succeeded and do not repeat completed external actions. "
                    "The old execution remains paused as evidence; this is a new planning turn.\n"
                    + json.dumps(
                        {
                            "original_request": values.get("user_text", ""),
                            "execution_id": execution.execution_id,
                            "checkpoint_id": execution.checkpoint_head_id,
                        },
                        ensure_ascii=False,
                    )
                )
                config = dict(
                    schema=1,
                    execution_id=execution.execution_id,
                    source_hash=_digest(payload),
                    parent_msg_id=parent,
                    prompt=prompt,
                    authority=authority,
                    permission=dict(
                        mode=values.get("permission_mode", "ask"),
                        rules=deepcopy(values.get("permission_rules")),
                    ),
                )
                store._write_json(path, config)
            inputs = dict(
                session_id=execution.session_id,
                job_id=job_id,
                prompt=config["prompt"],
                agent_id=values["agent_id"],
                source="self_update_replan",
                context_mode="inherit",
                parent_msg_id=config["parent_msg_id"],
                caller_msg_id=parent,
                spawn_caller=parent,
                advance_head=values.get("advance_head", True),
                wait=True,
                creates_agent=False,
                label="Continue after runtime change",
                profile_snapshot=values.get("profile_snapshot"),
                model_override=values.get("model_override"),
                tools_override=values.get("tools_override"),
                authority=config["authority"],
                worktree_id=(payload.get("job_context") or {}).get("worktree_id"),
            )
    runner.spawn_job(**inputs)
    return True


def replan_permission_snapshot(executions, job):
    """Recover policy from the exact old execution, not a caller's source label."""
    from openprogram.agent.authority import normalize_authority, owner_principal_id

    parts = job.id.split(":")
    if len(parts) != 4 or parts[0] != "self-update" or parts[2] != "replan":
        raise ValueError("invalid replan Job identity")
    store = SelfUpdateStore()
    with store._locked():
        record = store._load_unlocked(parts[1])
        from .continuation import recovery_failures

        failures = recovery_failures(store, record, executions)
        source_id = next(
            (eid for eid in failures if _command_id(parts[1], eid, "replan") == job.id),
            None,
        )
        if source_id is None:
            raise ValueError("replan requires this update's rejected continuation")
        payload = executions.get_job_agent_input(
            source_id
        ) or executions.get_agent_turn_input(source_id)
        config = _optional(_replan_path(store, parts[1], source_id))
        if config is None or config["source_hash"] != _digest(payload):
            raise ValueError("replan source changed")
        from openprogram.execution.model import _thaw_json

        payload = _thaw_json(payload)
        values = (
            payload["turn_request"]
            if payload["kind"] == "job_agent"
            else payload["request"]
        )
        authority = normalize_authority(values)
        authority.update(speaker_kind="agent", interaction="background")
        if (
            authority.get("authority_tier") == "owner"
            and authority.get("principal_id") != owner_principal_id()
        ):
            raise ValueError("replan owner changed")
        permission = dict(
            mode=values.get("permission_mode", "ask"),
            rules=values.get("permission_rules"),
        )
        if (
            config["authority"] != authority
            or normalize_authority(job) != authority
            or config["permission"] != permission
            or config["prompt"] != job.prompt
            or job.parent_msg_id != config["parent_msg_id"]
        ):
            raise ValueError("replan inputs changed")
        return permission
