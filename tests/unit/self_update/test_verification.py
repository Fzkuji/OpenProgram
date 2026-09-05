from __future__ import annotations
from pathlib import Path

from copy import deepcopy

import pytest

from openprogram.self_update import UpdateRequest
from openprogram.self_update.verification import (
    VerificationValidationError,
    validate_verifier_result,
)


def _request() -> UpdateRequest:
    return UpdateRequest(
        update_id="su_verify", session_id="session", origin_turn_id="turn",
        origin_assistant_id="turn_reply", agent_id="main", repo=str(Path("/tmp/OpenProgram").resolve()),
        worktree_id="wt_candidate", base_sha="1" * 40, candidate_sha="2" * 40,
        changed_paths=("openprogram/feature.py",), pre_update_evidence=("tests:pass",),
        goal="Add behavior", assertions=("API returns the result", "UI shows the result"),
    )


def _payload() -> dict:
    return {
        "schema": 1, "update_id": "su_verify", "candidate_sha": "2" * 40,
        "attempt": 1, "verdict": "pass",
        "assertions": [
            {
                "id": f"acceptance-{index}", "status": "pass", "entry": "default App",
                "observation": "Expected result observed", "evidence_refs": [f"session-node:{index}"],
                "observed_at": 101.0,
            }
            for index in (1, 2)
        ],
    }


def _validate(payload):
    return validate_verifier_result(
        payload, _request(), attempt=1, not_before=100.0, now=102.0,
    )


def test_complete_identity_bound_result_passes_and_is_immutable() -> None:
    payload = _payload()
    result = _validate(payload)
    payload["assertions"][0]["evidence_refs"].append("mutated")
    assert result.verdict == "pass"
    assert result.assertions[0].evidence_refs == ("session-node:1",)
    assert result.to_dict() == _payload()


@pytest.mark.parametrize("field,value", [
    ("update_id", "su_other"), ("candidate_sha", "3" * 40), ("attempt", 2),
    ("attempt", True), ("schema", 2),
])
def test_wrong_identity_or_schema_cannot_pass(field, value) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(VerificationValidationError):
        _validate(payload)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "empty_evidence", "stale", "future"])
def test_assertions_require_exact_coverage_and_fresh_evidence(change) -> None:
    payload = _payload()
    if change == "missing":
        payload["assertions"].pop()
    elif change == "duplicate":
        payload["assertions"][1] = deepcopy(payload["assertions"][0])
    elif change == "unknown":
        payload["assertions"][1]["id"] = "acceptance-3"
    elif change == "empty_evidence":
        payload["assertions"][0]["evidence_refs"] = []
    else:
        payload["assertions"][0]["observed_at"] = 99 if change == "stale" else 103
    with pytest.raises(VerificationValidationError):
        _validate(payload)


@pytest.mark.parametrize("status", ["fail", "inconclusive"])
def test_failed_assertion_cannot_be_promoted_to_pass(status) -> None:
    payload = _payload()
    payload["assertions"][0]["status"] = status
    with pytest.raises(VerificationValidationError, match="verdict"):
        _validate(payload)
    payload["verdict"] = status
    assert _validate(payload).verdict == status
