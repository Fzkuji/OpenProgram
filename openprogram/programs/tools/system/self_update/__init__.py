"""Owner-only conversational self-update request tools.

These tools only validate and persist an immutable candidate request. App
building, activation, restart, verification, and rollback are owned by the
external supervisor rather than this worker process.
"""

from __future__ import annotations

import fnmatch
import functools
import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import time
import tomllib
from typing import Any, Mapping

from openprogram.state.authority import normalize_authority
from openprogram.runtime.permissions import refusal
from openprogram.runtime.permissions.authority import owner_principal_id
from openprogram.runtime.agent.turn_request import get_turn_request
from openprogram.programs._host.runtime import function
from openprogram.operations.self_update.types import DEFAULT_APP_PATH
from openprogram.operations.self_update import (
    ActiveUpdateError,
    IterationMode,
    IterationPolicy,
    SelfUpdateError,
    SelfUpdateStore,
    UpdatePhase,
    UpdateRequest,
    mint_update_id,
)
from openprogram.state.worktree.manager import WorktreeError, get_manager
from openprogram.state.worktree.types import WorktreeStatus


_GIT_TIMEOUT_SECONDS = 10
_GIT_CONFIG_OVERRIDES = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
)


class SelfUpdateToolError(RuntimeError):
    """A stable validation error safe to return through the tool runtime."""


def _require_backend_host(name: str) -> None:
    from openprogram.operations.self_update.control import backend

    try:
        backend.require_host(name)
    except backend.BackendUnavailable as exc:
        raise SelfUpdateToolError(str(exc)) from exc


def _launch_supervisor(name: str, store: SelfUpdateStore, update_id: str, worktree: Path) -> None:
    from openprogram.operations.self_update.control import backend

    backend.launch(name, store, update_id, worktree=worktree)


def _require_local_owner(req: Any) -> dict[str, str]:
    authority = normalize_authority(req)
    try:
        installed_owner = owner_principal_id()
    except Exception as exc:
        raise SelfUpdateToolError("owner identity is unavailable") from exc
    if (
        not authority
        or authority.get("principal_id") != installed_owner
        or authority.get("authority_tier") != "owner"
    ):
        raise refusal.self_update_owner()
    return authority


def _git(cwd: Path, *args: str) -> str:
    from openprogram.host._platform.process_ownership import no_window_creation_flags
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
            encoding="utf-8",
            creationflags=no_window_creation_flags(),
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
        "HEAD": candidate_sha,
        "branch": f"refs/heads/{branch_name}",
    }
    for entry in entries:
        fields = {}
        for line in entry.split("\0"):
            key, separator, value = line.partition(" ")
            if separator:
                fields[key] = value
        # Git emits forward slashes on Windows; compare host paths rather than
        # their serialized spelling while keeping HEAD and branch exact.
        if fields.get("worktree") and Path(fields["worktree"]) == candidate:
            if all(fields.get(key) == value for key, value in expected.items()):
                return
            break
    raise SelfUpdateToolError("candidate does not match its registered Git worktree")


