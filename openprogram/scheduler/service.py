"""Scheduler CRUD shared by the Agent tool, REST API, and worker."""

from __future__ import annotations

from datetime import datetime
import os
import time
from typing import Any

from openprogram.memory.references import (
    normalize as normalize_memory_refs,
    resolve as resolve_memory_refs,
)
from openprogram.programs.functions.vanilla.cron import cron as cron_tool


TASK_TYPES = frozenset({"once", "recurring", "monitor"})
_EXTRA_FIELDS = frozenset({"legacy_commitment_id", "legacy_source"})


def _parse_run_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("run_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("run_at must include a timezone")
    return parsed.isoformat()


def _validate_schedule(task_type: str, cron: str | None, run_at: str | None) -> tuple[str | None, str | None]:
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


def create_task(
    *,
    title: str,
    task_type: str,
    prompt: str | None = None,
    command: str | None = None,
    cron: str | None = None,
    run_at: str | None = None,
    enabled: bool = True,
    memory_refs: list[dict[str, str]] | None = None,
    notes: str = "",
    cwd: str | None = None,
    authority: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_type = str(task_type or "").strip().lower()
    if task_type not in TASK_TYPES:
        raise ValueError("task_type must be once, recurring, or monitor")
    title = str(title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("title is required and must be at most 200 characters")
    prompt = str(prompt or "").strip() or None
    command = str(command or "").strip() or None
    if bool(prompt) == bool(command):
        raise ValueError("pass exactly one of prompt or command")
    if task_type == "monitor" and command:
        raise ValueError("monitor tasks require a prompt")
    if not enabled and task_type == "once" and not run_at:
        cron_value, run_at_value = None, None
    else:
        cron_value, run_at_value = _validate_schedule(task_type, cron, run_at)
    refs = normalize_memory_refs(memory_refs)
    resolve_memory_refs(refs)
    if refs and not prompt:
        raise ValueError("memory_refs are supported only for prompt tasks")
    if cwd is None:
        from openprogram.worktree.context import current_worktree_path

        cwd = current_worktree_path() or os.getcwd()
    if not os.path.isabs(cwd) or not os.path.isdir(cwd):
        raise ValueError("cwd must be an existing absolute directory")
    if authority is None:
        from openprogram.agent.authority import local_owner_authority

        authority = local_owner_authority()
    path = cron_tool._resolve_path()
    with cron_tool._store_lock(path):
        entry_id = cron_tool._mint_id()
        body_kind, body = ("prompt", prompt) if prompt else ("command", command)
        assert body is not None
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
        "memory_refs", "cwd",
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
            if "prompt" in patch and "command" in patch:
                raise ValueError("update prompt or command, not both")
            if "prompt" in patch:
                prompt = str(patch.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("prompt must not be empty")
                updated["prompt"] = prompt
                updated.pop("command", None)
            if "command" in patch:
                command = str(patch.get("command") or "").strip()
                if not command:
                    raise ValueError("command must not be empty")
                if task_type == "monitor":
                    raise ValueError("monitor tasks require a prompt")
                updated["command"] = command
                updated.pop("prompt", None)
                updated["memory_refs"] = []
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
                from openprogram.agent.authority import local_owner_authority

                authority = local_owner_authority()
            body_kind = "prompt" if "prompt" in updated else "command"
            updated["execution"] = cron_tool._build_execution_spec(
                kind=body_kind,
                body=updated[body_kind],
                cwd=cwd,
                entry_id=task_id,
                authority=authority,
                memory_refs=refs,
            )
            updated.pop("cwd", None)
            entries[index] = updated
            cron_tool._save(path, entries)
            return updated
    raise KeyError(task_id)


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
