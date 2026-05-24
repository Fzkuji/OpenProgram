"""edit function — string-replace inside an existing file."""

from __future__ import annotations

import os

from openprogram.functions._runtime import function
from openprogram.store.file_backup.helpers import backup_for_current_turn


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
    unsafe_in=["wechat", "telegram"],
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
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if old_string == new_string:
        return "Error: old_string and new_string are identical — nothing to change"

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
    # Snapshot pre-edit state for turn-scoped revert.
    backup_for_current_turn(file_path)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception as e:
        return f"Error writing {file_path}: {type(e).__name__}: {e}"

    replaced = count if replace_all else 1
    return f"Edited {file_path} ({replaced} replacement{'s' if replaced != 1 else ''})"