def _validate_candidate_snapshot(candidate: Path, candidate_sha: str) -> None:
    if _git(candidate, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SelfUpdateToolError("candidate worktree is dirty")
    if _git(candidate, "rev-parse", "HEAD") != candidate_sha:
        raise SelfUpdateToolError("candidate_sha is not the worktree HEAD")


def _check_candidate_sha(candidate_sha) -> None:
    if (
        not isinstance(candidate_sha, str)
        or len(candidate_sha) != 40
        or candidate_sha.lower() != candidate_sha
        or any(ch not in "0123456789abcdef" for ch in candidate_sha)
    ):
        raise SelfUpdateToolError("candidate_sha must be a full lowercase Git SHA")


def _check_prepare_arguments(candidate_sha, goal, assertions, iteration_policy, verification_plan) -> None:
    """The checks of a prepare call that need nothing but its arguments."""
    _check_candidate_sha(candidate_sha)
    _check_plan_arguments(goal, assertions, iteration_policy, verification_plan)


def _check_plan_arguments(goal, assertions, iteration_policy, verification_plan) -> None:
    """Those that hold for both kinds of prepare: by commit, or by task under sealed versions."""
    if not isinstance(goal, str) or not goal.strip():
        raise SelfUpdateToolError("goal is required")
    if (
        not isinstance(assertions, list)
        or not assertions
        or any(not isinstance(value, str) or not value.strip() for value in assertions)
    ):
        raise SelfUpdateToolError("assertions must be a non-empty list of strings")
    _iteration_policy(iteration_policy)
    if verification_plan is not None:
        from types import SimpleNamespace
        from openprogram.operations.self_update.verification.verification_plan import validate_plan
        try:
            validate_plan(verification_plan, SimpleNamespace(assertions=tuple(assertions)))
        except ValueError as exc:
            raise SelfUpdateToolError(f"verification_plan: {exc}") from exc


def _precheck(args: dict) -> str | None:
    """Refuse a prepare call with invalid arguments before it asks for approval."""
    if action_of(args) != "prepare":
        return None
    try:
        if args.get("task"):  # Under sealed versions a prepare names its task, not a commit.
            _check_plan_arguments(args.get("goal"), args.get("assertions"),
                                  args.get("iteration_policy"), args.get("verification_plan"))
        else:
            _check_prepare_arguments(args.get("candidate_sha"), args.get("goal"), args.get("assertions"),
                                     args.get("iteration_policy"), args.get("verification_plan"))
    except SelfUpdateToolError as exc:
        return str(exc)
    return None


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
    _check_prepare_arguments(candidate_sha, goal, assertions, iteration_policy, verification_plan)

    worktree = manager.get_worktree(worktree_id)
    if worktree is None:
        raise SelfUpdateToolError(f"worktree does not exist: {worktree_id}")
    if worktree.status is not WorktreeStatus.ACTIVE:
        raise SelfUpdateToolError("candidate worktree must be active")

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

    from openprogram.operations.self_update.control import backend
    backend_name = backend.select(source)
    if backend_name == backend.CHECKOUT:
        return backend.checkout_result()
    _require_backend_host(backend_name)
    # On the development line the controller rebases the candidate onto `dev`,
    # so it may fork from any earlier commit of that line.
    rebases = backend_name == backend.DEV_STABLE
    base_sha = _git(source, "rev-parse", "refs/heads/dev" if rebases else "HEAD")
    if base_sha == candidate_sha:
        raise SelfUpdateToolError("candidate must differ from the source checkout HEAD")
    fork_point = _git(candidate, "merge-base", base_sha, candidate_sha)
    if not rebases and fork_point != base_sha:
        raise SelfUpdateToolError("candidate is not based on the source checkout HEAD")
    changed = tuple(
        path
        for path in _git(
            candidate,
            "diff",
            "--name-only",
            "--diff-filter=ACDMRTUXB",
            "-z",
            f"{fork_point}..{candidate_sha}",
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
            *backend.evidence(backend_name),
        ),
        goal=goal.strip(),
        assertions=tuple(value.strip() for value in assertions),
        iteration_policy=policy,
        timeout_seconds=backend.timeout_seconds(backend_name),
    )
    try:
        request, configs = _freeze_configs(request, req, policy, verification_plan,
                                           candidate=candidate, branch_name=worktree.branch_name)
        state = store.create(request, **configs)
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
        _launch_supervisor(backend_name, store, request.update_id, candidate)
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
    if rebases:
        return {
            "update_id": request.update_id,
            "backend": backend_name,
            "phase": state.phase.value,
            "candidate_sha": candidate_sha,
            "base_sha": base_sha,
            "changed_paths": list(changed),
            "turn_release_pending": True,
            "next": (
                "After this turn ends the controller rebases the candidate onto dev and lands it, "
                "deploys it to the development instance on port 18200 and checks it, waits for the "
                "stable App to go idle, promotes exactly that commit and checks the stable App. "
                "The result arrives in this conversation; self_update() shows progress."
            ),
        }
    from openprogram.operations.self_update.delivery.activation import predict
    activation = predict(Path(DEFAULT_APP_PATH), candidate, candidate_sha)
    return {
        "update_id": request.update_id,
        "phase": state.phase.value,
        "candidate_sha": candidate_sha,
        "base_sha": base_sha,
        "changed_paths": list(changed),
        # hot: nothing restarts; worker: the worker restarts, the App stays
        # open; app: the App quits and reopens.
        "restart": activation.restart,
        "rebuild": list(activation.components) if activation.scope == "incremental" else "complete App",
        "turn_release_pending": True,
    }


# ``UpdateRequest`` names a worktree; going back to a built version has none.
_NO_WORKTREE = "none"


def _running_layout():
    from openprogram.operations.self_update.versions.layout import current_runtime

    runtime = current_runtime()
    if runtime is None:
        raise SelfUpdateToolError("this action needs an installation that runs from sealed versions")
    return runtime


def _task_name(task: Any) -> str:
    from openprogram.operations.self_update.versions.layout import valid_id

    if not valid_id(task):
        raise SelfUpdateToolError(
            "task must be letters, digits, dots, dashes or underscores, starting with a letter or digit"
        )
    return str(task)


def _begin_update(*, task: Any, req: Any, manager: Any) -> dict[str, Any]:
    """Open ``work/<task>`` from ``local`` and make it this turn's working directory."""
    from openprogram.state.worktree.context import set_worktree
    from openprogram.state.worktree.manager import WorktreeError

    _require_local_owner(req)
    name = _task_name(task)
    runtime = _running_layout()
    layout = runtime.layout
    destination = layout.work / name
    if destination.exists():
        raise SelfUpdateToolError(f"task {name} already has a worktree at {destination}")
    layout.work.mkdir(parents=True, exist_ok=True)
    try:
        worktree = manager.create_worktree(
            str(layout.repo), branch_name=f"op/wt/{name}", base_ref="refs/heads/local",
            label=name, parent_session=req.session_id, destination=str(destination),
        )
    except WorktreeError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    set_worktree(worktree.worktree_path)
    return {
        "task": name,
        "worktree_id": worktree.id,
        "path": worktree.worktree_path,
        "next": ('Edit and commit there with your ordinary tools, then call '
                 f'self_update(action="prepare", task="{name}", goal=..., assertions=[...]).'),
    }


def _list_versions() -> dict[str, Any]:
    from openprogram.operations.self_update.versions import pointer, store as sealed

    runtime = _running_layout()
    layout = runtime.layout
    present = pointer.read_or_recover(layout)
    versions = []
    for manifest in sealed.list_versions(layout):
        version = str(manifest["id"])
        versions.append({
            "version": version,
            "commit": manifest.get("commit"),
            "built_at": manifest.get("built_at"),
            "summary": manifest.get("summary"),
            "destructive_migration": manifest.get("destructive_migration"),
            "current": version == present.version,
            "last_good": version == present.last_good,
            "previous_good": version == present.previous_good,
        })
    return {"current": present.version, "probation": present.probation or None, "versions": versions}


def _freeze_configs(request: UpdateRequest, req: Any, policy: IterationPolicy, verification_plan: dict | None,
                    *, candidate: Path | None, branch_name: str) -> tuple[UpdateRequest, dict[str, Any]]:
    """Freeze what the update's later stages must not be able to change.

    A request with no worktree (``candidate`` is None) has no source to repair,
    so it gets no repair config and a failure ends with the diagnosis.
    """
    from openprogram.operations.self_update.verification.verifier_config import freeze_verifier_config
    from openprogram.operations.self_update.verification.verifier_config import config_evidence
    from openprogram.operations.self_update.repair.diagnosis import freeze_config
    from openprogram.operations.self_update.repair.diagnosis import config_evidence as diagnosis_evidence
    from openprogram.operations.self_update.control.continuation import freeze_config as freeze_continuation
    from openprogram.operations.self_update.control.continuation import config_evidence as continuation_evidence
    from openprogram.operations.self_update.repair.source_repair import freeze_config as freeze_repair
    from openprogram.operations.self_update.repair.source_repair import config_evidence as repair_evidence
    from openprogram.operations.self_update.repair.next_candidate import root_config
    from openprogram.operations.self_update.repair.next_candidate import config_evidence as iteration_evidence

    continuation_config = freeze_continuation(request, req)
    verifier_config = freeze_verifier_config(request, req, verification_plan=verification_plan)
    diagnosis_config = freeze_config(request, verifier_config)
    repair_config = (None if candidate is None else
                     freeze_repair(request, verifier_config, candidate_path=str(candidate), branch_name=branch_name))
    iteration_config = root_config(request)
    if policy.mode is IterationMode.BOUNDED_AUTO and (policy.deadline is None or policy.deadline <= time.time()
                                                    or not policy.required_tests):
        raise ValueError("bounded_auto requires a future total deadline and non-empty required_tests")
    if policy.deadline is not None:
        request = replace(request, timeout_seconds=min(request.timeout_seconds, int(policy.deadline - request.created_at)))
    request = replace(request, pre_update_evidence=(
        *request.pre_update_evidence, config_evidence(verifier_config), diagnosis_evidence(diagnosis_config),
        *(() if repair_config is None else (repair_evidence(repair_config),)),
        iteration_evidence(iteration_config), continuation_evidence(continuation_config)))
    configs = {
        "verifier_config": verifier_config, "diagnosis_config": diagnosis_config,
        "iteration_config": iteration_config, "continuation_config": continuation_config,
    }
    if repair_config is not None:
        configs["source_repair_config"] = repair_config
    return request, configs


def _origin_turn(req: Any, assistant_id: str) -> str:
    turn = getattr(req, "user_msg_id", None)
    if not turn and assistant_id.endswith("_reply"):
        turn = assistant_id[: -len("_reply")]
    return turn or assistant_id


def _prepare_version_update(
    *, task: Any, goal: str, assertions: list[str], iteration_policy: Mapping[str, Any] | None, req: Any,
    assistant_id: str, manager: Any, store: SelfUpdateStore, verification_plan: dict | None = None,
) -> dict[str, Any]:
    """``prepare`` on the versions backend: validate, then start or queue."""
    from openprogram.operations.self_update.control import backend

    _require_local_owner(req)
    if not isinstance(assistant_id, str) or not assistant_id.strip():
        raise SelfUpdateToolError("self-update must run inside a persisted chat turn")
    name = _task_name(task)
    runtime = _running_layout()
    layout = runtime.layout
    tree = (layout.work / name).resolve()
    worktree = next((item for item in manager.list_worktrees()
                     if Path(item.worktree_path).resolve() == tree), None)
    if worktree is None:
        raise SelfUpdateToolError(f'no task {name}; call self_update(action="begin", task="{name}") first')
    if worktree.status is not WorktreeStatus.ACTIVE:
        raise SelfUpdateToolError("the task's worktree must be active")
    candidate = _recorded_path(worktree.worktree_path, "candidate worktree")
    if _git(candidate, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SelfUpdateToolError("commit your changes first")
    candidate_sha = _git(candidate, "rev-parse", "HEAD")
    _check_prepare_arguments(candidate_sha, goal, assertions, iteration_policy, verification_plan)
    local = _git(layout.repo, "rev-parse", "refs/heads/local")
    if candidate_sha == local or _git(candidate, "rev-list", "--count", f"{local}..{candidate_sha}") == "0":
        raise SelfUpdateToolError("the task has no commits beyond the running code")
    fork_point = _git(candidate, "merge-base", local, candidate_sha)
    changed = tuple(path for path in _git(
        candidate, "diff", "--name-only", "--diff-filter=ACDMRTUXB", "-z", f"{fork_point}..{candidate_sha}",
    ).split("\0") if path)
    if not changed:
        raise SelfUpdateToolError("candidate commit contains no changes")
    policy = _iteration_policy(iteration_policy)
    request = UpdateRequest(
        update_id=mint_update_id(), session_id=req.session_id, origin_turn_id=_origin_turn(req, assistant_id),
        origin_assistant_id=assistant_id, agent_id=req.agent_id, repo=os.path.normpath(str(layout.repo)),
        worktree_id=worktree.id, base_sha=local, candidate_sha=candidate_sha, changed_paths=changed,
        pre_update_evidence=("git-status:clean", f"source-head:{local}", f"candidate-head:{candidate_sha}"),
        goal=goal.strip(), assertions=tuple(value.strip() for value in assertions), iteration_policy=policy,
        timeout_seconds=backend.timeout_seconds(backend.VERSIONS), kind="change",
        controller_version=runtime.version, task=name,
    )
    try:
        request, configs = _freeze_configs(request, req, policy, verification_plan,
                                           candidate=candidate, branch_name=worktree.branch_name)
    except (SelfUpdateError, ValueError) as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    return _start_version_update(
        request, configs, store, worktree=candidate,
        summary={"task": name},
        next_step=("After this turn ends the running version builds the change, tries it on a copy of your "
                   "data, switches to it and watches it, going back on any failure. "
                   "The result arrives in this conversation; self_update() shows progress."),
    )


def _start_version_update(
    request: UpdateRequest, configs: dict[str, Any], store: SelfUpdateStore, *,
    worktree: Path, summary: dict[str, Any], next_step: str,
) -> dict[str, Any]:
    """Save the request and start its controller, or queue it behind the running one."""
    from openprogram.operations.self_update.control import backend

    result = {"update_id": request.update_id, "backend": backend.VERSIONS, **summary,
              "candidate_sha": request.candidate_sha, "base_sha": request.base_sha,
              "changed_paths": list(request.changed_paths)}
    try:
        state = store.create(request, **configs)
    except ActiveUpdateError:
        position = store.enqueue(request, configs)
        return {**result, "queued": True, "position": position,
                "next": "Another update is running. This one starts when it finishes; self_update() shows the queue."}
    except (SelfUpdateError, ValueError) as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    try:
        backend.launch(backend.VERSIONS, store, request.update_id, worktree=worktree)
    except Exception as exc:
        store.transition(request.update_id, UpdatePhase.ABORTED, expected_phase=UpdatePhase.PREPARING,
                         detail={"reason": "controller launch failed", "error": str(exc)[:1000]})
        raise SelfUpdateToolError(f"the controller could not be started: {exc}") from exc
    return {**result, "phase": state.phase.value, "turn_release_pending": True, "next": next_step}


def _undo_on_development_line(
    *, target: str, req: Any, assistant_id: str, manager: Any, store: SelfUpdateStore
) -> dict[str, Any]:
    """On a machine with stable and dev, an undo is a revert on dev going forward."""
    from openprogram.operations.self_update.control import undo
    from openprogram.operations.worker import dev_instance

    source = dev_instance.source_repo()
    try:
        commit, subject = undo.commit_on_line(source, target, "refs/heads/dev")
    except undo.UndoError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    tip = _git(source, "rev-parse", "refs/heads/dev")
    plan = undo.UndoPlan(undo.UNDO_COMMIT, target, commit, tip, subject)
    try:
        worktree = manager.create_worktree(
            str(source), base_ref="dev", label=f"undo-{commit[:12]}", parent_session=req.session_id
        )
    except WorktreeError as exc:
        raise SelfUpdateToolError(f"could not create a worktree for the revert: {exc}") from exc
    try:
        head = undo.revert_in(Path(worktree.worktree_path), commit)
    except undo.UndoError as exc:
        manager.discard_worktree(worktree.id, force=True)
        raise SelfUpdateToolError(str(exc)) from exc
    return _prepare_update(
        worktree_id=worktree.id, candidate_sha=head, goal=plan.goal, assertions=list(plan.assertions),
        iteration_policy=None, req=req, assistant_id=assistant_id, manager=manager, store=store,
    )


def _submit_version_undo(
    plan: Any, *, req: Any, assistant_id: str, manager: Any, store: SelfUpdateStore
) -> dict[str, Any]:
    """Hand a resolved undo to the versions lifecycle as a schema 2 request."""
    from openprogram.operations.self_update.control import backend, undo

    if not isinstance(assistant_id, str) or not assistant_id.strip():
        raise SelfUpdateToolError("self-update must run inside a persisted chat turn")
    runtime = _running_layout()
    layout = runtime.layout
    fields = plan.request_fields()
    worktree_id, branch, path = _NO_WORKTREE, "local", None
    if plan.kind == undo.UNDO_COMMIT:
        try:
            work = undo.create_revert(layout, plan, manager=manager, parent_session=req.session_id)
        except undo.UndoError as exc:
            raise SelfUpdateToolError(str(exc)) from exc
        fields["candidate_sha"] = work.head
        worktree_id, branch, path = work.worktree_id, work.branch, work.path
    candidate_sha = str(fields.pop("candidate_sha"))
    if candidate_sha == plan.local_tip:
        raise SelfUpdateToolError("OpenProgram already runs that code.")
    changed = tuple(item for item in _git(
        layout.repo, "diff", "--name-only", "--diff-filter=ACDMRTUXB", "-z", f"{candidate_sha}..{plan.local_tip}",
    ).split("\0") if item)
    if not changed:
        raise SelfUpdateToolError("That version has the same files as the running code, so there is nothing to go back from.")
    policy = _iteration_policy(None)
    goal, assertions, base_sha = str(fields.pop("goal")), tuple(fields.pop("assertions")), str(fields.pop("base_sha"))
    request = UpdateRequest(
        update_id=mint_update_id(), session_id=req.session_id, origin_turn_id=_origin_turn(req, assistant_id),
        origin_assistant_id=assistant_id, agent_id=req.agent_id, repo=os.path.normpath(str(layout.repo)),
        worktree_id=worktree_id, base_sha=base_sha, candidate_sha=candidate_sha, changed_paths=changed,
        pre_update_evidence=("git-status:clean", f"source-head:{base_sha}", f"candidate-head:{candidate_sha}"),
        goal=goal, assertions=assertions, iteration_policy=policy,
        timeout_seconds=backend.timeout_seconds(backend.VERSIONS),
        controller_version=runtime.version, **fields,
    )
    try:
        request, configs = _freeze_configs(request, req, policy, None, candidate=path, branch_name=branch)
    except (SelfUpdateError, ValueError) as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    return _start_version_update(
        request, configs, store, worktree=path or layout.repo,
        summary={"kind": plan.kind, "target": plan.target},
        next_step=("After this turn ends the running version tries the older code on a copy of your data, "
                   "switches to it and watches it, returning on any failure. "
                   "The result arrives in this conversation; self_update() shows progress."),
    )


def _undo_update(
    *, target: str, req: Any, assistant_id: str, manager: Any, store: SelfUpdateStore
) -> dict[str, Any]:
    _require_local_owner(req)
    if not isinstance(target, str) or not target.strip():
        raise SelfUpdateToolError("target is required: a version id, or a commit to undo")
    from openprogram.operations.self_update.control import backend, undo

    backend_name = backend.select()
    if backend_name == backend.CHECKOUT:
        return backend.checkout_result()
    if backend_name == backend.DEV_STABLE:
        return _undo_on_development_line(
            target=target.strip(), req=req, assistant_id=assistant_id, manager=manager, store=store
        )
    if backend_name == backend.LEGACY_APP:
        raise SelfUpdateToolError(
            "This packaged App keeps no earlier versions to go back to. "
            "Published releases are changed with `openprogram upgrade` or the Desktop updater."
        )
    _require_backend_host(backend_name)
    from openprogram.operations.self_update.versions.layout import Layout

    try:
        plan = undo.plan(Layout(backend.runtime_root()), target.strip())
    except undo.UndoError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    return _submit_version_undo(plan, req=req, assistant_id=assistant_id, manager=manager, store=store)


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
    from openprogram.operations.self_update.control.projection import read_status
    from openprogram.operations.self_update.types import UpdateNotFoundError
    queued = store.queued() if update_id is None else []
    try:
        status = read_status(store, session_id=req.session_id, update_id=update_id, owner=True)
    except UpdateNotFoundError:
        if queued:
            return {"queued": queued}
        raise SelfUpdateToolError("no matching self-update") from None
    except (SelfUpdateError, ValueError, OSError, KeyError, TypeError):
        raise SelfUpdateToolError("self-update state is unavailable or inconsistent") from None
    return {**status, "queued": queued} if queued else status


def _turn_context() -> tuple[Any, str]:
    req = get_turn_request()
    if req is None:
        raise SelfUpdateToolError("self-update tool requires an active chat turn")
    from openprogram.state.store import _current_turn_id

    assistant_id = _current_turn_id.get()
    if not assistant_id:
        raise SelfUpdateToolError("self-update tool requires an active assistant turn")
    return req, assistant_id


ACTIONS = ("status", "begin", "prepare", "retry", "cancel", "versions", "undo")
# Actions that install something; each binds one exact candidate.
APPROVED_ACTIONS = frozenset({"prepare", "retry", "undo"})


def action_of(args: Mapping[str, Any] | None) -> str:
    """The action a ``self_update`` call names; an omitted action reads status."""
    value = (args or {}).get("action") if isinstance(args, Mapping) else None
    return str(value or "status")


def _overview() -> str:
    from openprogram.operations.self_update.control import backend
    if backend.select() == backend.VERSIONS:
        return """
        Change OpenProgram itself. Writing is never gated; only activation is checked.

        1. self_update(action="begin", task="short-name") opens a private copy of the code and
           makes it this turn's working directory. Edit, test and commit there.
        2. self_update(action="prepare", task, goal, assertions) hands it over. After this turn
           the running version builds the change, tries it on a copy of your data, waits for a
           quiet moment, switches and watches the new version, and goes back on any failure.
           Messages sent during the short switch run right after it.
        3. self_update() reads progress and the queue; self_update(action="versions") lists
           what is kept. self_update(action="cancel") stops an update before the switch.
    """
    return """
        Change and install OpenProgram itself, from a commit in an isolated worktree.

        1. worktree_create a worktree of the OpenProgram repo; edit, test and commit there.
        2. self_update(action="prepare", ...) with that worktree and commit. The supervisor
           rebuilds only the changed parts, installs, verifies the assertions and rolls back
           on failure. The call returns once the request is saved. On a machine with a stable
           and a development instance the commit is rebased onto dev, deployed to the
           development instance, checked, and promoted to the stable App, and a failed
           promotion is undone by a revert commit on dev.
        3. self_update() reads status. After a rollback the supervisor diagnoses, repairs the
           source in isolation and tests it; a tested repair is installed with action="retry".
        4. self_update(action="cancel") stops the update and everything it started.
           A transaction that already activated still finishes verification or rollback.
        5. self_update(action="undo", target=...) goes back to a kept version or removes one
           commit, and goes through the same checks as any other change.

        What restarts depends on the changed paths: web, docs and terminal UI swap in while
        everything runs; Python restarts only the worker; the Desktop shell or dependencies
        restart the App. Published CLI/Desktop releases use `openprogram upgrade` instead.
    """


def _prepare_guide() -> str:
    from openprogram.operations.self_update.control import backend
    from openprogram.operations.self_update.verification.native_checks import NATIVE_ENTRIES
    if backend.select() == backend.VERSIONS:
        return """
        self_update(action="prepare", task, goal, assertions, iteration_policy=None, verification_plan=None)

        - task: the name given to begin. Its worktree must be clean, with commits beyond the
          running code. A change based on an older commit is rebased for you.
        - goal: one sentence on what the change is for.
        - assertions: non-empty list of observable facts the new version must satisfy.
        - iteration_policy, verification_plan: as in the other install path.

        When another update holds the slot this request waits in the queue. The result names
        the update_id or the queue position.
    """
    from openprogram.operations.self_update.verification.system_probe import DOCS_PAGE, OBSERVATION_ENTRIES
    entries = ", ".join(sorted({*OBSERVATION_ENTRIES, *NATIVE_ENTRIES, "ui:main", DOCS_PAGE}))
    return f"""
        self_update(action="prepare", worktree_id, candidate_sha, goal, assertions,
                    iteration_policy=None, verification_plan=None)

        - worktree_id: an active worktree of the OpenProgram repo.
        - candidate_sha: the full 40-character lowercase SHA of its clean HEAD; it must
          change at least one path. It must be based on the source checkout HEAD, except on
          a machine with a stable branch, where it is rebased onto dev for you.
        - goal: one sentence on what the change is for.
        - assertions: non-empty list of observable facts the installed App must satisfy.
          They are numbered acceptance-1, acceptance-2, ... in order.
        - iteration_policy: {{"mode": "approve_each_activation" | "bounded_auto",
          "max_attempts": 1-3 (default 3), "deadline": unix seconds, "allowed_paths": [globs],
          "required_tests": [test paths]}}. The default mode installs each new SHA only when
          retry is called, and that call is decided like any other. bounded_auto installs
          repairs on its own, inside the attempt budget, a future deadline, allowed_paths
          (the first candidate must fit them too) and non-empty required_tests.
        - verification_plan: {{"schema": 1, "checks": [...]}} with one check per assertion:
          {{"id", "assertion_id": "acceptance-N", "entry", "timeout_seconds": 1-60,
          "max_output_bytes": 1-262144 (1-1572864 for ui:main)}}. entry is one of: {entries}.
          /docs/<path>.html reads a page of the installed docs site (for documentation
          changes, e.g. /docs/install/upgrade.html for docs/install/upgrade.html);
          "test:python" adds "argv": [relative script.py, ...] run in the candidate;
          "ui:main" captures the main window (the model needs image input) and may add
          "interaction": {{"kind": "scroll", "delta_y": -1200..1200}} or
          {{"kind": "view", "target": "session" | "dag"}}.
          Without a plan the verifier judges the assertions from its own observations.

        The update may pause and resume active tasks at a safe point, runs a follow-up turn
        in this session afterwards, and repairs in isolation with the listed tests after a
        rollback. The result names the update_id, the predicted restart
        (hot / worker / app) and what will be rebuilt.
    """


def _retry_guide() -> str:
    return """
        self_update(action="retry", update_id, candidate_sha)

        After a rollback, status shows the repaired candidate the supervisor tested. retry
        submits exactly that SHA as the next attempt of the same update: the original goal,
        assertions, scope, model and attempt budget carry over. The result is the new child
        request, not proof that it installed; read status afterwards.
    """


def _undo_guide() -> str:
    return """
        self_update(action="undo", target)

        - target is a version id: return to exactly that version; everything after it stops
          running, and the old line stays reachable under refs/openprogram/undone/.
        - target is a commit: remove that one commit with a revert and keep the later ones.
          If later commits changed the same lines the undo stops and names the files.
        - A version that is no longer on disk is built again from its commit.

        On a machine with a stable and a development instance target is a commit, and the
        revert is landed on dev and promoted like any other change.
        From a terminal, `openprogram versions use <id>` switches at once with no checks.
    """


def _requires_approval(action: str = "status", **_: Any) -> bool:
    return str(action or "status") in APPROVED_ACTIONS


def _with_progress(run):
    """Put what the update is doing, in plain words, at the front of the result."""
    @functools.wraps(run)
    def reported(*args: Any, **kwargs: Any) -> Any:
        from openprogram.operations.self_update.control.progress import annotate

        return annotate(run(*args, **kwargs), SelfUpdateStore())
    return reported


@function(
    name="self_update",
    description=(
        "Install a committed change to OpenProgram itself and follow it through build, "
        "verification, rollback and repair. When the repository has a `stable` branch, prepare "
        "lands the candidate on dev, deploys it to the development instance (port 18200), checks "
        "it, and promotes it to the stable App once that is idle. "
        "Actions: status (default), begin, prepare, retry, cancel, versions, undo. "
        "The first result explains the workflow; each action's parameters come from help."
    ),
    toolset=["core"],
    requires_approval=_requires_approval,
    path_params={},
    unsafe_in=["plan"],
)
@_with_progress
def self_update(
    action: str = "status",
    update_id: str | None = None,
    task: str | None = None,
    worktree_id: str | None = None,
    candidate_sha: str | None = None,
    goal: str | None = None,
    assertions: list[str] | None = None,
    iteration_policy: dict[str, Any] | None = None,
    verification_plan: dict[str, Any] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Run one self-update action.

    Args:
        action: status, begin, prepare, retry, cancel, versions or undo.
        update_id: The update to read, retry or cancel; defaults to the active one.
        task: begin, prepare: a short name for the piece of work (sealed-version installs).
        worktree_id: prepare: the worktree holding the candidate commit.
        candidate_sha: prepare, retry: the exact commit to install.
        goal: prepare: what the change is for.
        assertions: prepare: facts the installed App must satisfy.
        iteration_policy: prepare: retry mode and repair budget.
        verification_plan: prepare: checks bound to each assertion.
        target: undo: the version id to go back to, or the commit to remove.
    """
    req, assistant_id = _turn_context()
    if action == "status":
        return _status_update(update_id=update_id, req=req, store=SelfUpdateStore())
    if action == "cancel":
        return _cancel_iteration(update_id=update_id, req=req, store=SelfUpdateStore())
    if action in {"begin", "versions"}:
        from openprogram.operations.self_update.control import backend
        if backend.select() == backend.CHECKOUT:
            return backend.checkout_result()
        if action == "versions":
            _require_local_owner(req)
            return _list_versions()
        return _begin_update(task=task, req=req, manager=get_manager())
    if action == "prepare" and task:
        from openprogram.operations.self_update.control import backend
        if backend.select() == backend.VERSIONS:
            return _prepare_version_update(
                task=task, goal=goal or "", assertions=assertions, iteration_policy=iteration_policy, req=req,
                assistant_id=assistant_id, manager=get_manager(), store=SelfUpdateStore(),
                verification_plan=verification_plan,
            )
    if action == "prepare":
        return _prepare_update(
            worktree_id=worktree_id or "",
            candidate_sha=candidate_sha or "",
            goal=goal or "",
            assertions=assertions,
            iteration_policy=iteration_policy,
            req=req,
            assistant_id=assistant_id,
            manager=get_manager(),
            store=SelfUpdateStore(),
            verification_plan=verification_plan,
        )
    if action == "retry":
        from openprogram.operations.self_update.control import backend
        from openprogram.operations.self_update.repair.next_candidate import submit
        backend_name = backend.select()
        if backend_name == backend.CHECKOUT:
            return backend.checkout_result()
        if backend_name == backend.DEV_STABLE:
            raise SelfUpdateToolError(
                "On this machine a failed update is repaired forward on dev: fix it in a worktree "
                "and call self_update(action=\"prepare\") again.")
        _require_backend_host(backend_name)
        return submit(update_id, candidate_sha, req=req, assistant_id=assistant_id)
    if action == "undo":
        return _undo_update(
            target=target or "", req=req, assistant_id=assistant_id, manager=get_manager(),
            store=SelfUpdateStore(),
        )
    raise SelfUpdateToolError(f"unknown action {action!r}; use one of {', '.join(ACTIONS)}")


def _cancel_iteration(*, update_id: str | None, req: Any, store: SelfUpdateStore) -> dict[str, Any]:
    """Stop an update and everything its iteration started."""
    _require_local_owner(req)
    from openprogram.operations.self_update.repair import next_candidate
    if update_id and store.remove_queued(update_id):
        return {"update_id": update_id, "status": "stopped", "phase": "aborted"}
    try:
        record = _resolve_record(update_id, store)
    except SelfUpdateError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    if next_candidate._config(store, record) is None:
        # A request without frozen iteration configuration has nothing to stop
        # but itself, and only before activation.
        return _abort_request(record, req=req, store=store)
    try:
        return next_candidate.cancel(record.request.update_id, req, store)
    except (SelfUpdateError, ValueError) as exc:
        raise SelfUpdateToolError(str(exc)) from exc


def _abort_request(record: Any, *, req: Any, store: SelfUpdateStore) -> dict[str, Any]:
    if record.state.phase not in {UpdatePhase.PREPARING, UpdatePhase.BUILDING, UpdatePhase.TRIAL,
                                  UpdatePhase.READY, UpdatePhase.QUIESCING}:
        raise SelfUpdateToolError(f"cannot cancel self-update in {record.state.phase.value}")
    try:
        state = store.transition(
            record.request.update_id,
            UpdatePhase.ABORTED,
            expected_phase=record.state.phase,
            detail={**record.state.detail, "reason": "owner requested cancellation",
                    "cancelled_by_session": req.session_id},
        )
    except SelfUpdateError as exc:
        raise SelfUpdateToolError(str(exc)) from exc
    return {"update_id": record.request.update_id, "status": "stopped", "phase": state.phase.value}


from openprogram.programs._authoring import guides as _guides
from openprogram.programs._authoring import prechecks as _prechecks

_prechecks.register("self_update", _precheck)

_guides.register("self_update", [
    _guides.Guide("self_update", _overview, mode="open", activates=("worktree_create",)),
    _guides.Guide("self_update.prepare", _prepare_guide, summary="parameters, iteration policy and verification plan",
                  required_for=lambda args: action_of(args) == "prepare"),
    _guides.Guide("self_update.retry", _retry_guide, summary="install the repaired candidate after a rollback",
                  required_for=lambda args: action_of(args) == "retry"),
    _guides.Guide("self_update.undo", _undo_guide, summary="go back to a kept version or remove one commit",
                  required_for=lambda args: action_of(args) == "undo"),
])


@function(
    name="self_update_observe",
    description=("For the active post-update verifier only: execute an approved frozen check_id "
                 "and save identity-bound evidence (local HTTP, fixed CLI, candidate script or main-window capture). "
                 "Do not supply execution arguments. Legacy requests accept entry: /api/commands, /api/diagnostics, "
                 "/api/doctor, /healthz, /chat, or a docs page /docs/<path>.html. "
                 "HTML and candidate tests are not installed UI evidence."),
    toolset=["core"],
    path_params={},
)
def self_update_observe(entry: str = "", check_id: str | None = None) -> dict[str, Any]:
    from openprogram.operations.self_update.verification.verification_channel import observe
    result = observe(entry, check_id=check_id)
    if result["entry"] == "ui:main":
        from openprogram.models.providers.types import AgentToolResult
        from openprogram.models.providers.types import ImageContent, TextContent
        capture = json.loads(result["body"])
        image = capture["screenshot"].pop("data")
        result["body"] = json.dumps(capture, allow_nan=False)
        return AgentToolResult(content=[TextContent(text=json.dumps(result, allow_nan=False)),
                                        ImageContent(data=image, mime_type="image/png")])
    return result


__all__ = ["self_update", "self_update_observe", "action_of", "APPROVED_ACTIONS"]
