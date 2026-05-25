"""write function — create a new file or overwrite an existing one."""

from __future__ import annotations

import os

from openprogram.functions._runtime import function
from openprogram.store.file_backup.helpers import backup_for_current_turn


_DESCRIPTION = (
    "Write the given content to a file on disk, creating it (and any missing "
    "parent directories) if it doesn't exist, or overwriting it if it does.\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- Prefer the `edit` tool for modifying existing files — it sends only the diff "
    "and is safer for concurrent edits. Use `write` for new files or full rewrites."
)


@function(
    name="write",
    description=_DESCRIPTION,
    toolset=["core"],
    unsafe_in=["wechat", "telegram", "plan"],
)
def write(file_path: str, content: str) -> str:
    """Write `content` to `file_path`, creating parents if needed.

    Args:
        file_path: Absolute path of the file to write.
        content: Full file contents to write.
    """
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    parent = os.path.dirname(file_path)
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            return f"Error creating directory {parent}: {e}"
    # Back up pre-edit state for turn-scoped revert. Safe to call
    # even when the file doesn't exist yet (records pre_existing=False
    # so restore_turn knows to delete-on-restore).
    backup_for_current_turn(file_path)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return f"Error writing {file_path}: {type(e).__name__}: {e}"
    return f"Wrote {len(content)} bytes to {file_path}"
