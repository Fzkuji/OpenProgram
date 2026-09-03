"""Validate identity-bound, timestamped self-update acceptance evidence."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .types import UpdateRequest, _required_text


class VerificationValidationError(ValueError):
    """A result cannot be used as proof of successful acceptance."""


@dataclass(frozen=True)
class AssertionResult:
    id: str
    status: str
    entry: str
    observation: str
    evidence_refs: tuple[str, ...]
    observed_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "entry": self.entry,
            "observation": self.observation, "evidence_refs": list(self.evidence_refs),
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class VerifierResult:
    update_id: str
    candidate_sha: str
    attempt: int
    verdict: str
    assertions: tuple[AssertionResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1, "update_id": self.update_id,
            "candidate_sha": self.candidate_sha, "attempt": self.attempt,
            "verdict": self.verdict,
            "assertions": [result.to_dict() for result in self.assertions],
        }


def _timestamp(value: Any, name: str) -> float:
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < 0
    ):
        raise VerificationValidationError(f"{name} must be a finite timestamp")
    return float(value)


def validate_verifier_result(
    payload: Mapping[str, Any],
    request: UpdateRequest,
    *,
    attempt: int,
    not_before: float,
    now: float,
) -> VerifierResult:
    """Validate structure and identity, not the truth of referenced evidence.

    The external controller resolves evidence references separately. A validation
    error is an inconclusive result, never permission to commit the installation.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 3:
        raise VerificationValidationError("attempt must be between 1 and 3")
    start = _timestamp(not_before, "not_before")
    end = _timestamp(now, "now")
    if end < start:
        raise VerificationValidationError("verification time window is reversed")
    expected_fields = {"schema", "update_id", "candidate_sha", "attempt", "verdict", "assertions"}
    if not isinstance(payload, Mapping) or set(payload) != expected_fields:
        raise VerificationValidationError("malformed verifier result")
    if (
        type(payload["schema"]) is not int or payload["schema"] != 1
        or payload["update_id"] != request.update_id
        or payload["candidate_sha"] != request.candidate_sha
        or type(payload["attempt"]) is not int or payload["attempt"] != attempt
    ):
        raise VerificationValidationError("verifier result identity does not match")
    rows = payload["assertions"]
    if not isinstance(rows, list) or len(rows) != len(request.assertions):
        raise VerificationValidationError("assertions do not cover the acceptance contract")
    expected_ids = {f"acceptance-{index}" for index in range(1, len(request.assertions) + 1)}
    seen: set[str] = set()
    results: list[AssertionResult] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "id", "status", "entry", "observation", "evidence_refs", "observed_at",
        }:
            raise VerificationValidationError("malformed assertion evidence")
        identifier = row["id"]
        if not isinstance(identifier, str) or identifier not in expected_ids or identifier in seen:
            raise VerificationValidationError("unknown or duplicate assertion id")
        seen.add(identifier)
        status = row["status"]
        if status not in ("pass", "fail", "inconclusive"):
            raise VerificationValidationError("invalid assertion status")
        refs = row["evidence_refs"]
        if not isinstance(refs, list) or not refs:
            raise VerificationValidationError("assertion requires evidence references")
        try:
            entry = _required_text(row["entry"], "entry", maximum=512)
            observation = _required_text(row["observation"], "observation", maximum=8192)
            evidence_refs = tuple(_required_text(ref, "evidence reference") for ref in refs)
        except ValueError as exc:
            raise VerificationValidationError(str(exc)) from exc
        observed_at = _timestamp(row["observed_at"], "observed_at")
        if not start <= observed_at <= end:
            raise VerificationValidationError("assertion evidence is outside this verification window")
        results.append(AssertionResult(identifier, status, entry, observation, evidence_refs, observed_at))
    statuses = {result.status for result in results}
    verdict = "fail" if "fail" in statuses else "inconclusive" if "inconclusive" in statuses else "pass"
    if payload["verdict"] != verdict:
        raise VerificationValidationError("overall verdict does not match assertion outcomes")
    return VerifierResult(request.update_id, request.candidate_sha, attempt, verdict, tuple(results))


__all__ = ["AssertionResult", "VerifierResult", "VerificationValidationError", "validate_verifier_result"]
