"""Durable, evidence-bound submission of a repaired self-update candidate."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import logging
import math
import os
import stat
import time

from . import diagnosis, source_repair
from .store import SelfUpdateStore
from .types import IterationMode, UpdatePhase, UpdateRequest, _validate_update_id, mint_update_id
from .verification_channel import _read, _digest
from .verifier_config import load_verifier_config

PREFIX = "iteration-config-sha256:"
_log = logging.getLogger(__name__)


def root_config(request):
    return dict(schema=1, root_id=request.update_id, parent_id=None, parent_sha256=None, attempt=1)


def config_evidence(config):
    return PREFIX + _digest(config)


def _path(store, record, kind):
    return store.root / record.request.update_id / f"iteration-{kind}.json"


def _optional(path):
    try:
        return _read(path)
    except FileNotFoundError:
        return None


def _config(store, record):
    markers = [item for item in record.request.pre_update_evidence if item.startswith(PREFIX)]
    if not markers:
        return None
    config = _read(_path(store, record, "root"))
    if (set(config) != {"schema", "root_id", "parent_id", "parent_sha256", "attempt"}
            or type(config["schema"]) is not int or config["schema"] != 1
            or markers != [config_evidence(config)] or type(config["attempt"]) is not int
            or config["attempt"] != record.state.attempt):
        raise ValueError("iteration authorization changed")
    _validate_update_id(config["root_id"])
    return config


def chain(store, record, *, read_only=False):
    """Read at most the three originally permitted attempts, root first."""
    records = [record]
    while True:
        current = records[0]
        config = _config(store, current)
        if config is None or config["parent_id"] is None:
            if current.state.attempt != 1 or config is not None and config != root_config(current.request):
                raise ValueError("invalid root iteration")
            break
        if len(records) >= 3:
            raise ValueError("iteration ancestry exceeds budget")
        parent = store._load_unlocked(_validate_update_id(config["parent_id"]), read_only=read_only)
        if config["parent_sha256"] != _digest(parent.request.to_dict()) or parent.state.attempt + 1 != current.state.attempt:
            raise ValueError("iteration parent changed")
        records.insert(0, parent)
    root = records[0]
    root_verifier = load_verifier_config(store, root)
    for item in records:
        config = _config(store, item)
        if config is not None and config["root_id"] != root.request.update_id:
            raise ValueError("iteration root changed")
        for field in ("session_id", "agent_id", "repo", "base_sha", "goal", "assertions", "iteration_policy", "app_path"):
            if getattr(item.request, field) != getattr(root.request, field):
                raise ValueError("iteration expanded original authorization")
        verifier = load_verifier_config(store, item)
        if any(verifier[k] != root_verifier[k] for k in root_verifier if k not in {"attempt", "response_format"}):
            raise ValueError("iteration verifier differs from original model and authority")
    return records


def _pointer(store):
    pointer = _optional(store.root / "iteration-pending.json")
    if pointer is None:
        return None
    if set(pointer) != {"schema", "update_id"} or type(pointer["schema"]) is not int or pointer["schema"] != 1:
        raise ValueError("invalid iteration pointer")
    return _validate_update_id(pointer["update_id"])


def status(store, record):
    value = _optional(_path(store, record, "status"))
    if value is not None and (set(value) != {"schema", "status", "reason", "update_id", "child_id", "at"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["status"] not in {"awaiting_approval", "submitting", "submitted", "stopped"}
            or value["update_id"] != record.request.update_id or not isinstance(value["reason"], str)
            or type(value["at"]) not in (float, int) or not math.isfinite(value["at"])):
        raise ValueError("invalid iteration status")
    if value is not None and value["child_id"] is not None:
        _validate_update_id(value["child_id"])
    return value


def _status(store, record, phase, reason, child_id=None):
    previous = status(store, record)
    if previous is not None and previous["status"] == "stopped":
        return
    if child_id is None and previous is not None:
        child_id = previous["child_id"]
    store._write_json(_path(store, record, "status"), dict(schema=1, status=phase, reason=reason[:1000],
        update_id=record.request.update_id, child_id=child_id, at=time.time()))


def _remove_pointer(store, update_id):
    if _pointer(store) == update_id:
        (store.root / "iteration-pending.json").unlink()
        store._fsync_directory(store.root)


def supersede(store):
    """Caller holds the store lock for a new independent owner request."""
    update_id = _pointer(store)
    if update_id is not None:
        record = store._load_unlocked(update_id)
        _status(store, record, "stopped", "superseded by a new owner update")
        _remove_pointer(store, update_id)


def _failure(store, record):
    evidence = diagnosis._evidence(store, record)
    failure = evidence["failure"]
    if failure["verifier_verdict"] == "inconclusive":
        raise ValueError("inconclusive failure cannot authorize another installation")
    receipt = failure["verifier_result"]
    if receipt is None:
        # No model explanation or changing process identity enters this key.
        if failure["verifier_verdict"] is not None or not failure["error"]:
            raise ValueError("missing failure evidence")
        # Only the controller's fixed probe stages identify a system failure.
        # Free exception/model text cannot manufacture a distinct next attempt.
        checks = {f"system probe failed: {check}": check for check in (
            "owner_auth", "identity", "health", "web", "doctor", "websocket",
        )}
        check = checks.get(failure["error"])
        if check is None:
            raise ValueError("unrecognized system failure evidence")
        return _digest({"kind": "system-failure", "check": check})
    from .verification_channel import _check_signature
    grant = _read(store.root / record.request.update_id / f"verifier-grant-{record.state.attempt}.json")
    _check_signature(receipt, grant["token"])
    if (receipt.get("verdict") != "fail" or receipt.get("candidate_sha") != record.request.candidate_sha
            or receipt.get("update_id") != record.request.update_id or receipt.get("attempt") != record.state.attempt
            or receipt.get("grant_sha256") != _digest(grant)
            or record.state.detail.get("verifier_grant_sha256") != _digest(grant)):
        raise ValueError("verifier failure is not bound to candidate")
    rows = receipt.get("result", {}).get("assertions", [])
    if not rows or any(row["status"] == "inconclusive" for row in rows):
        raise ValueError("inconclusive or missing assertion evidence")
    return _digest(dict(kind="assertions", failed=sorted(row["id"] for row in rows if row["status"] != "pass")))


def check_failure_history(store, records):
    fingerprints = [_failure(store, item) for item in records]
    if len(fingerprints) >= 2 and fingerprints[-1] == fingerprints[-2]:
        raise ValueError("repeated failure")
    return fingerprints


def _candidate(store, record):
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import JobStatus
    from openprogram.programs.tools.system.self_update import _git, _recorded_path, _validate_registered_worktree, _validate_candidate_snapshot
    from .repair_candidate import allowed_path, _edits, _file
    repair_request, config = source_repair._load(store, record)
    job = load_job(record.request.session_id, repair_request["job_id"])
    source_repair._check_job(store, record, repair_request, config, job)
    if job.status is not JobStatus.COMPLETED or job.cancel_requested_at is not None:
        raise ValueError("repair Job was not completed")
    output = source_repair._output(record, repair_request, job)
    result = source_repair.read_result(store, record)
    if result is None or result["status"] != "candidate_ready":
        raise ValueError("candidate has no passing required tests")
    manifest = _read(source_repair._path(store, record, "candidate"))
    if result["candidate"] != manifest:
        raise ValueError("candidate evidence changed")
    intent = _read(source_repair._path(store, record, "intent"))
    if intent["output"] != output:
        raise ValueError("repair output differs from materialization intent")
    worktree = intent["worktree"]
    for key in ("worktree_path", "branch_name"):
        if manifest[key] != worktree[key]:
            raise ValueError("repair worktree binding changed")
    if manifest["worktree_id"] != worktree["id"] or worktree["parent_session"] != record.request.session_id:
        raise ValueError("repair worktree owner changed")
    source = _recorded_path(record.request.repo, "source")
    candidate = _recorded_path(manifest["worktree_path"], "repaired candidate")
    original = _recorded_path(config["candidate_path"], "original candidate")
    sha = manifest["candidate_sha"]
    _validate_registered_worktree(source, original, record.request.candidate_sha, config["branch_name"])
    _validate_candidate_snapshot(original, record.request.candidate_sha)
    _validate_registered_worktree(source, candidate, sha, manifest["branch_name"])
    _validate_candidate_snapshot(candidate, sha)
    if _git(candidate, "merge-base", record.request.candidate_sha, sha) != record.request.candidate_sha:
        raise ValueError("repair is not a descendant of the original candidate")
    expected = _edits(record.request, original, output["edits"])
    changed_edits = set(filter(None, _git(candidate, "diff", "--name-only", "-z", record.request.candidate_sha, sha).split("\0")))
    if changed_edits != {p.relative_to(original).as_posix() for p, (before, after) in expected.items() if before != after}:
        raise ValueError("candidate differs from approved repair edits")
    for path, (_, after) in expected.items():
        if _file(candidate, path.relative_to(original).as_posix())[1] != after:
            raise ValueError("candidate contents differ from repair output")
    changed = tuple(filter(None, _git(candidate, "diff", "--name-only", "-z", record.request.base_sha, sha).split("\0")))
    if list(changed) != manifest["changed_paths"] or manifest["base_sha"] != record.request.base_sha:
        raise ValueError("candidate full diff changed")
    for path in changed:
        allowed_path(record.request, path)
    tests = manifest["tests"]
    if not tests or len(tests) != len(record.request.iteration_policy.required_tests):
        raise ValueError("required tests missing")
    if len(set(record.request.iteration_policy.required_tests)) != len(tests):
        raise ValueError("ambiguous required test evidence")
    for index, (command, test) in enumerate(zip(record.request.iteration_policy.required_tests, tests)):
        log = store.root / record.request.update_id / f"repair-test-{record.state.attempt}-{index}" / "test.log"
        if (test["command"] != command or test["candidate_sha"] != sha or type(test["exit_code"]) is not int
                or test["exit_code"] != 0 or test["log_path"] != str(log) or log.resolve() != log):
            raise ValueError("required test binding changed")
        fd = os.open(log, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_048_576 or info.st_nlink != 1:
                raise ValueError("invalid required test log")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                data = handle.read(1_048_577)
        finally:
            if fd >= 0:
                os.close(fd)
        if len(data) > 1_048_576 or hashlib.sha256(data).hexdigest() != test["log_sha256"]:
            raise ValueError("required test log changed")
    return manifest, result


def validate(store, record, sha, *, automatic):
    from .maintenance import load_maintenance
    records = chain(store, record)
    root = records[0]
    if _optional(_path(store, root, "stop")) is not None:
        raise ValueError("iteration was cancelled")
    state = status(store, record)
    if state is not None and state["status"] == "stopped":
        raise ValueError("iteration was stopped")
    if load_maintenance(store) is not None:
        raise ValueError("maintenance prevents another update")
    policy = root.request.iteration_policy
    if record.state.attempt >= policy.max_attempts:
        raise ValueError("attempt budget exhausted")
    if policy.deadline is not None and time.time() >= policy.deadline:
        raise ValueError("original iteration deadline exhausted")
    if automatic and (_config(store, root) is None or policy.mode is not IterationMode.BOUNDED_AUTO
                      or policy.deadline is None or not policy.allowed_paths or not policy.required_tests):
        raise ValueError("original bounded installation authorization missing")
    fingerprints = check_failure_history(store, records)
    manifest, result = _candidate(store, record)
    if manifest["candidate_sha"] != sha or sha in {item.request.candidate_sha for item in records} | {root.request.base_sha}:
        raise ValueError("candidate SHA is stale or not new")
    if automatic:
        from .iteration import evaluate_iteration, TestEvidence
        diagnostic = diagnosis._result(store, record)["diagnosis"]
        decision = evaluate_iteration(root.request, attempt=record.state.attempt, candidate_sha=sha,
            changed_paths=tuple(manifest["changed_paths"]),
            test_evidence=tuple(TestEvidence(command=t["command"], candidate_sha=t["candidate_sha"], exit_code=t["exit_code"])
                                for t in manifest["tests"]), failure_kind=diagnostic["category"],
            failure_fingerprints=tuple(fingerprints), rollback_succeeded=True, now=time.time())
        if not decision.allowed:
            raise ValueError(decision.reason)
    return records, manifest, result


def prepare(store, record):
    """Caller holds store lock; publication precedes repair pointer deletion."""
    result = source_repair.read_result(store, record)
    if result is None or result["status"] != "candidate_ready" or status(store, record) is not None:
        return
    try:
        records, manifest, result = validate(store, record, result["candidate"]["candidate_sha"], automatic=False)
        if _pointer(store) not in (None, record.request.update_id):
            raise ValueError("another iteration owns pending submission")
        proposal = dict(schema=1, update_id=record.request.update_id, root_id=records[0].request.update_id,
                        parent_sha256=_digest(record.request.to_dict()), candidate_sha=manifest["candidate_sha"],
                        result_sha256=_digest(result))
        existing = _optional(_path(store, record, "proposal"))
        if existing not in (None, proposal):
            raise ValueError("iteration proposal changed")
        store._write_json(_path(store, record, "proposal"), proposal)
        store._write_json(store.root / "iteration-pending.json", dict(schema=1, update_id=record.request.update_id))
        _status(store, record, "awaiting_approval", "candidate awaits original envelope or exact owner approval")
    except Exception as exc:
        _status(store, record, "stopped", str(exc))


def _reservation(store, record, records, manifest, result, *, automatic, assistant_id=None, turn_id=None, reserved=None):
    from .verifier_config import _response_format, config_evidence as verifier_evidence
    from openprogram.providers.structured_output import normalize_response_format
    root = records[0]
    now = time.time() if reserved is None else reserved.created_at
    timeout = root.request.timeout_seconds
    if root.request.iteration_policy.deadline is not None:
        timeout = min(timeout, int(root.request.iteration_policy.deadline - now))
    if timeout < 1:
        raise ValueError("insufficient original deadline")
    child = replace(root.request, update_id=mint_update_id() if reserved is None else reserved.update_id, created_at=now, timeout_seconds=timeout,
                    worktree_id=manifest["worktree_id"], candidate_sha=manifest["candidate_sha"],
                    changed_paths=tuple(manifest["changed_paths"]),
                    pre_update_evidence=("source-repair-result-sha256:" + _digest(result),),
                    origin_assistant_id=assistant_id or root.request.origin_assistant_id,
                    origin_turn_id=turn_id or root.request.origin_turn_id)
    config = dict(schema=1, root_id=root.request.update_id, parent_id=record.request.update_id,
                  parent_sha256=_digest(record.request.to_dict()), attempt=record.state.attempt + 1)
    verifier = deepcopy(load_verifier_config(store, root))
    verifier["attempt"] = config["attempt"]
    verifier["response_format"] = asdict(normalize_response_format(_response_format(child, config["attempt"])))
    diagnostic = diagnosis.freeze_config(child, verifier)
    repair = source_repair.freeze_config(child, verifier, candidate_path=manifest["worktree_path"], branch_name=manifest["branch_name"])
    child = replace(child, pre_update_evidence=(*child.pre_update_evidence, config_evidence(config), verifier_evidence(verifier),
        diagnosis.config_evidence(diagnostic), source_repair.config_evidence(repair)))
    return dict(schema=1, parent_sha256=_digest(record.request.to_dict()), result_sha256=_digest(result),
                automatic=automatic, request=child.to_dict(), iteration_config=config,
                verifier_config=verifier, diagnosis_config=diagnostic, source_repair_config=repair)


def submit(update_id, candidate_sha, *, req=None, assistant_id=None, _resume=False):
    """Trusted automatic call or the body of the forced one-shot owner tool."""
    from openprogram.programs.tools.system.self_update import _require_local_owner
    from .launcher import launch_supervisor
    store = SelfUpdateStore()
    with store._locked():
        record = store._load_unlocked(_validate_update_id(update_id))
        reservation = _optional(_path(store, record, "next"))
        if _resume and reservation is None:
            raise ValueError("cannot resume without original reservation")
        automatic = reservation["automatic"] if _resume else req is None
        if not automatic and not _resume:
            _require_local_owner(req)
            if req.session_id != record.request.session_id or not assistant_id:
                raise ValueError("retry requires the original owner session and persisted turn")
        records, manifest, result = validate(store, record, candidate_sha, automatic=automatic)
        proposal = _read(_path(store, record, "proposal"))
        if proposal != dict(schema=1, update_id=update_id, root_id=records[0].request.update_id,
                parent_sha256=_digest(record.request.to_dict()), candidate_sha=candidate_sha, result_sha256=_digest(result)):
            raise ValueError("pending proposal differs from current evidence")
        if reservation is None:
            if store._load_active_unlocked() is not None:
                raise ValueError("another update is active")
            reservation = _reservation(store, record, records, manifest, result, automatic=automatic,
                assistant_id=assistant_id, turn_id=getattr(req, "user_msg_id", None))
            store._write_json(_path(store, record, "next"), reservation)
        elif (reservation["parent_sha256"] != _digest(record.request.to_dict())
              or reservation["result_sha256"] != _digest(result) or reservation["automatic"] != automatic):
            raise ValueError("reserved iteration no longer matches evidence or approval")
        child = UpdateRequest.from_dict(reservation["request"])
        expected = _reservation(store, record, records, manifest, result, automatic=automatic,
            assistant_id=child.origin_assistant_id, turn_id=child.origin_turn_id, reserved=child)
        if reservation != expected or (automatic and (child.origin_assistant_id != records[0].request.origin_assistant_id
                                                       or child.origin_turn_id != records[0].request.origin_turn_id)):
            raise ValueError("reserved child expanded original inputs")
        if time.time() >= child.created_at + child.timeout_seconds:
            raise ValueError("original reserved child deadline expired")
        if child.candidate_sha != candidate_sha:
            raise ValueError("reserved child candidate changed")
        active = store._load_active_unlocked()
        if active is not None and active.request.update_id != child.update_id:
            raise ValueError("another update owns active slot")
        if not (store.root / child.update_id).exists():
            store._create_unlocked(child, **{key: reservation[key] for key in (
                "iteration_config", "verifier_config", "diagnosis_config", "source_repair_config")})
        current = store._load_unlocked(child.update_id)
        if current.request != child:
            raise ValueError("published child differs from reservation")
        if automatic and current.state.phase is UpdatePhase.PREPARING:
            store._transition_unlocked(child.update_id, UpdatePhase.STAGING, expected_phase=UpdatePhase.PREPARING,
                detail={"iteration_release": True, "parent_id": update_id, "reservation_sha256": _digest(reservation)})
        _status(store, record, "submitting", "original child reserved", child.update_id)
    try:
        launch_supervisor(child.update_id, resume=(store.root / child.update_id / "supervisor.sh").exists())
    except Exception as exc:
        with store._locked():
            current = store._load_unlocked(child.update_id)
            if current.state.phase in {UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY}:
                store._transition_unlocked(child.update_id, UpdatePhase.ABORTED, expected_phase=current.state.phase,
                    detail={**current.state.detail, "reason": "supervisor launch failed: " + str(exc)[:1000]})
            _status(store, record, "stopped", "supervisor launch failed: " + str(exc), child.update_id)
            _remove_pointer(store, update_id)
        raise
    with store._locked():
        _status(store, record, "submitted", "child handed to external supervisor", child.update_id)
        _remove_pointer(store, update_id)
        final_status = status(store, record)["status"]
    return dict(update_id=update_id, child_id=child.update_id, candidate_sha=candidate_sha,
                attempt=current.state.attempt, status=final_status, turn_release_pending=not automatic)


def dispatch_pending():
    store = SelfUpdateStore()
    update_id = None
    try:
        with store._locked():
            update_id = _pointer(store)
            if update_id is None:
                return
            record = store._load_unlocked(update_id)
            records = chain(store, record)
            reservation = _optional(_path(store, record, "next"))
            if reservation is None and (_config(store, records[0]) is None
                    or records[0].request.iteration_policy.mode is not IterationMode.BOUNDED_AUTO):
                return
            proposal = _read(_path(store, record, "proposal"))
        submit(update_id, proposal["candidate_sha"], _resume=reservation is not None)
    except Exception as exc:
        _log.warning("Could not submit next self-update candidate", exc_info=True)
        if update_id is not None:
            with store._locked():
                record = store._load_unlocked(update_id)
                _status(store, record, "stopped", str(exc))
                _remove_pointer(store, update_id)


def ensure_not_cancelled(store, record):
    config = _config(store, record)
    if config is not None and _optional(store.root / config["root_id"] / "iteration-stop.json") is not None:
        raise ValueError("iteration was cancelled")


def summary(store, record, *, read_only=False):
    if _config(store, record) is None:
        return None
    records = chain(store, record, read_only=read_only)
    root = records[0]
    return dict(root_id=root.request.update_id, parent_id=_config(store, record)["parent_id"],
                attempt=record.state.attempt, max_attempts=root.request.iteration_policy.max_attempts,
                deadline=root.request.iteration_policy.deadline, submission=status(store, record),
                stopped=_optional(_path(store, root, "stop")) is not None)


def approval_preview(update_id, candidate_sha, req):
    from openprogram.programs.tools.system.self_update import _require_local_owner
    _require_local_owner(req)
    store = SelfUpdateStore()
    with store._locked():
        record = store._load_unlocked(_validate_update_id(update_id))
        if record.request.session_id != req.session_id:
            raise ValueError("retry belongs to another session")
        records, manifest, _ = validate(store, record, candidate_sha, automatic=False)
        return dict(root_id=records[0].request.update_id, candidate_sha=candidate_sha,
            goal=record.request.goal, assertions=record.request.assertions, changed_paths=manifest["changed_paths"],
            tests=manifest["tests"], next_attempt=record.state.attempt + 1,
            max_attempts=record.request.iteration_policy.max_attempts, deadline=record.request.iteration_policy.deadline)


def cancel(update_id, req):
    from openprogram.programs.tools.system.self_update import _require_local_owner
    from openprogram.agent.job import get_runner
    from openprogram.agent.job.store import load_job
    _require_local_owner(req)
    store = SelfUpdateStore()
    jobs = []
    with store._locked():
        record = store._load_unlocked(_validate_update_id(update_id))
        if record.request.session_id != req.session_id:
            raise ValueError("iteration belongs to another session")
        root = chain(store, record)[0]
        store._write_json(_path(store, root, "stop"), dict(schema=1, root_id=root.request.update_id, at=time.time()))
        current = root
        for _ in range(3):
            _status(store, current, "stopped", "owner cancelled iteration")
            _remove_pointer(store, current.request.update_id)
            if source_repair._pointer(store) == current.request.update_id:
                source_repair.cancel_pending(store, reason="owner cancelled iteration")
            if diagnosis._pointer(store) == current.request.update_id:
                diagnosis.cancel_pending(store, reason="owner cancelled iteration")
            for source, kind in (("self_update_diagnose", "diagnose"), ("self_update_repair", "repair")):
                job_id = f"self-update:{current.request.update_id}:{kind}:{current.state.attempt}"
                job = load_job(req.session_id, job_id)
                if job is not None and job.source == source and job.spawn_caller == current.request.origin_assistant_id:
                    jobs.append(job_id)
            if current.state.phase in {UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY}:
                store._transition_unlocked(current.request.update_id, UpdatePhase.ABORTED,
                    expected_phase=current.state.phase, detail={**current.state.detail, "reason": "owner cancelled iteration"})
            reservation = _optional(_path(store, current, "next"))
            if reservation is None or not (store.root / reservation["request"]["update_id"]).exists():
                break
            child = store._load_unlocked(_validate_update_id(reservation["request"]["update_id"]))
            if chain(store, child)[0].request.update_id != root.request.update_id:
                raise ValueError("reserved child belongs to another iteration")
            current = child
    for job_id in jobs:
        get_runner().cancel_execution(job_id, reason="owner cancelled self-update iteration")
    return dict(root_id=root.request.update_id, status="stopped")
