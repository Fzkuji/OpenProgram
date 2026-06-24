"""Agent tools for checkpoint (file snapshot) management."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from openprogram.functions._runtime import function


def _get_session_dir() -> Optional[Path]:
    try:
        from openprogram.store import _store
        shim = _store.get()
        if shim is None:
            return None
        return shim.store._session_dir(shim.session_id)
    except Exception:
        return None


@function(
    name="checkpoint_list",
    description=(
        "List available file checkpoints for the current session. "
        "Each checkpoint corresponds to a turn where files were "
        "modified. Shows turn_id, modification time, and backed-up "
        "file paths.\n\n"
        "Args:\n"
        "  limit: maximum number of checkpoints to return (default 20, "
        "newest first)."
    ),
    toolset=["core"],
)
def checkpoint_list(limit: int = 20) -> str:
    session_dir = _get_session_dir()
    if not session_dir:
        return "[checkpoint_list error] no active session"

    from openprogram.store.snapshot.checkpoint.paths import session_backup_root
    from openprogram.store.snapshot.checkpoint.store import CheckpointStore

    root = session_backup_root(session_dir)
    if not root.exists():
        return "[checkpoint_list] no checkpoints"

    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return "[checkpoint_list] no checkpoints"

    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    dirs = dirs[:limit]

    store = CheckpointStore(session_dir)
    lines = []
    for d in dirs:
        turn_id = d.name
        try:
            mtime = d.stat().st_mtime
            from datetime import datetime
            ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = "?"
        paths = store.list_backed_paths(turn_id)
        files_str = ", ".join(Path(p).name for p in paths[:5])
        if len(paths) > 5:
            files_str += f" (+{len(paths) - 5} more)"
        lines.append(f"  {turn_id}  {ts}  [{len(paths)} files: {files_str}]")

    return "[checkpoint_list] " + str(len(lines)) + " checkpoints:\n" + "\n".join(lines)


@function(
    name="checkpoint_restore",
    description=(
        "Restore files from a checkpoint to their pre-edit state. "
        "Reverses all file changes that happened during the specified "
        "turn.\n\n"
        "Args:\n"
        "  turn_id: the turn identifier from checkpoint_list."
    ),
    toolset=["core"],
    requires_approval=True,
)
def checkpoint_restore(turn_id: str) -> str:
    if not turn_id or not isinstance(turn_id, str):
        return "[checkpoint_restore error] turn_id required"

    session_dir = _get_session_dir()
    if not session_dir:
        return "[checkpoint_restore error] no active session"

    from openprogram.store.snapshot.checkpoint.store import CheckpointStore
    store = CheckpointStore(session_dir)
    restored = store.restore_turn(turn_id.strip())
    if not restored:
        return f"[checkpoint_restore] no files to restore for turn {turn_id}"
    return (
        f"[checkpoint_restore] restored {len(restored)} file(s):\n"
        + "\n".join(f"  {p}" for p in restored)
    )
