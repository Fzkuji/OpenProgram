"""Authoritative Agent results survive checkpoint serialization and delivery."""
from __future__ import annotations

import hashlib
import json
import time

from .effects import EffectStatus
from .safe_points import AgentSafePointConflict
from .model import _json


def record_result(service, *, attempt, effect_id, terminal_receipt, checkpoint_inputs,
                  restart_window, command_id=None, consumed_steer_command_ids=()):
    """Commit a complete result and reconstruction input under the live owner."""
    from openprogram.agent.continuation import canonical_json_bytes
    envelope = dict(version=1, effect_id=effect_id, terminal_receipt=dict(terminal_receipt),
                    checkpoint_inputs=checkpoint_inputs, restart_window=restart_window,
                    command_id=command_id, consumed_steer_command_ids=list(consumed_steer_command_ids))
    store = service.executions
    with store._transaction() as connection:
        execution = store._require_execution(connection, attempt.execution_id)
        owner = service.attempts._require(connection, attempt.attempt_id)
        effect = service.effects._require(connection, effect_id)
        if (execution.current_attempt_id != attempt.attempt_id
                or execution.owner_lease.get("generation") != attempt.generation
                or owner.generation != attempt.generation or owner.status.value != "active"
                or owner.lease_expires_at <= time.time()
                or effect.execution_id != execution.execution_id or effect.attempt_id != attempt.attempt_id):
            raise AgentSafePointConflict("stale_attempt", "result receipt owner is stale")
        if effect.status in {EffectStatus.COMMITTED, EffectStatus.NOT_COMMITTED}:
            prior = load_result(store, connection, effect)
            envelope["base_checkpoint_id"] = prior.get("base_checkpoint_id")
            descriptor = effect.receipt.get("agent_result_ref") or {}
            if descriptor.get("sha256") == hashlib.sha256(canonical_json_bytes(envelope)).hexdigest():
                return effect
            raise AgentSafePointConflict("receipt_conflict", "result receipt already records different content")
        not_started = effect.status is EffectStatus.PLANNED and terminal_receipt.get("execution_started") is False
        if effect.status is not EffectStatus.DISPATCHED and not not_started:
            raise AgentSafePointConflict("effect_state_invalid", "result receipt has no matching dispatch")
        envelope["base_checkpoint_id"] = execution.checkpoint_head_id
        payload = canonical_json_bytes(envelope)
        descriptor = store._put_state_blob_in_transaction(connection,
            execution_id=execution.execution_id, payload=payload, media_type="application/json", schema_version=1)
        receipt = {**terminal_receipt, "agent_result_ref": descriptor, "checkpoint_pending": True}
        now = time.time()
        connection.execute("UPDATE effects SET status = ?, receipt_json = ?, updated_at = ?, resolved_at = ? WHERE effect_id = ?",
                           ("not_committed" if not_started else "committed", _json(receipt), now, now, effect_id))
        result = service.effects._require(connection, effect_id)
        service.effects._append_event(connection, execution.status_version, result, now)
        return result


def load_result(store, connection, effect):
    try:
        descriptor = effect.receipt.get("agent_result_ref")
        if not isinstance(descriptor, dict) or not descriptor.get("ref"):
            raise AgentSafePointConflict("receipt_invalid", "Agent result reference is missing")
        row = connection.execute(
            "SELECT payload, sha256, byte_length FROM execution_state_blobs "
            "WHERE execution_id = ? AND ref = ?",
            (effect.execution_id, descriptor.get("ref")),
        ).fetchone()
        if row is None:
            raise AgentSafePointConflict("receipt_invalid", "Agent result blob is missing")
        payload = bytes(row["payload"])
        digest = hashlib.sha256(payload).hexdigest()
        if digest != descriptor.get("sha256") or digest != row["sha256"] or len(payload) != row["byte_length"]:
            raise AgentSafePointConflict("receipt_invalid", "Agent result blob failed integrity validation")
        try:
            envelope = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AgentSafePointConflict("receipt_invalid", "Agent result blob is not JSON") from exc
        if not isinstance(envelope, dict):
            raise AgentSafePointConflict("receipt_invalid", "Agent result envelope is not an object")
        if envelope.get("version") != 1 or envelope.get("effect_id") != effect.effect_id:
            raise AgentSafePointConflict("receipt_invalid", "Agent result identity is invalid")
        if not isinstance(envelope.get("terminal_receipt"), dict):
            raise AgentSafePointConflict("receipt_invalid", "Agent result receipt is malformed")
        if not isinstance(envelope.get("checkpoint_inputs"), dict):
            raise AgentSafePointConflict("receipt_invalid", "Agent result checkpoint input is malformed")
        envelope.setdefault("consumed_steer_command_ids", [])
        if not isinstance(envelope.get("consumed_steer_command_ids"), list):
            raise AgentSafePointConflict("receipt_invalid", "Agent result steering identity is malformed")
        return envelope
    except AgentSafePointConflict:
        raise
    except Exception as exc:
        raise AgentSafePointConflict("receipt_invalid", "Agent result could not be loaded") from exc


