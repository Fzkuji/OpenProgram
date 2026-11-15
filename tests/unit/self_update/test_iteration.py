from __future__ import annotations
from pathlib import Path

from dataclasses import replace

import pytest

from openprogram.operations.self_update import IterationMode, IterationPolicy, UpdateRequest
from openprogram.operations.self_update.control.iteration import TestEvidence
from openprogram.operations.self_update.control.iteration import evaluate_iteration


def _request() -> UpdateRequest:
    return UpdateRequest(
        update_id="su_iterate", session_id="session", origin_turn_id="turn",
        origin_assistant_id="turn_reply", agent_id="main", repo=str(Path("/tmp/OpenProgram").resolve()),
        worktree_id="wt_candidate", base_sha="1" * 40, candidate_sha="2" * 40,
        changed_paths=("openprogram/feature.py",), pre_update_evidence=("tests:pass",),
        goal="Add behavior", assertions=("API returns the result",),
        iteration_policy=IterationPolicy(
            mode=IterationMode.BOUNDED_AUTO, max_attempts=3, deadline=200.0,
            allowed_paths=("openprogram/*", "tests/*"), required_tests=("pytest tests/feature",),
        ),
    )


def _evaluate(request=None, **overrides):
    values = dict(
        attempt=1, candidate_sha="3" * 40, changed_paths=("openprogram/feature.py",),
        test_evidence=(TestEvidence("pytest tests/feature", "3" * 40, 0),),
        failure_kind="implementation", failure_fingerprints=("failure-a",),
        rollback_succeeded=True, now=150.0,
    )
    values.update(overrides)
    return evaluate_iteration(request or _request(), **values)


def test_bounded_candidate_with_passing_bound_tests_can_continue() -> None:
    decision = _evaluate()
    assert decision.allowed is True
    assert decision.next_attempt == 2


def test_default_policy_requires_new_approval() -> None:
    request = replace(_request(), iteration_policy=IterationPolicy())
    assert _evaluate(request).reason == "approval-required"


@pytest.mark.parametrize("overrides,reason", [
    ({"attempt": 3}, "attempt-budget-exhausted"),
    ({"now": 201.0}, "deadline-exhausted"),
    ({"candidate_sha": "2" * 40}, "candidate-not-new"),
    ({"changed_paths": ("apps/web/page.tsx",)}, "scope-expanded"),
    ({"test_evidence": ()}, "required-tests-missing"),
    ({"test_evidence": (TestEvidence("pytest tests/feature", "3" * 40, 1),)}, "required-tests-failed"),
    ({"test_evidence": (TestEvidence("pytest tests/feature", "2" * 40, 0),)}, "test-candidate-mismatch"),
    ({"failure_kind": "environment"}, "environment-failure"),
    ({"failure_fingerprints": ("same", "same")}, "repeated-failure"),
    ({"rollback_succeeded": False}, "rollback-not-verified"),
])
def test_stopping_conditions(overrides, reason) -> None:
    decision = _evaluate(**overrides)
    assert decision.allowed is False
    assert decision.next_attempt is None
    assert decision.reason == reason


@pytest.mark.parametrize("path", [
    "pyproject.toml", "apps/web/package.json", "uv.lock",
    "setup.py",
    "openprogram/runtime/permissions/approval.py",
    "openprogram/runtime/permissions/state.py", "openprogram/runtime/permissions/classifier.py", "openprogram/runtime/permissions/policy.py",
    "openprogram/runtime/permissions/lifecycle.py", "openprogram/runtime/permissions/__init__.py", "openprogram/runtime/permissions/authority.py",
    "openprogram/runtime/conversation/turn_runtime/__init__.py", "openprogram/runtime/conversation/turn_runtime/loop_runner.py",
    "openprogram/runtime/agent/loop/loop.py", "openprogram/runtime/conversation/model_tools.py",
    "openprogram/programs/__init__.py",
    "openprogram/programs/_host/runtime.py",
    "openprogram/operations/self_update/control/supervisor.py", "openprogram/host/sandbox/__init__.py",
    "apps/desktop/scripts/install-app.sh",
])
def test_no_path_needs_approval_of_its_own(path) -> None:
    request = _request()
    request = replace(request, iteration_policy=replace(request.iteration_policy, allowed_paths=("*",)))
    assert _evaluate(request, changed_paths=(path,)).reason == "within-approved-envelope"


def test_bounded_policy_without_deadline_cannot_auto_activate() -> None:
    request = _request()
    request = replace(request, iteration_policy=replace(request.iteration_policy, deadline=None))
    assert _evaluate(request).reason == "incomplete-envelope"
