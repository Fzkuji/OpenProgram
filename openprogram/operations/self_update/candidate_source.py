"""A candidate's source: the registered Git worktree and commit an update builds from.

The ``self_update`` tool checks these when the owner asks for an update, and
the update's supervisor, the repair loop and the candidate's checks check them
again before they build or run anything, so the checks live here, with
self-update, and the tool uses them.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from openprogram.runtime.permissions import refusal
from openprogram.runtime.permissions.authority import owner_principal_id
from openprogram.state.authority import normalize_authority


_GIT_TIMEOUT_SECONDS = 10
_GIT_CONFIG_OVERRIDES = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.pager=cat",
)


class SelfUpdateToolError(RuntimeError):
    """A stable validation error safe to return through the tool runtime."""


def require_local_owner(req: Any) -> dict[str, str]:
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


def git(cwd: Path, *args: str) -> str:
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


def recorded_path(value: str, name: str) -> Path:
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


def validate_registered_worktree(
    source: Path, candidate: Path, candidate_sha: str, branch_name: str
) -> None:
    entries = git(source, "worktree", "list", "--porcelain", "-z").split("\0\0")
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


def validate_candidate_snapshot(candidate: Path, candidate_sha: str) -> None:
    if git(candidate, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SelfUpdateToolError("candidate worktree is dirty")
    if git(candidate, "rev-parse", "HEAD") != candidate_sha:
        raise SelfUpdateToolError("candidate_sha is not the worktree HEAD")


__all__ = [
    "SelfUpdateToolError",
    "git",
    "recorded_path",
    "require_local_owner",
    "validate_candidate_snapshot",
    "validate_registered_worktree",
]
