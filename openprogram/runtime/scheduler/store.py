"""The Scheduler's task store: where tasks are kept, and the signed spec each one runs.

``<state>/scheduler/tasks.json`` holds every task. Each task carries an
immutable execution spec (what it runs, where, and whose authority it runs
with) signed with a key kept beside the file, so the worker runs only what
an owner's call wrote. The ``scheduler`` tool, the Scheduler API and the
worker read and write tasks only through this module; ``service`` is the
CRUD built on it.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import contextlib
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any

from openprogram.host._platform import locking
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)

DEFAULT_CRON_ENV = "OPENPROGRAM_CRON_PATH"
DEFAULT_SCHEDULER_ENV = "OPENPROGRAM_SCHEDULER_PATH"
DEFAULT_REL_PATH = "scheduler/tasks.json"
LEGACY_REL_PATH = "cron/schedule.json"
#: The task file was saved: the scheduler worker (``worker.py``) reads it
#: again and sleeps until its next due entry.
TOPIC = "schedule.entries"

_STORE_THREAD_LOCK = threading.RLock()
# What a task runs when it fires: an agent turn, a shell command, or a
# reminder shown to the user (and optionally handed to the agent).
EXECUTION_KINDS = ("prompt", "command", "reminder")


def _valid_cron(expr: str) -> bool:
    from openprogram.runtime.scheduler.worker import valid_cron

    return valid_cron(expr)


def _resolve_path() -> str:
    override = os.environ.get(DEFAULT_SCHEDULER_ENV) or os.environ.get(DEFAULT_CRON_ENV)
    if override:
        return os.path.abspath(override)
    from openprogram.state.paths import get_state_dir

    state = get_state_dir()
    path = state / DEFAULT_REL_PATH
    legacy = state / LEGACY_REL_PATH
    if not path.exists() and legacy.is_file():
        migration_lock = str(state / ".scheduler-store-migration")
        with _store_lock(migration_lock):
            if not path.exists() and legacy.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                for name in (".signing-key", "worker-state.json", "logs"):
                    old = legacy.parent / name
                    new = path.parent / name
                    if old.exists() and not new.exists():
                        shutil.move(str(old), str(new))
                shutil.move(str(legacy), str(path))
                marker = legacy.parent / "migrated-to-scheduler"
                marker.write_text(f"{path}\n", encoding="utf-8")
    return str(path)


def _load(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return [e for e in data["entries"] if isinstance(e, dict)]
    return []


def _save(path: str, entries: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="schedule-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"entries": entries}, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        _keep_previous(path, len(entries))
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    from openprogram.host._platform import changes
    changes.signal(TOPIC)


PREVIOUS_SUFFIX = ".previous"


def _keep_previous(path: str, count: int) -> None:
    """Before a save that leaves fewer tasks, keep the list as it was beside
    it (``tasks.json.previous``), so a task removed by mistake can be read back."""
    if count >= len(_load(path)):
        return
    with contextlib.suppress(OSError):
        shutil.copyfile(path, path + PREVIOUS_SUFFIX)
        os.chmod(path + PREVIOUS_SUFFIX, 0o600)


@contextmanager
def _store_lock(path: str):
    """Serialize Scheduler read-modify-write operations across threads/processes."""
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with _STORE_THREAD_LOCK, open(lock_path, "a+b") as handle:
        locking.flock(handle.fileno(), locking.LOCK_EX)
        try:
            yield
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)


def _mint_id() -> str:
    return uuid.uuid4().hex[:8]


def _json_hash(value: dict) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _signing_key_path() -> str:
    return os.path.join(os.path.dirname(_resolve_path()) or ".", ".signing-key")


def _signing_key(*, create: bool) -> bytes:
    path = _signing_key_path()
    try:
        key = open(path, "rb").read()
    except FileNotFoundError:
        if not create:
            raise ValueError("cron signing key is missing")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        key = secrets.token_bytes(32)
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            )
        except FileExistsError:
            key = open(path, "rb").read()
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
    if len(key) != 32:
        raise ValueError("cron signing key is invalid")
    return key


def _signature(value: dict, *, create_key: bool) -> str:
    return hmac.new(
        _signing_key(create=create_key), _canonical_json(value), hashlib.sha256,
    ).hexdigest()


def _caller_authority() -> dict[str, Any]:
    """The authority of whoever asked for this turn, not of the reply it runs in."""
    from openprogram.runtime.permissions.authority import turn_origin
    from openprogram.runtime.execution.run_control import get_current_session_id
    from openprogram.state.store import _current_turn_id

    return turn_origin(
        get_current_session_id() or "", _current_turn_id.get() or "",
    )[1]


def _authorize(action: str) -> tuple[dict[str, Any], str | None]:
    from openprogram.state.authority import has_capability, normalize_authority
    from openprogram.runtime.permissions.authority import owner_principal_id

    authority = normalize_authority(_caller_authority())
    capability = (
        "schedule.create" if action == "create" else
        "schedule.manage" if action in {"update", "delete"} else "fs.read"
    )
    if not authority or not has_capability(authority, capability):
        return {}, f"Error: authority tier does not allow {capability}."
    if action in {"create", "update", "delete"} and not (
        authority["authority_tier"] == "owner"
        and authority["principal_id"] == owner_principal_id()
    ):
        return {}, "Error: schedule changes are the owner's."
    return authority, None


def _origin_session() -> tuple[str, str]:
    """The conversation creating the task and its Agent; no conversation is ""."""
    from openprogram.runtime.execution.run_control import get_current_session_id

    session_id = get_current_session_id() or ""
    agent_id = "main"
    if session_id:
        try:
            from openprogram.runtime.context.session_db import default_db
            session = default_db().get_session(session_id) or {}
            agent_id = session.get("agent_id") or agent_id
        except Exception:
            # Unknown agent: the task runs under the main agent.
            _log.debug("origin session agent unavailable session=%s", session_id, exc_info=True)
    return session_id, agent_id


def _build_execution_spec(
    *, kind: str, body: str, cwd: str, entry_id: str,
    authority: dict[str, Any], memory_refs: list[dict[str, str]] | None = None,
    wake: bool = False, origin: tuple[str, str] | None = None,
    watch: str | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "version": 2,
        "kind": kind,
        kind: body,
        "cwd": os.path.realpath(cwd),
        "memory_refs": list(memory_refs or []),
    }
    if watch:
        # The check command is code that runs on every tick: signed like the body.
        spec["watch"] = watch
    job_authority: dict[str, Any] = {
        "version": 1,
        "principal_id": authority["principal_id"],
        "creator": {
            "speaker_kind": authority["speaker_kind"],
            "speaker_id": authority["speaker_id"],
            "speaker_display": authority["speaker_display"],
            "interaction": authority["interaction"],
        },
        "authority_tier": authority["authority_tier"],
        "kind": kind,
        "created_at": int(time.time()),
    }
    job_authority["authority_hash"] = _json_hash(job_authority)
    spec["job_authority"] = job_authority
    session_id, agent_id = origin or _origin_session()
    if kind == "prompt":
        spec.update({
            "session_id": session_id or f"cron-{entry_id}",
            "agent_id": agent_id,
        })
    elif kind == "command":
        # The conversation that set it, whose Sandbox switch applies at each fire.
        spec["session_id"] = session_id
    elif kind == "reminder":
        # A reminder belongs to the conversation it was set in: it is shown
        # there, and a waking reminder starts its turn there.
        if wake and not session_id:
            raise ValueError("a reminder that wakes the agent must be set from a conversation")
        spec.update({"session_id": session_id, "agent_id": agent_id, "wake": bool(wake)})
    spec["spec_hash"] = _json_hash(spec)
    spec["signature"] = _signature(spec, create_key=True)
    return spec


def _verify_execution_spec(
    value: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """A task's signed spec, or why it may not run.

    Version 1 specs embed a sandbox policy; it is covered by the signature
    like every other field and otherwise ignored. A version 1 spec set from
    a chat channel no longer runs.
    """
    if not isinstance(value, dict):
        return None, "missing immutable execution spec"
    signature = value.get("signature")
    expected_spec_hash = value.get("spec_hash")
    unsigned = {
        k: v for k, v in value.items() if k not in {"spec_hash", "signature"}
    }
    if not isinstance(expected_spec_hash, str) or not hmac.compare_digest(
        expected_spec_hash, _json_hash(unsigned)
    ):
        return None, "execution spec hash mismatch"
    signed = {k: v for k, v in value.items() if k != "signature"}
    try:
        expected_signature = _signature(signed, create_key=False)
    except ValueError as exc:
        return None, str(exc)
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature, expected_signature,
    ):
        return None, "execution spec owner signature mismatch"
    kind = value.get("kind")
    if kind not in EXECUTION_KINDS:
        return None, "execution spec kind must be command, prompt, or reminder"
    if not isinstance(value.get(kind), str) or not value[kind].strip():
        return None, f"execution spec {kind} is empty"
    if "watch" in value and not (isinstance(value["watch"], str) and value["watch"].strip()):
        return None, "execution spec watch is empty"
    cwd = value.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None, "execution spec cwd must be absolute"
    if not os.path.isdir(cwd):
        return None, f"execution spec cwd does not exist: {cwd}"
    if kind == "prompt" and not all(
        isinstance(value.get(key), str) and value[key]
        for key in ("session_id", "agent_id")
    ):
        return None, "prompt execution spec needs session_id and agent_id"
    if kind == "reminder" and not (
        isinstance(value.get("session_id"), str)
        and isinstance(value.get("wake"), bool)
        and (value["session_id"] or not value["wake"])
    ):
        return None, "reminder execution spec needs a session to wake"
    try:
        from openprogram.state.memory.references import normalize as normalize_memory_refs

        normalized_refs = normalize_memory_refs(value.get("memory_refs") or [])
    except ValueError as exc:
        return None, str(exc)
    if normalized_refs != (value.get("memory_refs") or []):
        return None, "memory_refs are not canonical"
    if normalized_refs and kind != "prompt":
        return None, "memory_refs require a prompt execution spec"
    job_authority = value.get("job_authority")
    if not isinstance(job_authority, dict):
        return None, "missing immutable job authority"
    expected_authority_hash = job_authority.get("authority_hash")
    raw_authority = {
        key: item for key, item in job_authority.items()
        if key != "authority_hash"
    }
    if not isinstance(expected_authority_hash, str) or not hmac.compare_digest(
        expected_authority_hash, _json_hash(raw_authority),
    ):
        return None, "job authority hash mismatch"
    if job_authority.get("authority_tier") != "owner" \
            or job_authority.get("kind") != kind:
        return None, "job authority does not match execution kind"
    try:
        from openprogram.runtime.permissions.authority import owner_principal_id
        from openprogram.state.authority import LOCAL_OWNER_SPEAKER

        current_owner = owner_principal_id()
    except Exception as exc:
        return None, f"owner identity unavailable: {exc}"
    if job_authority.get("principal_id") != current_owner:
        return None, "job authority belongs to a different owner"
    if value.get("version") == 1 and (job_authority.get("creator") or {}).get("speaker_id") != LOCAL_OWNER_SPEAKER:
        # Back then any approved chat sender counted as the owner. Nothing
        # recorded whether this one still does, so the task does not run as one.
        return None, ("this task was set from a chat before chat senders stopped counting as the owner; "
                      "set it again from one of your own conversations")
    return value, None


def _refusals_path() -> str:
    return os.path.join(os.path.dirname(_resolve_path()) or ".", "refusals.json")


def read_refusals() -> dict[str, str]:
    """Each task's latest refused run, by task id."""
    try:
        with open(_refusals_path(), encoding="utf-8") as stream:
            found = json.load(stream)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def report_refusal(entry_id: str, text: str | None) -> None:
    """Record why a task's run was refused; ``None`` clears it after a run that started."""
    path = _refusals_path()
    with _store_lock(path):
        found = read_refusals()
        if text is None and entry_id not in found:
            return
        if text is None:
            del found[entry_id]
        else:
            found[entry_id] = text
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(found, stream, indent=2, ensure_ascii=False)
