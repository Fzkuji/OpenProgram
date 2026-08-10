"""Read-only commit-message generation from the current Git diff.

Also hosts the pure string helpers the `commit-push-pr` skill uses to build
exact commit trailers, PR bodies, and the `gh pr create` argv. They are pure
so they can be unit-tested and called from a one-line `python3 -c`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


_MAX_STATUS_CHARS = 20_000
_MAX_CHANGE_CONTEXT_CHARS = 80_000

#: Identity written when the acting model is unknown or unmapped.
CO_AUTHOR_NAME = "OpenProgram"
CO_AUTHOR_EMAIL = "noreply@openprogram.dev"
#: Fixed last line of a generated pull-request body.
PR_FOOTER = "Generated with OpenProgram"

# A trailer line: "Token: value" / "Token-Name: value". Git's own rule, minus
# the "---" separator handling we do not need here.
_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s|^[A-Za-z-]+ #")


def co_author_trailer(model: str | None = None, *, enabled: bool | None = None) -> str | None:
    """`Co-Authored-By: <name> <email>` for the acting model, or None when off.

    ``enabled`` defaults to the ``git.co_author`` config toggle. ``model`` is
    the display name of the model doing the work; unknown/empty falls back to
    the generic OpenProgram identity.
    """
    if enabled is None:
        enabled = _co_author_enabled()
    if not enabled:
        return None
    name = (model or "").strip() or CO_AUTHOR_NAME
    return f"Co-Authored-By: {name} <{CO_AUTHOR_EMAIL}>"


def _co_author_enabled() -> bool:
    try:
        from openprogram import setup as _setup

        return bool((_setup._read_config().get("git", {}) or {}).get("co_author", True))
    except Exception:
        return True


def apply_trailers(message: str, *, co_author: str | None) -> str:
    """Append ``co_author`` to ``message`` as a git trailer. Idempotent.

    A blank line is inserted before the trailer when the message does not
    already end in a trailer block; when it does (e.g. ``Signed-off-by:``)
    the new trailer joins that block directly.
    """
    body = message.rstrip()
    if not co_author:
        return body
    lines = body.splitlines()
    if any(line.strip() == co_author for line in lines):
        return body
    if not lines:
        return co_author
    separator = "\n" if _TRAILER_RE.match(lines[-1]) else "\n\n"
    return body + separator + co_author


def pr_body(
    summary: str,
    *,
    changes: list[str] | None = None,
    testing: list[str] | None = None,
) -> str:
    """Assemble a pull-request body ending in exactly one PR_FOOTER line."""
    parts = [f"## Summary\n\n{summary.strip()}"]
    if changes:
        parts.append("## What changed\n\n" + "\n".join(f"- {c}" for c in changes))
    if testing:
        parts.append("## Testing\n\n" + "\n".join(f"- {t}" for t in testing))
    body = "\n\n".join(parts)
    return append_pr_footer(body)


def append_pr_footer(body: str) -> str:
    """Append PR_FOOTER unless the body already ends with it. Idempotent."""
    text = body.rstrip()
    if text.endswith(PR_FOOTER):
        return text
    return f"{text}\n\n{PR_FOOTER}"


def gh_pr_create_argv(
    *, base: str, head: str, title: str, body_file: str, draft: bool = False,
) -> list[str]:
    """The exact `gh pr create` argv the commit-push-pr skill documents."""
    argv = [
        "gh", "pr", "create",
        "--base", base,
        "--head", head,
        "--title", title,
        "--body-file", body_file,
    ]
    if draft:
        argv.append("--draft")
    return argv


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
