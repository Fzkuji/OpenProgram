"""The trial's last check: the independent verifier, in the live worker.

It is the same frozen Job as after a switch, read-only and with a signed
receipt, except that it observes the trial's port and has 15 minutes.
"""
from __future__ import annotations

import time
from typing import Mapping

from ..store import SelfUpdateStore
from ..types import UpdatePhase, UpdateRecord
from ..verification.system_probe import ProbeTarget, probe_instance, request_dispatch
from .instance import TrialError, TrialInstance

LIMIT_SECONDS = 15 * 60
_POLL_SECONDS = 2.0


def run_verifier(store: SelfUpdateStore, record: UpdateRecord, instance: TrialInstance, *, live: ProbeTarget,
                 host_pid: int, doctor_baseline: Mapping[str, bool] | None = None, limit: float = LIMIT_SECONDS) -> dict:
    """Admit the Job, wait for its receipt, and return it when it passed."""
    from ..verification.verification_channel import _digest, consume_result, issue_grant

    update_id = record.request.update_id
    target = instance.target(doctor_baseline)
    gate = probe_instance(record, target, revision=record.request.candidate_sha, timeout=60, require_loaded=True)
    grant = issue_grant(store, update_id, gate)
    store.record_detail(update_id, UpdatePhase.TRIAL, detail={
        "system_gate": gate, "verifier_grant_sha256": _digest(grant),
        "trial": {"port": target.port, "state_dir": str(target.state_dir), "worker_pid": gate["worker_pid"],
                  "host_pid": host_pid, "version_dir": str(instance.layout.version(instance.version))},
    })
    current = store.load(update_id)
    request_dispatch(current, target=live)
    deadline = time.monotonic() + min(limit, max(0.0, grant["deadline"] - time.time()))
    while True:
        receipt = consume_result(store, update_id, grant["token"])
        if receipt is not None:
            if receipt.get("verdict") != "pass":
                raise TrialError(f"the verifier did not pass the update: {receipt.get('reason')}")
            return receipt
        if time.monotonic() >= deadline:
            raise TrialError("the verifier did not finish within its limit")
        time.sleep(_POLL_SECONDS)


__all__ = ["LIMIT_SECONDS", "run_verifier"]
