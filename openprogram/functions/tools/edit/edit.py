"""edit function — string-replace inside an existing file."""

from __future__ import annotations

import os

from openprogram.functions._runtime import function
from openprogram.store.file_backup.helpers import backup_for_current_turn
from openprogram.worktree.path_resolve import resolve_path


_DESCRIPTION = (
    "Replace an exact string in an existing file with a new string.\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- `old_string` must match the target text EXACTLY including whitespace and "
    "indentation. If it isn't unique in the file, either add more surrounding "
    "context to make it unique, or pass `replace_all=true`.\n"
    "- Use `write` instead when creating a new file or completely rewriting one."
)


@function(
    name="edit",
    description=_DESCRIPTION,
    toolset=["core"],
    unsafe_in=["wechat", "telegram", "plan"],
)
def edit(file_path: str,
         old_string: str,
         new_string: str,
         replace_all: bool = False) -> str:
    """Replace `old_string` with `new_string` inside `file_path`.

    Args:
        file_path: Absolute path of the file to edit.
        old_string: Exact text to find (must match existing content byte-for-byte).
        new_string: Replacement text (must differ from old_string).
        replace_all: Replace every occurrence of old_string. Default false.
    """
    # Worktree-aware resolution: if an agent worktree is bound to the
    # current context, treat a relative path as relative to the
    # worktree root; if the LLM passed an absolute path outside the
    # worktree, surface a soft warning but still proceed (D6 — warn,
    # don't block).
    resolved_path, outside_warning = resolve_path(file_path)
    file_path = resolved_path
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if old_string == new_string:
        return "Error: old_string and new_string are identical — nothing to change"

    # Read-before-edit freshness gate (Claude-Code-style): refuse to edit
    # a file the agent never read, or one that changed on disk since it
    # last saw it — so a concurrent user edit is never silently
    # overwritten. No-op outside a dispatcher turn.
    try:
        from openprogram.store import read_tracking as _rt
        _fresh = _rt.check_fresh(file_path)
        if _fresh in (_rt.NEVER_READ, _rt.STALE):
            return _rt.stale_message(file_path, _fresh)
    except Exception:
        pass

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return f"Error reading {file_path}: {type(e).__name__}: {e}"

    count = text.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {file_path}"
    if count > 1 and not replace_all:
        return (
            f"Error: old_string occurs {count} times in {file_path}. "
            "Add surrounding context to make it unique, or set replace_all=true."
        )

    new_text = (text.replace(old_string, new_string) if replace_all
                else text.replace(old_string, new_string, 1))
    # Back up pre-edit state for turn-scoped revert.
    backup_for_current_turn(file_path)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return f"Error writing {file_path}: {type(e).__name__}: {e}"

    # Refresh the baseline to what we just wrote, so the agent can edit
    # this file again without re-reading.
    try:
        from openprogram.store import read_tracking as _rt
        _rt.mark_seen(file_path)
    except Exception:
        pass

    replaced = count if replace_all else 1
    msg = f"Edited {file_path} ({replaced} replacement{'s' if replaced != 1 else ''})"
    if outside_warning:
        msg = f"{outside_warning}\n{msg}"
    return msg
