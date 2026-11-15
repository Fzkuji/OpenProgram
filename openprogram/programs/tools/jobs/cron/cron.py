"""Agent tool for one-time, recurring, and monitor Scheduler tasks.

Tasks are persisted under the active OpenProgram profile and executed by the
persistent worker. Prompt tasks may hold stable Memory references that are
resolved when the task runs and maintained through the existing Memory tools.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any

from openprogram.host._platform import locking

from openprogram.programs._authoring.helpers import read_string_param
from openprogram.programs._host.runtime import function
import logging
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)


NAME = "scheduler"
CRON_ALIAS_NAME = "cron"

DEFAULT_CRON_ENV = "OPENPROGRAM_CRON_PATH"
DEFAULT_SCHEDULER_ENV = "OPENPROGRAM_SCHEDULER_PATH"
DEFAULT_REL_PATH = "scheduler/tasks.json"
LEGACY_REL_PATH = "cron/schedule.json"

DESCRIPTION = (
    "Create, list, update, and delete one-time, recurring, monitor, or webhook "
    "tasks. A task runs a background agent prompt, a shell command, or a reminder "
    "(the `remind` tool sets reminders in fewer words). "
    "To act when something changes, give a recurring or monitor task a `watch` "
    "command: its cron is how often the command is checked, and the task runs "
    "only when the command's output differs from the last check. A `webhook` "
    "task has no schedule: it runs when an outside service POSTs to the URL "
    "the create result gives, with the key it gives. "
    "Tasks run in the persistent OpenProgram worker and may attach stable, "
    "Memory references. For a task worth retaining in long-term Memory, use "
    "memory_update to create or update its record and attach that record here; "
    "when the task closes, update the record or delete it if nothing durable "
    "remains."
)
_STORE_THREAD_LOCK = threading.RLock()
# What a task runs when it fires: an agent turn, a shell command, or a
# reminder shown to the user (and optionally handed to the agent).
EXECUTION_KINDS = ("prompt", "command", "reminder")


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "update", "delete", "get"],
                "description": "What to do.",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression for recurring or monitor tasks.",
            },
            "run_at": {
                "type": "string",
                "description": "Timezone-aware ISO 8601 timestamp for a one-time task.",
            },
            "type": {
                "type": "string",
                "enum": ["once", "recurring", "monitor", "webhook"],
                "description": "Task type. Inferred from run_at/cron when omitted.",
            },
            "title": {
                "type": "string",
                "description": "Short task title.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether the task may run.",
            },
            "memory_refs": {
                "type": "array",
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "memory_id": {"type": "string"},
                    },
                    "required": ["workspace_id", "memory_id"],
                    "additionalProperties": False,
                },
                "description": "Stable Memory blocks resolved at execution time.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt / task the daemon should hand to a fresh agent when the schedule fires. Either `prompt` or `command` is required for create.",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run when the schedule fires — runs directly, no agent involved. Mutually exclusive with `prompt`. Example: `python backup.py` or `rsync -a ~/src /backup/`.",
            },
            "reminder": {
                "type": "string",
                "description": "Reminder text shown to the user in this conversation when the schedule fires. Mutually exclusive with `prompt` and `command`.",
            },
            "wake": {
                "type": "boolean",
                "description": "For a reminder: also start a turn in this conversation so the agent acts on the reminder text.",
            },
            "watch": {
                "type": "string",
                "description": "For a recurring or monitor task: a shell command checked on the cron schedule (sandboxed, like `command`). The task runs only when its output changed since the previous check; the first check records the baseline. The run receives the change.",
            },
            "cwd": {
                "type": "string",
                "description": "Absolute working directory to freeze into the execution spec. Defaults to the active project directory.",
            },
            "notes": {
                "type": "string",
                "description": "Optional free-form notes for your own reference.",
            },
            "id": {
                "type": "string",
                "description": "Entry id. Required for delete / get.",
            },
        },
        "required": ["action"],
    },
}


def _valid_cron(expr: str) -> bool:
    from .worker import valid_cron

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
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
        from openprogram.runtime.permissions.authority import LOCAL_OWNER_SPEAKER, owner_principal_id

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


def execute(
    action: str | None = None,
    title: str | None = None,
    type: str | None = None,
    cron: str | None = None,
    run_at: str | None = None,
    prompt: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
    notes: str | None = None,
    enabled: bool | None = None,
    memory_refs: list[dict[str, str]] | None = None,
    id: str | None = None,
    reminder: str | None = None,
    wake: bool | None = None,
    watch: str | None = None,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op")
    title = title or read_string_param(kw, "title", "name")
    task_type = type or read_string_param(kw, "type", "task_type", "kind")
    cron_expr = cron or read_string_param(kw, "cron", "schedule", "expression")
    run_at = run_at or read_string_param(kw, "run_at", "at")
    prompt = prompt or read_string_param(kw, "prompt", "task", "text")
    command = command or read_string_param(kw, "command", "cmd", "shell")
    reminder = reminder or read_string_param(kw, "reminder", "message")
    cwd = cwd or read_string_param(kw, "cwd", "working_dir")
    notes = notes or read_string_param(kw, "notes", "note", "description")
    entry_id = id or read_string_param(kw, "id", "entry_id", "slug")

    if not action:
        return "Error: `action` is required (create / list / update / delete / get)."
    action = action.lower()
    if action not in {"create", "list", "update", "delete", "get"}:
        return f"Error: unknown action {action!r}. Expected create / list / update / delete / get."
    authority, authority_error = _authorize(action)
    if authority_error:
        return authority_error

    path = _resolve_path()
    entries = _load(path)

    if action == "list":
        if not entries:
            return f"No scheduled tasks in `{path}`."
        lines = [f"Scheduled tasks in `{path}`:"]
        refused = read_refusals()
        for e in entries:
            body = e.get("prompt") or e.get("command") or e.get("reminder") or ""
            kind = "$" if e.get("command") else "⏰" if e.get("reminder") else ">"
            schedule = e.get("run_at") or e.get("cron") or ("on POST" if e.get("type") == "webhook" else "unscheduled")
            if e.get("watch"):
                schedule += f" when `{e['watch'][:40]}` changes"
            line = f"- `{e.get('id','?')}`  {e.get('type','recurring')}  {schedule}  {kind} {body[:80]}"
            if e.get("notes"):
                line += f"   _({e['notes']})_"
            if e.get("id") in refused:
                line += f"   [last run refused: {refused[e['id']]}]"
            lines.append(line)
        return "\n".join(lines)

    if action == "get":
        if not entry_id:
            return "Error: `id` is required for get."
        for e in entries:
            if e.get("id") == entry_id:
                return json.dumps(e, indent=2, ensure_ascii=False)
        return f"Error: no entry with id {entry_id!r}."

    if action == "delete":
        if not entry_id:
            return "Error: `id` is required for delete."
        from openprogram.runtime.scheduler import service

        if not service.delete_task(entry_id):
            return f"Error: no entry with id {entry_id!r}."
        return f"Deleted scheduler task {entry_id!r} from `{path}`."

    if action == "update":
        if not entry_id:
            return "Error: `id` is required for update."
        from openprogram.runtime.scheduler import service

        patch: dict[str, Any] = {}
        for key, value in (
            ("title", title), ("cron", cron_expr), ("run_at", run_at),
            ("notes", notes), ("enabled", enabled), ("wake", wake),
            ("watch", watch),
        ):
            if value is not None:
                patch[key] = value
        for key, value in (
            ("prompt", prompt), ("command", command), ("cwd", cwd),
            ("memory_refs", memory_refs), ("reminder", reminder),
        ):
            if value is not None:
                patch[key] = value
        if not patch:
            return "Error: update requires at least one changed field."
        try:
            updated = service.update_task(entry_id, patch, authority=authority)
        except KeyError:
            return f"Error: no task with id {entry_id!r}."
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(updated, indent=2, ensure_ascii=False)

    if action == "create":
        if sum(bool(value) for value in (prompt, command, reminder)) != 1:
            return "Error: pass exactly one of `prompt` (agent task), `command` (shell), or `reminder`."
        task_type = task_type or ("once" if run_at else "recurring")
        watch = watch or read_string_param(kw, "watch")
        task_title = title or notes or (prompt or command or reminder or "Scheduled task")[:80]
        from openprogram.runtime.scheduler import service

        try:
            new_entry = service.create_task(
                title=task_title,
                task_type=task_type,
                prompt=prompt,
                command=command,
                reminder=reminder,
                wake=bool(wake),
                cron=cron_expr,
                run_at=run_at,
                enabled=True if enabled is None else enabled,
                memory_refs=memory_refs,
                notes=notes or "",
                cwd=cwd,
                authority=authority,
                watch=watch,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        body_label = "prompt" if prompt else "command" if command else "reminder"
        body_value = prompt or command or reminder or ""
        schedule = new_entry.get("run_at") or new_entry.get("cron")
        if new_entry.get("type") == "webhook":
            from openprogram.runtime.scheduler import hooks
            return (
                f"Created webhook task `{new_entry['id']}`:\n"
                f"  {body_label}: {body_value[:160]}\n"
                f"  URL: {hooks.url(new_entry['id'])}\n"
                f"  Key: {new_entry['hook_key']}\n"
                "Give the user both. A service starts the task with a POST to the URL that carries "
                "the key in the X-OpenProgram-Key header, or, for a GitHub webhook, with the key "
                "as the webhook secret. The request body reaches the run as outside data."
            )
        watch_line = f"  watch: {new_entry['watch'][:160]} (runs only when its output changes)\n" \
            if new_entry.get("watch") else ""
        return (
            f"Created scheduler task `{new_entry['id']}` in `{path}`:\n"
            f"  schedule: {schedule}\n"
            f"{watch_line}"
            f"  {body_label}: {body_value[:160]}\n"
            "The persistent OpenProgram worker will execute it."
        )

    raise AssertionError("validated cron action was not handled")


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=["core"],
)(execute)

# Keep the former tool name callable for existing profiles and clients while
# presenting Scheduler as the primary contract.
function(
    name=CRON_ALIAS_NAME,
    description="Compatibility alias for the scheduler tool.",
    parameters=SPEC["parameters"],
    toolset=["core"],
)(execute)


__all__ = ["NAME", "CRON_ALIAS_NAME", "SPEC", "execute", "DESCRIPTION"]
