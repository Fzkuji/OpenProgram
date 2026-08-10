"""Read-only commit-message generation from the current Git diff."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


_MAX_STATUS_CHARS = 20_000
_MAX_CHANGE_CONTEXT_CHARS = 80_000


def _truncate(text: str, limit: int, label: str) -> str:
    marker = f"\n[{label} truncated]"
    if len(text) <= limit:
        return text
    return text[: limit - len(marker)] + marker


def _working_directory(session_ctx: dict[str, Any]) -> Path:
    session_id = str((session_ctx or {}).get("session_id") or "").strip()
    if session_id:
        try:
            from openprogram.worktree.manager import get_manager

            active = get_manager().find_active_for_session(session_id)
            if active is not None:
                return Path(active.worktree_path)
        except Exception:
            pass
        try:
            from openprogram.store.project import project_for_session

            project = project_for_session(session_id)
            if project is not None and project.path:
                path = Path(project.path).expanduser()
                if path.is_dir():
                    return path
        except Exception:
            pass

    explicit = str((session_ctx or {}).get("cwd") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "git command failed")
    return result.stdout


def commit_message_builtin_handler(
    session_ctx: dict[str, Any], raw_args: str,
) -> dict[str, str]:
    """Generate one candidate without exposing mutation tools to the model."""
    del raw_args
    cwd = _working_directory(session_ctx)
    try:
        status = _git(cwd, "status", "--short", "--untracked-files=all")
        if not status.strip():
            return {"text": "No Git changes to describe."}

        diff = _git(cwd, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--")
        scope = "staged changes"
        if not diff.strip():
            diff = _git(cwd, "diff", "--no-ext-diff", "--no-textconv", "--")
            scope = "unstaged changes"
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"text": f"Commit message generation failed: {exc}"}

    from openprogram.providers.default_llm import build_default_llm

    llm = build_default_llm()
    if llm is None:
        return {"text": "Commit message generation unavailable: no default model is configured."}

    status = _truncate(status, _MAX_STATUS_CHARS, "status")
    system = (
        "Generate one Git commit message from the supplied repository status and diff. "
        "Do not modify files, the Git index, commits, branches, remotes, or configuration. "
        "Return only an imperative subject of at most 72 characters, followed by a body "
        "only when needed. Do not use code fences or commentary."
    )
    user = f"Scope: {scope}\n\nStatus:\n{status}\nDiff:\n{diff or '[no textual diff]'}"
    user = _truncate(user, _MAX_CHANGE_CONTEXT_CHARS, "change context")
    try:
        candidate = str(llm(system, user) or "").strip()
    except Exception as exc:  # noqa: BLE001 - return a command result, do not crash the host
        return {"text": f"Commit message generation failed: {type(exc).__name__}: {exc}"}
    return {"text": candidate or "Commit message generation returned an empty response."}
