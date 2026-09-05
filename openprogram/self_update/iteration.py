"""Deterministic authorization checks for a proposed self-update iteration."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import math
from pathlib import PurePosixPath
from typing import ClassVar

from .types import IterationMode, UpdateRequest, _GIT_SHA, _required_text, _validate_changed_path


_PROTECTED_PATHS = (
    "openprogram/self_update/*", "openprogram/programs/tools/system/self_update/*",
    "openprogram/agent/internals/_approval*", "openprogram/agent/permissions/*", "openprogram/agent/authority.py",
    "openprogram/agent/dispatcher/*", "openprogram/agent/agent_loop.py",
    "openprogram/agent/internals/_model_tools.py", "openprogram/programs/__init__.py",
    "openprogram/agent/sub_agent_run.py", "openprogram/agent/job/*",
    "openprogram/sandbox/*", "openprogram/security/*", "openprogram/auth/*",
    "openprogram/protected_paths.py", "openprogram/programs/_runtime/*",
    "openprogram/programs/_runtime.py",
    "apps/desktop/scripts/*", "scripts/release/*", "scripts/refresh-local-app.sh",
    "setup.py",
)
_DEPENDENCY_FILES = (
    "pyproject.toml", "uv.lock", "package.json", "package-lock.json",
    "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "requirements*.txt",
    "poetry.lock", "Pipfile*", ".npmrc", ".pypirc",
)


@dataclass(frozen=True)
class TestEvidence:
    __test__: ClassVar[bool] = False
    command: str
    candidate_sha: str
    exit_code: int

    def __post_init__(self) -> None:
        _required_text(self.command, "test command")
        if not isinstance(self.candidate_sha, str) or not _GIT_SHA.fullmatch(self.candidate_sha):
            raise ValueError("test evidence requires a full candidate SHA")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("test exit_code must be an integer")


@dataclass(frozen=True)
class IterationDecision:
    allowed: bool
    reason: str
    next_attempt: int | None = None


def evaluate_iteration(
    request: UpdateRequest,
    *,
    attempt: int,
    candidate_sha: str,
    changed_paths: tuple[str, ...],
    test_evidence: tuple[TestEvidence, ...],
    failure_kind: str,
    failure_fingerprints: tuple[str, ...],
    rollback_succeeded: bool,
    now: float,
) -> IterationDecision:
    """Check a controller-produced proposal without performing any side effect."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 3:
        raise ValueError("attempt must be between 1 and 3")
    if not isinstance(candidate_sha, str) or not _GIT_SHA.fullmatch(candidate_sha):
        raise ValueError("candidate_sha must be a full lowercase SHA")
    if not isinstance(changed_paths, tuple) or not changed_paths:
        raise ValueError("changed_paths must be a non-empty tuple")
    for path in changed_paths:
        _validate_changed_path(path)
    if not isinstance(test_evidence, tuple) or any(not isinstance(item, TestEvidence) for item in test_evidence):
        raise ValueError("test_evidence must contain TestEvidence records")
    if not isinstance(failure_fingerprints, tuple) or not failure_fingerprints:
        raise ValueError("failure_fingerprints must be a non-empty tuple")
    for fingerprint in failure_fingerprints:
        _required_text(fingerprint, "failure fingerprint")
    if failure_kind not in ("implementation", "test", "environment", "goal"):
        raise ValueError("unknown failure kind")
    if type(rollback_succeeded) is not bool:
        raise ValueError("rollback_succeeded must be a boolean")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise ValueError("now must be a finite timestamp")

    def stop(reason: str) -> IterationDecision:
        return IterationDecision(False, reason)

    policy = request.iteration_policy
    if not rollback_succeeded:
        return stop("rollback-not-verified")
    if policy.mode is IterationMode.APPROVE_EACH_ACTIVATION:
        return stop("approval-required")
    if policy.deadline is None or not policy.allowed_paths or not policy.required_tests:
        return stop("incomplete-envelope")
    if attempt >= policy.max_attempts:
        return stop("attempt-budget-exhausted")
    if now >= policy.deadline:
        return stop("deadline-exhausted")
    if failure_kind == "environment":
        return stop("environment-failure")
    if failure_kind == "goal":
        return stop("goal-clarification-required")
    if len(failure_fingerprints) >= 2 and failure_fingerprints[-1] == failure_fingerprints[-2]:
        return stop("repeated-failure")
    if candidate_sha in (request.base_sha, request.candidate_sha):
        return stop("candidate-not-new")
    for path in changed_paths:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in _PROTECTED_PATHS) or any(
            fnmatch.fnmatchcase(PurePosixPath(path).name, pattern) for pattern in _DEPENDENCY_FILES
        ):
            return stop("protected-change")
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in policy.allowed_paths):
            return stop("scope-expanded")
    evidence = {item.command: item for item in test_evidence}
    if len(evidence) != len(test_evidence):
        return stop("ambiguous-test-evidence")
    for command in policy.required_tests:
        item = evidence.get(command)
        if item is None:
            return stop("required-tests-missing")
        if item.candidate_sha != candidate_sha:
            return stop("test-candidate-mismatch")
        if item.exit_code != 0:
            return stop("required-tests-failed")
    return IterationDecision(True, "within-approved-envelope", attempt + 1)


__all__ = ["TestEvidence", "IterationDecision", "evaluate_iteration"]
