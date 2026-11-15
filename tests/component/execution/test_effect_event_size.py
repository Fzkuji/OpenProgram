"""A provider effect's message context is stored once, on its effect row.

Every model request plans, dispatches and commits one effect. Its
lifecycle events used to carry the whole context each time, which grew
executions.db by several copies of every conversation.
"""
from __future__ import annotations

import json

from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution._schema import SCHEMA_VERSION
from openprogram.runtime.execution.store import ExecutionStore


def test_effect_events_leave_the_context_on_the_effect_row(tmp_path):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "fixture"})
    execution = executions.create_execution(
        execution_id="exec", run_id="run", session_id="session",
        revision_id=revision.revision_id, capabilities=CapabilitySet(),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        "exec", expected_version=execution.status_version,
        owner_id="worker", attempt_id="attempt",
    )
    attempts.activate(
        "attempt", generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    effects = EffectStore(executions)
    context = {"messages": [{"role": "user", "content": "x" * 50_000}]}
    effects.register(
        effect_id="eff", execution_id="exec", attempt_id="attempt",
        action_id="provider.before", classification=EffectClassification.UNKNOWN,
        idempotency_key=None,
        metadata={"kind": "provider", "payload": {"context": context}},
    )
    effects.mark_dispatched("eff", expected_status=EffectStatus.PLANNED)
    effects.resolve(
        "eff", expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED, receipt={"ok": True},
    )

    assert effects.get("eff").metadata["payload"]["context"] == context
    rows = executions._connect().rows(
        "execution_events", execution_id="exec", order="sequence",
        where=lambda row: row["kind"].startswith("effect."),
    )
    assert [row["kind"] for row in rows] == [
        "effect.planned", "effect.dispatched", "effect.committed",
    ]
    for row in rows:
        metadata = json.loads(row["payload_json"])["effect"]["metadata"]
        assert metadata == {"kind": "provider"}
        assert len(row["payload_json"]) < 5_000

