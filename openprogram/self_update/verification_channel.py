"""Bind live observations and durable Job results to one verifier authorization."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid

from .rollback_intent import load_rollback_intent
from .store import SelfUpdateStore
from .types import UpdatePhase
from .verifier_config import load_verifier_config, verifier_prompt
from .verification import validate_verifier_result

_MAX_JSON = 2_097_152
_REFERENCE = re.compile(r"observation:([0-9a-f]{32}):([0-9a-f]{64})")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sign(value, token: str) -> str:
    return hmac.new(token.encode(), _digest(value).encode(), hashlib.sha256).hexdigest()


def _check_signature(value, token: str) -> None:
    payload = {k: v for k, v in value.items() if k != "signature"}
    signature = value.get("signature")
    if (not isinstance(token, str) or not isinstance(signature, str)
            or not hmac.compare_digest(signature, _sign(payload, token))):
        raise ValueError("verification receipt signature does not match")


def _read(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > _MAX_JSON:
            raise ValueError("invalid private verification file")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            value = SelfUpdateStore._loads_json(handle.read(_MAX_JSON + 1))
        if not isinstance(value, dict):
            raise ValueError("verification file must be an object")
        return value
    finally:
        if fd >= 0:
            os.close(fd)


def _paths(store, record):
    directory = store.root / record.request.update_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("invalid verification directory")
    return directory, directory / f"verifier-grant-{record.state.attempt}.json", directory / f"verifier-result-{record.state.attempt}.json"


def issue_grant(store, update_id: str, system_gate: dict) -> dict:
    """Controller-only issuance; the returned token must not enter model context."""
    with store._locked():
        record = store._load_unlocked(update_id)
        if record.state.phase is not UpdatePhase.ACTIVATING:
            raise ValueError("verifier grant requires activating state")
        _, path, _ = _paths(store, record)
        if path.exists() or path.is_symlink():
            raise ValueError("verifier grant already exists")
        now = time.time()
        grant = dict(schema=1, update_id=update_id, candidate_sha=record.request.candidate_sha,
                     attempt=record.state.attempt, job_id=f"self-update:{update_id}:verify:{record.state.attempt}",
                     worker_pid=system_gate["worker_pid"], system_gate_sha256=_digest(system_gate),
                     issued_at=now, deadline=min(now + 600, record.request.created_at + record.request.timeout_seconds),
                     token=secrets.token_urlsafe(32))
        if grant["deadline"] <= now:
            raise ValueError("verification deadline expired before grant")
        store._write_json(path, grant)
        return grant


def load_grant(store, record) -> dict:
    if record.state.phase is not UpdatePhase.VERIFYING or load_rollback_intent(store, record) is not None:
        raise ValueError("verification is not active")
    _, path, _ = _paths(store, record)
    grant = _read(path)
    if set(grant) != {"schema", "update_id", "candidate_sha", "attempt", "job_id", "worker_pid",
                      "system_gate_sha256", "issued_at", "deadline", "token"}:
        raise ValueError("malformed verifier grant")
    gate = record.state.detail.get("system_gate", {})
    if (
        _digest(grant) != record.state.detail.get("verifier_grant_sha256")
        or type(grant["schema"]) is not int or grant["schema"] != 1
        or grant["update_id"] != record.request.update_id or grant["candidate_sha"] != record.request.candidate_sha
        or type(grant["attempt"]) is not int or grant["attempt"] != record.state.attempt
        or grant["job_id"] != f"self-update:{record.request.update_id}:verify:{record.state.attempt}"
        or type(grant["worker_pid"]) is not int or grant["worker_pid"] <= 0
        or grant["worker_pid"] != gate.get("worker_pid") or grant["system_gate_sha256"] != _digest(gate)
        or not isinstance(grant["token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", grant["token"])
        or any(type(grant[k]) not in (int, float) or not math.isfinite(grant[k]) for k in ("issued_at", "deadline"))
        or not record.request.created_at <= grant["issued_at"] <= time.time() < grant["deadline"]
        or grant["deadline"] > min(grant["issued_at"] + 600, record.request.created_at + record.request.timeout_seconds)
    ):
        raise ValueError("verifier grant does not match the active update or deadline")
    return grant


def _check_job(store, record, grant, job) -> None:
    from openprogram.agent.authority import normalize_authority
    config = load_verifier_config(store, record)
    if job is None or (
        job.id != grant["job_id"] or job.parent_session_id != record.request.session_id
        or job.source != "self_update_verify" or job.agent_id != config["agent_id"]
        or job.context_mode != "clean" or job.parent_msg_id is not None or job.advance_head
        or job.spawn_caller != record.request.origin_assistant_id or job.prompt != verifier_prompt(record, config)
        or normalize_authority(job) != config["authority"]
        or any(getattr(job, key) != config[key] for key in ("profile_snapshot", "model_override", "tools_override", "response_format"))
        or record.state.dispatch is None or record.state.dispatch.job_id != job.id
        or record.state.dispatch.claimed_by != f"worker:{grant['worker_pid']}"
    ):
        raise ValueError("verifier Job differs from its frozen execution contract")


def _observation_context(store):
    from openprogram.agent.turn_request_context import get_turn_request
    from openprogram.agent.run_control import get_current_execution_id, is_cancelled
    from openprogram.agent.authority import owner_principal_id
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import JobStatus
    record = store._load_active_unlocked()
    if record is None:
        raise ValueError("no active verifier")
    grant = load_grant(store, record)
    req = get_turn_request()
    if (req is None or req.source != "self_update_verify" or req.session_id != record.request.session_id
        or req.principal_id != owner_principal_id() or req.authority_tier != "owner"
        or get_current_execution_id() != grant["job_id"] or grant["worker_pid"] != os.getpid()
        or is_cancelled(record.request.session_id)):
        raise ValueError("observation requires the authorized running verifier")
    job = load_job(record.request.session_id, grant["job_id"])
    _check_job(store, record, grant, job)
    if job.status is not JobStatus.RUNNING or job.cancel_requested_at is not None:
        raise ValueError("verifier is not running or has been cancelled")
    return record, grant


def observe(entry: str = "", *, check_id: str | None = None) -> dict:
    from .system_probe import OBSERVATION_ENTRIES, observe_system
    from .verification_plan import resolve_check
    from .native_checks import NATIVE_ENTRIES, observe_native
    if check_id is None and (not isinstance(entry, str) or entry not in OBSERVATION_ENTRIES):
        raise ValueError("unsupported read-only observation entry")
    store = SelfUpdateStore()
    with store._locked():
        record, grant = _observation_context(store)
        config = load_verifier_config(store, record)
        check = None
        if config["schema"] == 2:
            if entry != "":
                raise ValueError("planned verification accepts only check_id")
            check = resolve_check(config["verification_plan"], check_id)
        elif check_id is not None:
            raise ValueError("legacy verifier has no verification plan")
    if check is None:
        observed = observe_system(record, entry)
    elif check["entry"] == "ui:main":
        from .ui_checks import observe_ui
        observed = observe_ui(store, record, check, grant)
    elif check["entry"] in NATIVE_ENTRIES:
        def revalidate():
            with store._locked():
                current, current_grant = _observation_context(store)
                if current.request.update_id != record.request.update_id or current_grant != grant:
                    raise ValueError("verification changed during native execution")
        observed = observe_native(store, record, check, grant["deadline"], revalidate)
    else:
        observed = observe_system(record, check["entry"],
                                  timeout_seconds=min(check["timeout_seconds"], grant["deadline"] - time.time()),
                                  max_output_bytes=check["max_output_bytes"])
    if grant["token"] in json.dumps(observed, allow_nan=False):
        raise ValueError("observation contains a private verification credential")
    with store._locked():
        current, current_grant = _observation_context(store)
        if current.request.update_id != record.request.update_id or current_grant != grant:
            raise ValueError("verification changed during observation")
        directory, _, _ = _paths(store, current)
        evidence_dir = directory / "observations"
        if evidence_dir.is_symlink() or (evidence_dir.exists() and not evidence_dir.is_dir()):
            raise ValueError("invalid evidence directory")
        evidence_dir.mkdir(mode=0o700, exist_ok=True)
        if len(list(evidence_dir.iterdir())) >= 32:
            raise ValueError("verifier observation budget exhausted")
        evidence = dict(schema=1, update_id=record.request.update_id, candidate_sha=record.request.candidate_sha,
                        attempt=record.state.attempt, job_id=grant["job_id"], grant_sha256=_digest(grant), **observed)
        if check is not None:
            evidence.update(check_id=check["id"], assertion_id=check["assertion_id"],
                            plan_sha256=_digest(config["verification_plan"]))
        evidence["signature"] = _sign(evidence, grant["token"])
        identifier = uuid.uuid4().hex
        reference = f"observation:{identifier}:{_digest(evidence)}"
        store._write_json(evidence_dir / f"{identifier}.json", evidence)
        return {"evidence_ref": reference, **observed["observation"]}


def _resolve_evidence(store, record, grant, result):
    from .verification_plan import resolve_check
    config = load_verifier_config(store, record)
    directory, _, _ = _paths(store, record)
    evidence_dir = directory / "observations"
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise ValueError("observation evidence is unavailable")
    for assertion in result.assertions:
        for ref in assertion.evidence_refs:
            match = _REFERENCE.fullmatch(ref)
            if match is None:
                raise ValueError("unsupported evidence reference")
            evidence = _read(evidence_dir / f"{match[1]}.json")
            if _digest(evidence) != match[2]:
                raise ValueError("observation evidence changed")
            _check_signature(evidence, grant["token"])
            observed = evidence.get("observation", {})
            gate = evidence.get("system_gate", {})
            if config["schema"] == 2:
                check = resolve_check(config["verification_plan"], evidence.get("check_id"))
                if check["entry"] == "ui:main":
                    from .ui_checks import validate_observation
                    validate_observation(observed, record, check, grant)
                from .native_checks import NATIVE_ENTRIES, validate_execution
                if check["entry"] in NATIVE_ENTRIES:
                    from .source_repair import _config
                    candidate_config = _config(store, record) if check["entry"] == "test:python" else None
                    validate_execution(observed, check, record.request, passed=assertion.status == "pass",
                                       candidate_config=candidate_config)
                if (evidence.get("plan_sha256") != _digest(config["verification_plan"])
                        or evidence.get("assertion_id") != assertion.id or check["assertion_id"] != assertion.id
                        or observed.get("entry") != check["entry"]
                        or len(observed.get("body", "").encode("utf-8")) > check["max_output_bytes"]):
                    raise ValueError("observation differs from its frozen verification check")
            if (
                evidence.get("schema") != 1 or evidence.get("update_id") != record.request.update_id
                or evidence.get("candidate_sha") != record.request.candidate_sha or evidence.get("attempt") != record.state.attempt
                or evidence.get("job_id") != grant["job_id"] or evidence.get("grant_sha256") != _digest(grant)
                or gate.get("candidate_sha") != record.request.candidate_sha or gate.get("worker_pid") != grant["worker_pid"]
                or observed.get("entry") != assertion.entry or observed.get("observed_at") != assertion.observed_at
                or not grant["issued_at"] <= assertion.observed_at <= gate.get("verified_at", 0) <= grant["deadline"]
            ):
                raise ValueError("observation evidence does not match this assertion and verifier")


def consume_result(store, update_id: str, token: str) -> dict | None:
    """Controller-only consumption; pending Jobs return None, never pass."""
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import JobStatus, TERMINAL_STATUSES
    with store._locked():
        record = store._load_unlocked(update_id)
        grant = load_grant(store, record)
        if not isinstance(token, str) or not hmac.compare_digest(token, grant["token"]):
            raise ValueError("invalid verifier result authorization")
        _, _, result_path = _paths(store, record)
        job = load_job(record.request.session_id, grant["job_id"])
        if job is None:
            return None
        _check_job(store, record, grant, job)
        if job.status not in TERMINAL_STATUSES:
            return None
        if job.result_text is not None and (not isinstance(job.result_text, str) or len(job.result_text.encode()) > _MAX_JSON):
            raise ValueError("verifier Job result exceeds the size limit")
        job_digest = _digest(job.to_dict())
        if result_path.exists() or result_path.is_symlink():
            receipt = _read(result_path)
            _check_signature(receipt, grant["token"])
            if receipt.get("grant_sha256") != _digest(grant) or receipt.get("job_sha256") != job_digest:
                raise ValueError("verifier authorization was already consumed for another result")
            return receipt
        verdict, reason, result = "inconclusive", "verifier did not complete successfully", None
        if job.status is JobStatus.COMPLETED and job.cancel_requested_at is None:
            try:
                if (not isinstance(job.result_text, str) or len(job.result_text.encode()) > _MAX_JSON
                    or type(job.started_at) not in (float, int) or type(job.completed_at) not in (float, int)
                    or not grant["issued_at"] <= job.started_at <= job.completed_at <= min(time.time(), grant["deadline"])):
                    raise ValueError("verifier result size or execution time is invalid")
                result = validate_verifier_result(SelfUpdateStore._loads_json(job.result_text), record.request,
                                                 attempt=record.state.attempt, not_before=job.started_at, now=job.completed_at)
                _resolve_evidence(store, record, grant, result)
                verdict, reason = result.verdict, "validated durable Job result and live observation references"
            except Exception:
                result, reason = None, "invalid verifier result or unresolved observation evidence"
        receipt = dict(schema=1, update_id=update_id, candidate_sha=record.request.candidate_sha,
                       attempt=record.state.attempt, job_id=grant["job_id"], grant_sha256=_digest(grant),
                       job_sha256=job_digest, verdict=verdict, reason=reason, consumed_at=time.time(),
                       result=None if result is None else result.to_dict())
        receipt["signature"] = _sign(receipt, grant["token"])
        store._write_json(result_path, receipt)
        return receipt
