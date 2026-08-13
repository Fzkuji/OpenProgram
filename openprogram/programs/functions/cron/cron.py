"""cron tool — schedule recurring agent tasks.

Persists cron entries to a JSON file. A separate daemon (not shipped in
this commit) is responsible for actually waking up and firing them.
Ships the registration surface first so agents / users can describe
schedules now, and we can land the executor in a follow-up.

Storage: ``$OPENPROGRAM_CRON_PATH`` or ``~/.openprogram/cron/schedule.json``.
Each entry is ``{"id", "cron", "prompt", "created_at", "notes"}``. ``id`` is
a stable 8-char hex slug so the agent can delete by a name it knows.

Actions:

  create  add a new schedule — requires ``cron`` expression + ``prompt``
  list    return all schedules
  delete  remove a schedule by id
  get     read a single schedule by id

Cron expression is validated loosely (five whitespace-separated fields);
we don't parse the wildcard semantics here — that belongs to the
executor. Keeping validation lenient means users can use any standard
cron dialect (Vixie, Quartz-ish, @daily macros) and the daemon decides
what it supports.

Credit: shape follows openclaw / hermes cron tools; execution layer
is deferred.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from typing import Any

from ..._helpers import read_string_param
from ..._runtime import function


NAME = "cron"

DEFAULT_CRON_ENV = "OPENPROGRAM_CRON_PATH"
DEFAULT_REL_PATH = "cron/schedule.json"

DESCRIPTION = (
    "Register / list / delete recurring agent tasks. Entries are persisted "
    "to a JSON file; the companion `openprogram cron-worker` process fires "
    "each `prompt` when its `cron` expression matches. Creating an entry "
    "only schedules it — if no worker is running, entries accumulate until "
    "one starts."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "delete", "get"],
                "description": "What to do.",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression, e.g. `0 9 * * *`. Required for create.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt / task the daemon should hand to a fresh agent when the schedule fires. Either `prompt` or `command` is required for create.",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run when the schedule fires — runs directly, no agent involved. Mutually exclusive with `prompt`. Example: `python backup.py` or `rsync -a ~/src /backup/`.",
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


_CRON_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_CRON_MACROS = {"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly", "@reboot"}


def _valid_cron(expr: str) -> bool:
    if expr.strip().lower() in _CRON_MACROS:
        return True
    return bool(_CRON_RE.match(expr))


def _resolve_path() -> str:
    override = os.environ.get(DEFAULT_CRON_ENV)
    if override:
        return os.path.abspath(override)
    return os.path.expanduser(f"~/.openprogram/{DEFAULT_REL_PATH}")


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
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    from openprogram.agent.authority import authority_from_message
    from openprogram.agent.run_control import get_current_session_id
    from openprogram.store import _current_turn_id

    return authority_from_message(
        get_current_session_id() or "", _current_turn_id.get() or "",
    )


def _authorize(action: str) -> tuple[dict[str, Any], str | None]:
    from openprogram.agent.authority import (
        has_capability,
        normalize_authority,
        owner_principal_id,
    )

    authority = normalize_authority(_caller_authority())
    capability = (
        "schedule.create" if action == "create" else
        "schedule.manage" if action == "delete" else "fs.read"
    )
    if not authority or not has_capability(authority, capability):
        return {}, f"Error: authority tier does not allow {capability}."
    if action in {"create", "delete"} and not (
        authority["speaker_kind"] == "owner"
        and authority["interaction"] == "interactive"
        and authority["principal_id"] == owner_principal_id()
    ):
        return {}, "Error: schedule changes require the local interactive owner."
    return authority, None


def _build_execution_spec(
    *, kind: str, body: str, cwd: str, entry_id: str,
    authority: dict[str, Any],
) -> dict[str, Any]:
    from openprogram import sandbox

    policy = sandbox.resolve_policy(required=True)
    assert policy is not None
    spec: dict[str, Any] = {
        "version": 1,
        "kind": kind,
        kind: body,
        "cwd": os.path.realpath(cwd),
        "sandbox_policy": sandbox.policy_to_dict(policy),
        "policy_hash": sandbox.policy_hash(policy),
    }
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
    if kind == "prompt":
        from openprogram.agent.run_control import get_current_session_id

        session_id = get_current_session_id() or f"cron-{entry_id}"
        agent_id = "main"
        try:
            from openprogram.agent.session_db import default_db
            session = default_db().get_session(session_id) or {}
            agent_id = session.get("agent_id") or agent_id
        except Exception:
            pass
        spec.update({
            "session_id": session_id,
            "agent_id": agent_id,
            "permission_mode": "ask",
        })
    spec["spec_hash"] = _json_hash(spec)
    spec["signature"] = _signature(spec, create_key=True)
    return spec


def _verify_execution_spec(
    value: Any,
) -> tuple[dict[str, Any] | None, Any | None, str | None]:
    from openprogram import sandbox

    if not isinstance(value, dict):
        return None, None, "missing immutable execution spec"
    signature = value.get("signature")
    expected_spec_hash = value.get("spec_hash")
    unsigned = {
        k: v for k, v in value.items() if k not in {"spec_hash", "signature"}
    }
    if not isinstance(expected_spec_hash, str) or not hmac.compare_digest(
        expected_spec_hash, _json_hash(unsigned)
    ):
        return None, None, "execution spec hash mismatch"
    signed = {k: v for k, v in value.items() if k != "signature"}
    try:
        expected_signature = _signature(signed, create_key=False)
    except ValueError as exc:
        return None, None, str(exc)
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature, expected_signature,
    ):
        return None, None, "execution spec owner signature mismatch"
    try:
        policy = sandbox.policy_from_dict(value.get("sandbox_policy"))
    except ValueError as exc:
        return None, None, str(exc)
    expected_policy_hash = value.get("policy_hash")
    if not isinstance(expected_policy_hash, str) or not hmac.compare_digest(
        expected_policy_hash, sandbox.policy_hash(policy)
    ):
        return None, None, "sandbox policy hash mismatch"
    kind = value.get("kind")
    if kind not in {"command", "prompt"}:
        return None, None, "execution spec kind must be command or prompt"
    if not isinstance(value.get(kind), str) or not value[kind].strip():
        return None, None, f"execution spec {kind} is empty"
    cwd = value.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None, None, "execution spec cwd must be absolute"
    if not os.path.isdir(cwd):
        return None, None, f"execution spec cwd does not exist: {cwd}"
    if kind == "prompt" and not all(
        isinstance(value.get(key), str) and value[key]
        for key in ("session_id", "agent_id")
    ):
        return None, None, "prompt execution spec needs session_id and agent_id"
    job_authority = value.get("job_authority")
    if not isinstance(job_authority, dict):
        return None, None, "missing immutable job authority"
    expected_authority_hash = job_authority.get("authority_hash")
    raw_authority = {
        key: item for key, item in job_authority.items()
        if key != "authority_hash"
    }
    if not isinstance(expected_authority_hash, str) or not hmac.compare_digest(
        expected_authority_hash, _json_hash(raw_authority),
    ):
        return None, None, "job authority hash mismatch"
    if job_authority.get("authority_tier") != "owner" \
            or job_authority.get("kind") != kind:
        return None, None, "job authority does not match execution kind"
    try:
        from openprogram.agent.authority import owner_principal_id

        current_owner = owner_principal_id()
    except Exception as exc:
        return None, None, f"owner identity unavailable: {exc}"
    if job_authority.get("principal_id") != current_owner:
        return None, None, "job authority belongs to a different owner"
    return value, policy, None


def execute(
    action: str | None = None,
    cron: str | None = None,
    prompt: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
    notes: str | None = None,
    id: str | None = None,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op")
    cron_expr = cron or read_string_param(kw, "cron", "schedule", "expression")
    prompt = prompt or read_string_param(kw, "prompt", "task", "text")
    command = command or read_string_param(kw, "command", "cmd", "shell")
    cwd = cwd or read_string_param(kw, "cwd", "working_dir")
    notes = notes or read_string_param(kw, "notes", "note", "description")
    entry_id = id or read_string_param(kw, "id", "entry_id", "slug")

    if not action:
        return "Error: `action` is required (create / list / delete / get)."
    action = action.lower()
    if action not in {"create", "list", "delete", "get"}:
        return f"Error: unknown action {action!r}. Expected create / list / delete / get."
    authority, authority_error = _authorize(action)
    if authority_error:
        return authority_error

    path = _resolve_path()
    entries = _load(path)

    if action == "list":
        if not entries:
            return f"No cron entries in `{path}`."
        lines = [f"Cron entries in `{path}`:"]
        for e in entries:
            body = e.get("prompt") or e.get("command") or ""
            kind = "$" if e.get("command") else ">"
            line = f"- `{e.get('id','?')}`  {e.get('cron','?')}  {kind} {body[:80]}"
            if e.get("notes"):
                line += f"   _({e['notes']})_"
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
        keep = [e for e in entries if e.get("id") != entry_id]
        if len(keep) == len(entries):
            return f"Error: no entry with id {entry_id!r}."
        _save(path, keep)
        return f"Deleted cron entry {entry_id!r} from `{path}`."

    if action == "create":
        if not cron_expr:
            return "Error: `cron` expression is required for create."
        if prompt and command:
            return "Error: pass either `prompt` (agent task) or `command` (shell), not both."
        if not prompt and not command:
            return "Error: either `prompt` or `command` is required for create."
        if not _valid_cron(cron_expr):
            return (
                f"Error: {cron_expr!r} doesn't look like a cron expression "
                "(want 5 fields like `0 9 * * *`, or a macro like `@daily`)."
            )
        if cwd and not os.path.isabs(cwd):
            return f"Error: cwd must be absolute, got {cwd!r}."
        if cwd and not os.path.isdir(cwd):
            return f"Error: cwd does not exist: {cwd}"
        if not cwd:
            from openprogram.worktree.context import current_worktree_path
            cwd = current_worktree_path() or os.getcwd()
        new_entry: dict[str, Any] = {
            "id": _mint_id(),
            "cron": cron_expr.strip(),
            "notes": notes or "",
            "created_at": int(time.time()),
        }
        if prompt:
            new_entry["prompt"] = prompt
            body_label, body_value = "prompt", prompt
        else:
            new_entry["command"] = command
            body_label, body_value = "command", command
        new_entry["execution"] = _build_execution_spec(
            kind=body_label,
            body=body_value,
            cwd=cwd,
            entry_id=new_entry["id"],
            authority=authority,
        )
        entries.append(new_entry)
        _save(path, entries)
        return (
            f"Created cron entry `{new_entry['id']}` in `{path}`:\n"
            f"  schedule: {new_entry['cron']}\n"
            f"  {body_label}: {body_value[:160]}\n"
            "Start the worker in another shell to fire entries:\n"
            "  openprogram cron-worker            # run until Ctrl+C\n"
            "  openprogram cron-worker --list     # show which entries match now"
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
    max_result_chars=40_000,
)(execute)


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]
