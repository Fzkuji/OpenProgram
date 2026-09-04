"""Owner-only conversational self-update request tools.

These tools only validate and persist an immutable candidate request. App
building, activation, restart, verification, and rollback are owned by the
external supervisor rather than this worker process.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import time
import tomllib
from typing import Any, Mapping

from openprogram.agent.authority import normalize_authority, owner_principal_id
from openprogram.agent.turn_request_context import get_turn_request
from openprogram.programs._runtime import function
from openprogram.self_update import (
    ActiveUpdateError,
    IterationMode,
    IterationPolicy,
    SelfUpdateError,
    SelfUpdateStore,
    UpdatePhase,
    UpdateRequest,
    mint_update_id,
)
from openprogram.worktree.manager import get_manager
from openprogram.worktree.types import WorktreeStatus


_NON_INTERACTIVE_SOURCES = frozenset({"agent_spawn", "cron", "scheduler", "mcp"})
_GIT_TIMEOUT_SECONDS = 10
_GIT_CONFIG_OVERRIDES = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
)


class SelfUpdateToolError(RuntimeError):
    """A stable validation error safe to return through the tool runtime."""


def _launch_supervisor(update_id: str) -> None:
    from openprogram.self_update.launcher import launch_supervisor

    launch_supervisor(update_id)


def _require_local_owner(req: Any) -> dict[str, str]:
    authority = normalize_authority(req)
    try:
        installed_owner = owner_principal_id()
    except Exception as exc:
        raise SelfUpdateToolError("interactive local owner identity is unavailable") from exc
    if (
        not authority
        or authority.get("principal_id") != installed_owner
        or authority.get("authority_tier") != "owner"
        or authority.get("speaker_kind") != "owner"
        or authority.get("interaction") != "interactive"
        or getattr(req, "source", None) in _NON_INTERACTIVE_SOURCES
    ):
        raise SelfUpdateToolError("self-update requires an interactive local owner")
    return authority


def _git(cwd: Path, *args: str) -> str:
    environment = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
        ),
        "HOME": os.environ.get("HOME", "/var/empty"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    command = ["git", "--no-optional-locks"]
    for value in _GIT_CONFIG_OVERRIDES:
        command.extend(("-c", value))
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise SelfUpdateToolError(f"git validation failed: {type(exc).__name__}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()[:500]
        raise SelfUpdateToolError(f"git validation failed: {message or result.returncode}")
    return result.stdout.rstrip("\n")


def _iteration_policy(value: Mapping[str, Any] | None) -> IterationPolicy:
    if value is None:
        return IterationPolicy()
    if not isinstance(value, Mapping):
        raise SelfUpdateToolError("iteration_policy must be an object")
    allowed = {"mode", "max_attempts", "deadline", "allowed_paths", "required_tests"}
    unknown = set(value) - allowed
    if unknown:
        raise SelfUpdateToolError(
            "iteration_policy contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    for field in ("allowed_paths", "required_tests"):
        raw = value.get(field)
        if raw is not None and not isinstance(raw, list):
            raise SelfUpdateToolError(f"iteration_policy {field} must be an array")
    try:
        policy = IterationPolicy(
            mode=IterationMode(
                value.get("mode", IterationMode.APPROVE_EACH_ACTIVATION.value)
            ),
            max_attempts=value.get("max_attempts", 3),
            deadline=value.get("deadline"),
            allowed_paths=tuple(value.get("allowed_paths") or ()),
            required_tests=tuple(value.get("required_tests") or ()),
        )
    except (TypeError, ValueError) as exc:
        raise SelfUpdateToolError(f"invalid iteration_policy: {exc}") from exc
    if policy.deadline is not None and policy.deadline <= time.time():
        raise SelfUpdateToolError("iteration_policy deadline must be in the future")
    return policy


def _validate_openprogram_repo(source: Path, worktree: Path) -> None:
    if source == worktree:
        raise SelfUpdateToolError("candidate must be an isolated linked worktree")
    try:
        metadata = tomllib.loads((worktree / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SelfUpdateToolError("candidate does not contain readable project metadata") from exc
    project = metadata.get("project")
    if not isinstance(project, dict) or project.get("name") != "openprogram":
        raise SelfUpdateToolError("candidate is not an OpenProgram source checkout")


def _git_directory(cwd: Path) -> Path:
    value = Path(_git(cwd, "rev-parse", "--absolute-git-dir"))
    return value.resolve()


def _recorded_path(value: str, name: str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise SelfUpdateToolError(f"{name} must be an absolute path")
    lexical = Path(os.path.normpath(str(raw)))
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise SelfUpdateToolError(f"{name} is unavailable") from exc
    if resolved != lexical:
        raise SelfUpdateToolError(f"{name} must not contain symlinks")
    return resolved


def _validate_registered_worktree(
    source: Path, candidate: Path, candidate_sha: str, branch_name: str
) -> None:
    entries = _git(source, "worktree", "list", "--porcelain", "-z").split("\0\0")
    expected = {
        "worktree": str(candidate),
        "HEAD": candidate_sha,
        "branch": f"refs/heads/{branch_name}",
    }
    for entry in entries:
        fields = {}
        for line in entry.split("\0"):
            key, separator, value = line.partition(" ")
            if separator:
                fields[key] = value
        if fields.get("worktree") == str(candidate):
            if all(fields.get(key) == value for key, value in expected.items()):
                return
            break
    raise SelfUpdateToolError("candidate does not match its registered Git worktree")


def _validate_candidate_snapshot(candidate: Path, candidate_sha: str) -> None:
    if _git(candidate, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SelfUpdateToolError("candidate worktree is dirty")
    if _git(candidate, "rev-parse", "HEAD") != candidate_sha:
        raise SelfUpdateToolError("candidate_sha is not the worktree HEAD")


def _prepare_update(
    *,
    worktree_id: str,
    candidate_sha: str,
    goal: str,
    assertions: list[str],
    iteration_policy: Mapping[str, Any] | None,
    req: Any,
    assistant_id: str,
    manager: Any,
    store: SelfUpdateStore,
    verification_plan: dict | None = None,
) -> dict[str, Any]:
    _require_local_owner(req)
    if not isinstance(assistant_id, str) or not assistant_id.strip():
        raise SelfUpdateToolError("self-update must run inside a persisted chat turn")
    if not isinstance(worktree_id, str) or not worktree_id.strip():
        raise SelfUpdateToolError("worktree_id is required")
    if (
        not isinstance(candidate_sha, str)
        or len(candidate_sha) != 40
        or candidate_sha.lower() != candidate_sha
        or any(ch not in "0123456789abcdef" for ch in candidate_sha)
    ):
        raise SelfUpdateToolError("candidate_sha must be a full lowercase Git SHA")
    if not isinstance(goal, str) or not goal.strip():
        raise SelfUpdateToolError("goal is required")
    if (
        not isinstance(assertions, list)
        or not assertions
        or any(not isinstance(value, str) or not value.strip() for value in assertions)
    ):
        raise SelfUpdateToolError("assertions must be a non-empty list of strings")

    worktree = manager.get_worktree(worktree_id)
    if worktree is None:
        raise SelfUpdateToolError(f"worktree does not exist: {worktree_id}")
    if worktree.status is not WorktreeStatus.ACTIVE:
        raise SelfUpdateToolError("candidate worktree must be active")
    if worktree.parent_session != req.session_id:
        raise SelfUpdateToolError("candidate worktree is not owned by this session")

    source = _recorded_path(worktree.source_repo, "source repo")
    candidate = _recorded_path(worktree.worktree_path, "candidate worktree")
    _validate_openprogram_repo(source, candidate)
    candidate_common = Path(_git(candidate, "rev-parse", "--git-common-dir"))
    if not candidate_common.is_absolute():
        candidate_common = candidate / candidate_common
    if candidate_common.resolve() != _git_directory(source):
        raise SelfUpdateToolError("candidate worktree is not linked to its source repo")
    if Path(_git(candidate, "rev-parse", "--show-toplevel")).resolve() != candidate:
        raise SelfUpdateToolError("candidate path is not the worktree root")
    _validate_registered_worktree(
        source, candidate, candidate_sha, worktree.branch_name
    )
    _validate_candidate_snapshot(candidate, candidate_sha)

    base_sha = _git(source, "rev-parse", "HEAD")
    if base_sha == candidate_sha:
        raise SelfUpdateToolError("candidate must differ from the source checkout HEAD")
    if _git(candidate, "merge-base", base_sha, candidate_sha) != base_sha:
        raise SelfUpdateToolError("candidate is not based on the source checkout HEAD")
    changed = tuple(
        path
        for path in _git(
            candidate,
            "diff",
            "--name-only",
            "--diff-filter=ACDMRTUXB",
            "-z",
            f"{base_sha}..{candidate_sha}",
        ).split("\0")
        if path
    )
    if not changed:
        raise SelfUpdateToolError("candidate commit contains no changes")
    if any(any(ord(char) < 32 for char in path) for path in changed):
        raise SelfUpdateToolError("candidate contains unsupported control characters in paths")

    policy = _iteration_policy(iteration_policy)
    if policy.mode is IterationMode.BOUNDED_AUTO:
        outside = [
            path
            for path in changed
            if not any(fnmatch.fnmatchcase(path, pattern) for pattern in policy.allowed_paths)
        ]
        if outside:
            raise SelfUpdateToolError(
                "candidate changes paths outside iteration_policy: " + ", ".join(outside)
            )

    origin_turn_id = getattr(req, "user_msg_id", None)
    if not origin_turn_id and assistant_id.endswith("_reply"):
        origin_turn_id = assistant_id[: -len("_reply")]
    request = UpdateRequest(
        update_id=mint_update_id(),
        session_id=req.session_id,
        origin_turn_id=origin_turn_id or assistant_id,
        origin_assistant_id=assistant_id,
        agent_id=req.agent_id,
        repo=os.path.normpath(str(source)),
        worktree_id=worktree.id,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        changed_paths=changed,
        pre_update_evidence=(
            "git-status:clean",
            f"source-head:{base_sha}",
            f"candidate-head:{candidate_sha}",
        ),
        goal=goal.strip(),
        assertions=tuple(value.strip() for value in assertions),
        iteration_policy=policy,
    )
    try:
        from openprogram.self_update.verifier_config import freeze_verifier_config, config_evidence
        from openprogram.self_update.diagnosis import freeze_config, config_evidence as diagnosis_evidence
        verifier_config = freeze_verifier_config(request, req, verification_plan=verification_plan)
        diagnosis_config = freeze_config(request, verifier_config)
        from openprogram.self_update.source_repair import freeze_config as freeze_repair, config_evidence as repair_evidence
        repair_config = freeze_repair(request, verifier_config, candidate_path=str(candidate), branch_name=worktree.branch_name)
        from openprogram.self_update.next_candidate import root_config, config_evidence as iteration_evidence
        iteration_config = root_config(request)
        if policy.mode is IterationMode.BOUNDED_AUTO and (policy.deadline is None or policy.deadline <= time.time()
                                                        or not policy.required_tests):
            raise ValueError("bounded_auto requires a future total deadline and non-empty required_tests")
        if policy.deadline is not None:
            request = replace(request, timeout_seconds=min(request.timeout_seconds, int(policy.deadline - request.created_at)))
        request = replace(request, pre_update_evidence=(*request.pre_update_evidence, config_evidence(verifier_config),
                                                       diagnosis_evidence(diagnosis_config), repair_evidence(repair_config),
                                                       iteration_evidence(iteration_config)))
        state = store.create(request, verifier_config=verifier_config, diagnosis_config=diagnosis_config,
                             source_repair_config=repair_config, iteration_config=iteration_config)
    except ActiveUpdateError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    except (SelfUpdateError, ValueError) as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    try:
        _validate_registered_worktree(
            source, candidate, candidate_sha, worktree.branch_name
        )
        _validate_candidate_snapshot(candidate, candidate_sha)
    except SelfUpdateToolError as exc:
        try:
            store.transition(
                request.update_id,
                UpdatePhase.ABORTED,
                expected_phase=UpdatePhase.PREPARING,
                detail={"reason": "candidate drifted during prepare"},
            )
        except SelfUpdateError as abort_exc:
            raise SelfUpdateToolError(
                f"{exc}; failed to abort prepared request: {abort_exc}"
            ) from abort_exc
        raise
    try:
        _launch_supervisor(request.update_id)
    except Exception as exc:
        try:
            store.transition(
                request.update_id,
                UpdatePhase.ABORTED,
                expected_phase=UpdatePhase.PREPARING,
                detail={"reason": "supervisor launch failed", "error": str(exc)[:1000]},
            )
        except SelfUpdateError as abort_exc:
            raise SelfUpdateToolError(
                f"supervisor launch failed: {exc}; request abort failed: {abort_exc}"
            ) from abort_exc
        raise SelfUpdateToolError(f"supervisor launch failed: {exc}") from exc
    return {
        "update_id": request.update_id,
        "phase": state.phase.value,
        "candidate_sha": candidate_sha,
        "base_sha": base_sha,
        "changed_paths": list(changed),
        "turn_release_pending": True,
    }


def _resolve_record(update_id: str | None, store: SelfUpdateStore):
    if update_id:
        return store.load(update_id)
    record = store.load_active()
    if record is None:
        raise SelfUpdateToolError("no active self-update")
    return record


def _status_update(
    *, update_id: str | None, req: Any, store: SelfUpdateStore
) -> dict[str, Any]:
    _require_local_owner(req)
    from openprogram.self_update.projection import ProjectionAccessError, read_status
    from openprogram.self_update.types import UpdateNotFoundError
    try:
        return read_status(store, session_id=req.session_id, update_id=update_id)
    except ProjectionAccessError:
        raise SelfUpdateToolError("self-update belongs to another origin session") from None
    except UpdateNotFoundError:
        raise SelfUpdateToolError("no matching self-update") from None
    except (SelfUpdateError, ValueError, OSError, KeyError, TypeError):
        raise SelfUpdateToolError("self-update state is unavailable or inconsistent") from None


def _cancel_update(
    *, update_id: str | None, reason: str, req: Any, store: SelfUpdateStore
) -> dict[str, Any]:
    _require_local_owner(req)
    try:
        record = _resolve_record(update_id, store)
    except SelfUpdateError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    if record.request.session_id != req.session_id:
        raise SelfUpdateToolError("self-update belongs to another origin session")
    if record.state.phase not in {
        UpdatePhase.PREPARING,
        UpdatePhase.STAGING,
        UpdatePhase.READY,
    }:
        raise SelfUpdateToolError(
            f"cannot cancel self-update in {record.state.phase.value}"
        )
    try:
        state = store.transition(
            record.request.update_id,
            UpdatePhase.ABORTED,
            expected_phase=record.state.phase,
            detail={
                **record.state.detail,
                "reason": str(reason or "owner requested cancellation").strip()[:1000],
                "cancelled_by_session": req.session_id,
            },
        )
    except SelfUpdateError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    return {"update_id": record.request.update_id, "phase": state.phase.value}


def _turn_context() -> tuple[Any, str]:
    req = get_turn_request()
    if req is None:
        raise SelfUpdateToolError("self-update tool requires an active chat turn")
    from openprogram.store import _current_turn_id

    assistant_id = _current_turn_id.get()
    if not assistant_id:
        raise SelfUpdateToolError("self-update tool requires an active assistant turn")
    return req, assistant_id


@function(
    name="self_update_prepare",
    description=(
        "Prepare an owner-approved OpenProgram self-update from the exact clean HEAD "
        "of this session's active linked worktree. This persists intent only; it does "
        "not build, install, stop, or restart the App. Approval also permits isolated "
        "source repair and listed tests after verified rollback. Default mode requires approval for each new SHA; "
        "bounded_auto explicitly permits further installations within the original attempt, deadline, path and test limits."
    ),
    toolset=["core"],
    requires_approval=True,
    path_params={},
)
def self_update_prepare(
    worktree_id: str,
    candidate_sha: str,
    goal: str,
    assertions: list[str],
    iteration_policy: dict[str, Any] | None = None,
    verification_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req, assistant_id = _turn_context()
    return _prepare_update(
        worktree_id=worktree_id,
        candidate_sha=candidate_sha,
        goal=goal,
        assertions=assertions,
        iteration_policy=iteration_policy,
        req=req,
        assistant_id=assistant_id,
        manager=get_manager(),
        store=SelfUpdateStore(),
        verification_plan=verification_plan,
    )


@function(
    name="self_update_status",
    description="Read the durable status of this session's current or named self-update.",
    toolset=["core"],
    path_params={},
)
def self_update_status(update_id: str | None = None) -> dict[str, Any]:
    req, _assistant_id = _turn_context()
    return _status_update(update_id=update_id, req=req, store=SelfUpdateStore())


@function(
    name="self_update_cancel",
    description="Cancel this session's self-update before activation begins.",
    toolset=["core"],
    requires_approval=True,
    path_params={},
)
def self_update_cancel(
    update_id: str | None = None, reason: str = "owner requested cancellation"
) -> dict[str, Any]:
    req, _assistant_id = _turn_context()
    return _cancel_update(
        update_id=update_id, reason=reason, req=req, store=SelfUpdateStore()
    )


@function(
    name="self_update_repair_cancel",
    description="Cancel source repair or candidate tests for this session's rolled-back update; never installs or changes its verdict.",
    toolset=["core"], path_params={},
)
def self_update_repair_cancel(update_id: str) -> dict[str, Any]:
    from openprogram.self_update.source_repair import _finish, read_result
    req, _assistant_id = _turn_context()
    _require_local_owner(req)
    store = SelfUpdateStore()
    with store._locked():
        record = store._load_unlocked(update_id)
        if record.request.session_id != req.session_id:
            raise SelfUpdateToolError("source repair belongs to another origin session")
        if record.state.phase is not UpdatePhase.ROLLED_BACK:
            raise SelfUpdateToolError("source repair cancellation requires a rolled-back update")
        _finish(store, record, "cancelled", "owner cancelled source repair")
        return read_result(store, record)


@function(
    name="self_update_observe",
    description=("For the active post-update verifier only: execute an approved frozen check_id "
                 "and save identity-bound evidence (local HTTP, fixed CLI, candidate script or main-window capture). "
                 "Do not supply execution arguments. Legacy requests accept entry: /api/commands, /api/diagnostics, "
                 "/api/doctor, /healthz, /chat. HTML and candidate tests are not installed UI evidence."),
    toolset=["core"],
    path_params={},
)
def self_update_observe(entry: str = "", check_id: str | None = None) -> dict[str, Any]:
    from openprogram.self_update.verification_channel import observe
    result = observe(entry, check_id=check_id)
    if result["entry"] == "ui:main":
        from openprogram.agent.types import AgentToolResult
        from openprogram.providers.types import ImageContent, TextContent
        capture = json.loads(result["body"])
        image = capture["screenshot"].pop("data")
        result["body"] = json.dumps(capture, allow_nan=False)
        return AgentToolResult(content=[TextContent(text=json.dumps(result, allow_nan=False)),
                                        ImageContent(data=image, mime_type="image/png")])
    return result


@function(
    name="self_update_retry", toolset=["core"], requires_approval=True, path_params={},
    description="Approve this exact tested repair candidate in its original owner session. Preserves original goal, scope, model and iteration budget; returns a child request, not installation success.",
)
def self_update_retry(update_id: str, candidate_sha: str) -> dict[str, Any]:
    from openprogram.self_update.next_candidate import submit
    req, assistant_id = _turn_context()
    return submit(update_id, candidate_sha, req=req, assistant_id=assistant_id)


@function(
    name="self_update_iteration_cancel", toolset=["core"], path_params={},
    description="Stop this owner session's entire self-update iteration, including diagnosis, repair, tests and pending submission. An activated transaction still completes verification or safe rollback.",
)
def self_update_iteration_cancel(update_id: str) -> dict[str, Any]:
    from openprogram.self_update.next_candidate import cancel
    req, _ = _turn_context()
    return cancel(update_id, req)


__all__ = ["self_update_prepare", "self_update_status", "self_update_cancel", "self_update_repair_cancel", "self_update_observe",
           "self_update_retry", "self_update_iteration_cancel"]
