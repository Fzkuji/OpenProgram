"""Scheduler CRUD shared by the Agent tool, REST API, and worker."""

from __future__ import annotations

from datetime import datetime
import os
import time
from typing import Any

from openprogram.state.memory.references import (
    normalize as normalize_memory_refs,
    resolve as resolve_memory_refs,
)
from openprogram.programs.tools.jobs.cron import cron as cron_tool


TASK_TYPES = frozenset({"once", "recurring", "monitor", "webhook"})
# Task types whose cron may check a ``watch`` command instead of firing outright.
WATCH_TYPES = frozenset({"recurring", "monitor"})
_EXTRA_FIELDS = frozenset({"legacy_commitment_id", "legacy_source"})


def _parse_run_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("run_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("run_at must include a timezone")
    return parsed.isoformat()


def _run_minute(run_at: Any) -> datetime | None:
    """The moment a one-time task runs, to the minute: the editor shows no seconds
    and sends the time back in UTC, so the same time can be written differently."""
    try:
        return datetime.fromisoformat(str(run_at).replace("Z", "+00:00")).replace(second=0, microsecond=0)
    except ValueError:
        return None


def _validate_schedule(task_type: str, cron: str | None, run_at: str | None) -> tuple[str | None, str | None]:
    if task_type == "webhook":
        if cron or run_at:
            raise ValueError("webhook tasks run when called, not on a schedule")
        return None, None
    if task_type == "once":
        if cron:
            raise ValueError("once tasks use run_at, not cron")
        if not run_at:
            raise ValueError("run_at is required for an enabled once task")
        return None, _parse_run_at(run_at)
    if run_at:
        raise ValueError(f"{task_type} tasks use cron, not run_at")
    if not cron or not cron_tool._valid_cron(cron):
        raise ValueError("cron must be a supported five-field expression or macro")
    return cron.strip(), None


def _validate_watch(task_type: str, watch: Any) -> str | None:
    watch = str(watch or "").strip() or None
    if watch and task_type not in WATCH_TYPES:
        raise ValueError("watch applies only to recurring and monitor tasks")
    if watch and len(watch) > 4000:
        raise ValueError("watch must be at most 4000 characters")
    return watch


def create_task(
    *,
    title: str,
    task_type: str,
    prompt: str | None = None,
    command: str | None = None,
    reminder: str | None = None,
    wake: bool = False,
    cron: str | None = None,
    run_at: str | None = None,
    enabled: bool = True,
    memory_refs: list[dict[str, str]] | None = None,
    notes: str = "",
    cwd: str | None = None,
    authority: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    watch: str | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "").strip().lower()
    if task_type not in TASK_TYPES:
        raise ValueError("task_type must be once, recurring, monitor, or webhook")
    watch = _validate_watch(task_type, watch)
    title = str(title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("title is required and must be at most 200 characters")
    prompt = str(prompt or "").strip() or None
    command = str(command or "").strip() or None
    reminder = str(reminder or "").strip() or None
    if sum(bool(body) for body in (prompt, command, reminder)) != 1:
        raise ValueError("pass exactly one of prompt, command, or reminder")
    if task_type == "monitor" and not prompt:
        raise ValueError("monitor tasks require a prompt")
    if wake and not reminder:
        raise ValueError("wake applies only to reminders")
    if not enabled and task_type == "once" and not run_at:
        cron_value, run_at_value = None, None
    else:
        cron_value, run_at_value = _validate_schedule(task_type, cron, run_at)
    refs = normalize_memory_refs(memory_refs)
    resolve_memory_refs(refs)
    if refs and not prompt:
        raise ValueError("memory_refs are supported only for prompt tasks")
    if cwd is None:
        from openprogram.state.worktree.context import current_worktree_path

        cwd = current_worktree_path() or os.getcwd()
    if not os.path.isabs(cwd) or not os.path.isdir(cwd):
        raise ValueError("cwd must be an existing absolute directory")
    if authority is None:
        from openprogram.runtime.permissions.authority import local_owner_authority

        authority = local_owner_authority()
    path = cron_tool._resolve_path()
    with cron_tool._store_lock(path):
        entry_id = cron_tool._mint_id()
        body_kind, body = next(
            (kind, value) for kind, value in
            (("prompt", prompt), ("command", command), ("reminder", reminder)) if value
        )
        now = int(time.time())
        entry: dict[str, Any] = {
            "id": entry_id,
            "title": title,
            "type": task_type,
            "enabled": bool(enabled),
            "notes": str(notes or ""),
            "created_at": now,
            "updated_at": now,
            "memory_refs": refs,
            body_kind: body,
        }
        if body_kind == "reminder":
            entry["wake"] = bool(wake)
        # Where the task was set, for that conversation's Activity panel. Not
        # authority: what runs is the signed execution spec.
        origin_session = cron_tool._origin_session()[0]
        if origin_session:
            entry["session_id"] = origin_session
        if watch:
            entry["watch"] = watch
        if task_type == "webhook":
            from . import hooks
            entry["hook_key"] = hooks.new_key()
        if cron_value:
            entry["cron"] = cron_value
        if run_at_value:
            entry["run_at"] = run_at_value
        if extra:
            unsupported = set(extra) - _EXTRA_FIELDS
            if unsupported:
                raise ValueError("unsupported scheduler metadata field")
            entry.update(extra)
        entry["execution"] = cron_tool._build_execution_spec(
            kind=body_kind,
            body=body,
            cwd=cwd,
            entry_id=entry_id,
            authority=authority,
            memory_refs=refs,
            wake=bool(wake),
            watch=watch,
        )
        entries = cron_tool._load(path)
        entries.append(entry)
        cron_tool._save(path, entries)
    return entry


def list_tasks() -> list[dict[str, Any]]:
    return cron_tool._load(cron_tool._resolve_path())


def get_task(task_id: str) -> dict[str, Any] | None:
    return next((row for row in list_tasks() if row.get("id") == task_id), None)


def update_task(
    task_id: str,
    patch: dict[str, Any],
    *,
    authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allowed = {
        "title", "enabled", "notes", "cron", "run_at", "prompt", "command",
        "reminder", "wake", "memory_refs", "cwd", "watch",
    }
    if not isinstance(patch, dict) or not set(patch).issubset(allowed):
        raise ValueError("unsupported scheduler update field")
    path = cron_tool._resolve_path()
    with cron_tool._store_lock(path):
        entries = cron_tool._load(path)
        for index, current in enumerate(entries):
            if current.get("id") != task_id:
                continue
            updated = {**current, **patch, "updated_at": int(time.time())}
            title = str(updated.get("title") or "").strip()
            if not title or len(title) > 200:
                raise ValueError(
                    "title is required and must be at most 200 characters"
                )
            task_type = updated.get("type") or "recurring"
            watch = _validate_watch(task_type, updated.get("watch"))
            if watch:
                updated["watch"] = watch
            else:
                updated.pop("watch", None)
            enabled = bool(updated.get("enabled", True))
            if not enabled and task_type == "once" and not updated.get("run_at"):
                pass
            else:
                cron_value, run_at_value = _validate_schedule(
                    task_type, updated.get("cron"), updated.get("run_at")
                )
                if cron_value:
                    updated["cron"] = cron_value
                    updated.pop("run_at", None)
                if run_at_value:
                    updated["run_at"] = run_at_value
                    updated.pop("cron", None)
            bodies = [kind for kind in cron_tool.EXECUTION_KINDS if kind in patch]
            if len(bodies) > 1:
                raise ValueError("update one of prompt, command, or reminder")
            for kind in bodies:
                body = str(patch.get(kind) or "").strip()
                if not body:
                    raise ValueError(f"{kind} must not be empty")
                if task_type == "monitor" and kind != "prompt":
                    raise ValueError("monitor tasks require a prompt")
                for other in cron_tool.EXECUTION_KINDS:
                    updated.pop(other, None)
                updated[kind] = body
                if kind != "prompt":
                    updated["memory_refs"] = []
            if "reminder" in updated:
                updated["wake"] = bool(updated.get("wake"))
            else:
                if patch.get("wake"):
                    raise ValueError("wake applies only to reminders")
                updated.pop("wake", None)
            refs = normalize_memory_refs(updated.get("memory_refs"))
            resolve_memory_refs(refs)
            if refs and "prompt" not in updated:
                raise ValueError("memory_refs are supported only for prompt tasks")
            updated["memory_refs"] = refs
            execution = current.get("execution") or {}
            cwd = str(patch.get("cwd") or execution.get("cwd") or "")
            if not os.path.isabs(cwd) or not os.path.isdir(cwd):
                raise ValueError("cwd must be an existing absolute directory")
            if authority is None:
                from openprogram.runtime.permissions.authority import local_owner_authority

                authority = local_owner_authority()
            body_kind = next(
                kind for kind in cron_tool.EXECUTION_KINDS if kind in updated
            )
            origin = None
            if execution.get("session_id") is not None:
                # An edit made outside the conversation keeps the task's home.
                origin = (
                    str(execution["session_id"]),
                    str(execution.get("agent_id") or "main"),
                )
            updated["execution"] = cron_tool._build_execution_spec(
                kind=body_kind,
                body=updated[body_kind],
                cwd=cwd,
                entry_id=task_id,
                authority=authority,
                memory_refs=refs,
                wake=bool(updated.get("wake")),
                origin=origin,
                watch=watch,
            )
            updated.pop("cwd", None)
            entries[index] = updated
            cron_tool._save(path, entries)
            break
        else:
            raise KeyError(task_id)
    if updated.get("type") == "once" and _run_minute(updated.get("run_at")) != _run_minute(current.get("run_at")):
        from openprogram.programs.tools.jobs.cron import worker

        worker.forget_firing(task_id)
    return updated


def delete_task(task_id: str) -> bool:
    path = cron_tool._resolve_path()
    with cron_tool._store_lock(path):
        entries = cron_tool._load(path)
        kept = [row for row in entries if row.get("id") != task_id]
        if len(kept) == len(entries):
            return False
        cron_tool._save(path, kept)
        return True


__all__ = [
    "TASK_TYPES", "create_task", "list_tasks", "get_task", "update_task",
    "delete_task",
]