def validate_checkpoint(store, connection, effect, checkpoint, terminal_receipt):
    """An immutable receipt authorizes only its exact reconstructed checkpoint."""
    from openprogram.agent.continuation import AgentCheckpointV1
    envelope = load_result(store, connection, effect)
    expected = AgentCheckpointV1.build(**envelope["checkpoint_inputs"])
    execution = store._require_execution(connection, effect.execution_id)
    if envelope.get("base_checkpoint_id") != execution.checkpoint_head_id:
        raise AgentSafePointConflict("receipt_conflict", "recorded result has a different checkpoint predecessor")
    if (checkpoint is None or expected.to_bytes() != checkpoint.to_bytes()
            or envelope["terminal_receipt"] != dict(terminal_receipt)):
        raise AgentSafePointConflict("receipt_conflict", "checkpoint differs from recorded result")
    return envelope


def pending_result(service, execution_id, *, connection=None):
    query = ("SELECT * FROM effects WHERE execution_id = ? AND status IN ('committed', 'not_committed') "
             "AND json_extract(receipt_json, '$.checkpoint_pending') = 1 ORDER BY resolved_at DESC, effect_id LIMIT 1")
    if connection is None:
        from contextlib import closing
        with closing(service.executions._connect()) as conn:
            row = conn.execute(query, (execution_id,)).fetchone()
    else:
        row = connection.execute(query, (execution_id,)).fetchone()
    return service.effects._record(row) if row else None


def recover_checkpoint(service, connection, execution):
    """Deliver the current owner's recorded result without invoking its tool."""
    from openprogram.agent.continuation import AgentCheckpointV1, canonical_json_bytes
    if connection.execute("SELECT 1 FROM effects WHERE execution_id = ? AND status IN ('dispatched', 'uncertain') LIMIT 1",
                          (execution.execution_id,)).fetchone():
        return execution
    effect = pending_result(service, execution.execution_id, connection=connection)
    if effect is None:
        return execution
    if effect.attempt_id != execution.current_attempt_id:
        raise AgentSafePointConflict("stale_attempt", "pending result belongs to another owner")
    connection.execute("SAVEPOINT agent_receipt_delivery")
    try:
        envelope = load_result(service.executions, connection, effect)
        checkpoint = AgentCheckpointV1.build(**envelope["checkpoint_inputs"])
        owner = service.attempts._require(connection, execution.current_attempt_id)
        completion = service._commit_agent_safe_point_in_transaction(connection,
            execution_id=execution.execution_id, attempt_id=owner.attempt_id, generation=owner.generation,
            expected_version=execution.status_version, safe_point_kind=checkpoint.payload["safe_point"]["kind"],
            frontier=tuple(checkpoint.payload["frontier"]),
            state_refs={"restart_window_seconds": envelope["restart_window"]},
            effect_id=effect.effect_id, terminal_receipt=envelope["terminal_receipt"],
            receipt_blob=canonical_json_bytes(envelope["terminal_receipt"]), agent_checkpoint=checkpoint,
            command_id=None, managed_action_id=effect.action_id,
            consumed_steer_command_ids=tuple(envelope["consumed_steer_command_ids"]),
            recovery_effect_id=effect.effect_id)
        connection.execute("RELEASE agent_receipt_delivery")
        return completion.execution
    except Exception:
        connection.execute("ROLLBACK TO agent_receipt_delivery")
        connection.execute("RELEASE agent_receipt_delivery")
        raise


def validates_expired_publication(store, connection, effect_id, *, execution, state_refs, frontier):
    """Validate the narrow expired-owner exception for an already recorded result."""
    from .effects import EffectStore
    from openprogram.agent.continuation import AgentCheckpointV1
    effect = EffectStore(store)._require(connection, effect_id)
    if (effect.execution_id != execution.execution_id or effect.attempt_id != execution.current_attempt_id
            or effect.status not in {EffectStatus.COMMITTED, EffectStatus.NOT_COMMITTED} or not effect.receipt.get("agent_result_ref")):
        return False
    envelope = load_result(store, connection, effect)
    expected = AgentCheckpointV1.build(**envelope["checkpoint_inputs"])
    descriptor = state_refs.get("agent_checkpoint") or {}
    return (descriptor.get("sha256") == hashlib.sha256(expected.to_bytes()).hexdigest()
            and list(frontier) == expected.payload["frontier"]
            and state_refs.get("restart_window_seconds") == envelope["restart_window"]
            and execution.checkpoint_head_id == envelope.get("base_checkpoint_id"))
