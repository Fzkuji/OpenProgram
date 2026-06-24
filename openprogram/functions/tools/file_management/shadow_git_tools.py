"""Agent tools for shadow git (independent file history) management."""
from __future__ import annotations

import os
from typing import Optional

from openprogram.functions._runtime import function


def _resolve_project_path() -> Optional[str]:
    try:
        from openprogram.paths import get_default_workdir
        wd = get_default_workdir()
        if wd:
            return os.path.abspath(os.path.expanduser(wd))
    except Exception:
        pass
    try:
        import subprocess
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def _get_store():
    project = _resolve_project_path()
    if not project:
        return None
    from openprogram.store.shadow_git.store import ShadowGitStore
    return ShadowGitStore(project)


@function(
    name="shadow_git_log",
    description=(
        "Show the shadow git commit history — a permanent record of "
        "all file changes the agent has made, independent of the "
        "user's git. Each entry shows commit sha, timestamp, message, "
        "and changed files.\n\n"
        "Args:\n"
        "  n: number of recent commits to show (default 10)."
    ),
    toolset=["core"],
)
def shadow_git_log(n: int = 10) -> str:
    store = _get_store()
    if not store:
        return "[shadow_git_log error] cannot resolve project path"

    entries = store.log(n=n)
    if not entries:
        return "[shadow_git_log] no history"

    lines = []
    for e in entries:
        sha = e.get("sha", "?")[:8]
        ts = e.get("date", "?")
        msg = e.get("message", "").strip().split("\n")[0][:80]
        files = e.get("files", [])
        files_str = ", ".join(files[:3])
        if len(files) > 3:
            files_str += f" (+{len(files) - 3})"
        lines.append(f"  {sha}  {ts}  {msg}  [{files_str}]")

    return f"[shadow_git_log] {len(entries)} commits:\n" + "\n".join(lines)


@function(
    name="shadow_git_diff",
    description=(
        "Show the diff between two shadow git commits. Useful for "
        "reviewing what changed between two points in the agent's "
        "file modification history.\n\n"
        "Args:\n"
        "  sha1: first commit sha (older).\n"
        "  sha2: second commit sha (newer, default HEAD)."
    ),
    toolset=["core"],
)
def shadow_git_diff(sha1: str, sha2: str = "HEAD") -> str:
    if not sha1:
        return "[shadow_git_diff error] sha1 required"

    store = _get_store()
    if not store:
        return "[shadow_git_diff error] cannot resolve project path"

    try:
        result = store.diff(sha1.strip(), sha2.strip() or "HEAD")
        if not result.strip():
            return f"[shadow_git_diff] no differences between {sha1[:8]} and {sha2[:8]}"
        return f"[shadow_git_diff] {sha1[:8]}..{sha2[:8]}:\n{result}"
    except Exception as e:
        return f"[shadow_git_diff error] {type(e).__name__}: {e}"


@function(
    name="shadow_git_restore_file",
    description=(
        "Restore a single file from a specific shadow git commit. "
        "Writes the file content from the specified commit back to "
        "its original location on disk.\n\n"
        "Args:\n"
        "  sha: commit sha to restore from.\n"
        "  file_path: relative path of the file within the project.\n"
        "  dest: absolute destination path (defaults to the file's "
        "original location in the project)."
    ),
    toolset=["core"],
    requires_approval=True,
)
def shadow_git_restore_file(sha: str, file_path: str, dest: str = "") -> str:
    if not sha or not file_path:
        return "[shadow_git_restore_file error] sha and file_path required"

    store = _get_store()
    if not store:
        return "[shadow_git_restore_file error] cannot resolve project path"

    if not dest:
        dest = str(store.project_path / file_path)

    try:
        ok = store.restore_file(sha.strip(), file_path.strip(), dest)
        if ok:
            return f"[shadow_git_restore_file] restored {file_path} from {sha[:8]} → {dest}"
        return f"[shadow_git_restore_file error] file {file_path} not found in commit {sha[:8]}"
    except Exception as e:
        return f"[shadow_git_restore_file error] {type(e).__name__}: {e}"
