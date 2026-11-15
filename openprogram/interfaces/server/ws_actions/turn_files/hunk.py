"""Revert one hunk of a Review diff in the working file.

The client sends the hunk as the Review pane shows it: the old side
(context and removed lines) and the new side (context and added lines).
The server finds the new side in the file as it is now, nearest the line
the diff names, swaps in the old side and publishes the result through
the project's document history with the bytes it read as the baseline, so
a file that changes in between is a conflict rather than a lost edit.
A hunk whose new side is no longer in the file is a conflict too.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_MAX_HUNK_LINES = 5000
_MAX_FILE_BYTES = 5_000_000


def _strip_eol(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _eol(line: str) -> str:
    return line[len(_strip_eol(line)):]


def _valid_side(lines) -> bool:
    return (
        isinstance(lines, list)
        and len(lines) <= _MAX_HUNK_LINES
        and all(isinstance(line, str) and "\n" not in line and "\r" not in line for line in lines)
    )


def reverse_hunk(text: str, old: list[str], new: list[str], new_start: int) -> str | None:
    """``text`` with the hunk's new side put back to its old side.

    The new side is matched line by line (line endings aside) at the place
    nearest ``new_start`` (1-based, as in the ``@@`` header). Returns None
    when the new side is not in the text.
    """
    lines = text.splitlines(keepends=True)
    bare = [_strip_eol(line) for line in lines]
    width = len(new)
    # An empty new side sits after line ``new_start`` (unified diff rule).
    expected = new_start if width == 0 else new_start - 1
    expected = min(max(expected, 0), len(lines))
    found = None
    for distance in range(len(lines) + 1):
        for at in (expected - distance, expected + distance) if distance else (expected,):
            if 0 <= at <= len(lines) - width and bare[at:at + width] == new:
                found = at
                break
        if found is not None:
            break
    if found is None:
        return None
    block = lines[found:found + width]
    newline = next((_eol(line) for line in block if _eol(line)), None) \
        or next((_eol(line) for line in lines if _eol(line)), "\n")
    at_end = found + width == len(lines)
    # The file's last line keeps its missing final newline.
    final_eol = _eol(block[-1]) if block and at_end else newline
    restored = [
        line + (final_eol if index == len(old) - 1 else newline)
        for index, line in enumerate(old)
    ]
    return "".join(lines[:found] + restored + lines[found + width:])


def revert_review_hunk(session_id: str, path: str, hunk: dict, idempotency_key: str | None) -> dict:
    """Write the reverted file through the session project's document history."""
    old, new = hunk.get("old_lines"), hunk.get("new_lines")
    new_start = hunk.get("new_start")
    if not _valid_side(old) or not _valid_side(new) or not isinstance(new_start, int) or new_start < 0:
        return {"status": "error", "error_code": "INVALID_REQUEST", "error": "invalid hunk"}
    from openprogram.state.store.project.project_store import DEFAULT_PROJECT_ID, get_project, project_for_session
    project = project_for_session(session_id) or get_project(DEFAULT_PROJECT_ID)
    if project is None or not project.path:
        return {"status": "error", "error_code": "OUTSIDE_PROJECT", "error": "the session has no project folder"}
    root = Path(project.path).expanduser().resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root) or target == root:
        return {"status": "error", "error_code": "OUTSIDE_PROJECT", "error": "the file is outside the project folder"}
    try:
        if target.stat().st_size > _MAX_FILE_BYTES:
            return {"status": "error", "error_code": "TOO_LARGE", "error": "the file is too large to edit"}
        raw = target.read_bytes()
    except FileNotFoundError:
        return {"status": "conflict", "error_code": "CONFLICT", "error": "the file no longer exists"}
    except OSError as exc:
        return {"status": "error", "error_code": "UNAVAILABLE", "error": f"{type(exc).__name__}: {exc}"}
    if b"\0" in raw[:8192]:
        return {"status": "error", "error_code": "BINARY", "error": "binary files cannot be edited"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "error", "error_code": "BINARY", "error": "the file is not UTF-8 text"}
    reverted = reverse_hunk(text, old, new, new_start)
    if reverted is None:
        return {"status": "conflict", "error_code": "CONFLICT", "error": "the file no longer matches this change"}
    from ..files.mutations import _write_file
    result = _write_file(
        project.id, os.path.relpath(target, root), reverted, None,
        expected_revision=hashlib.sha256(raw).hexdigest(),
        idempotency_key=idempotency_key, editor_id="review-hunk",
    )
    if result.get("conflict"):
        return {"status": "conflict", "error_code": "CONFLICT", "error": "the file changed while reverting"}
    if result.get("ok"):
        return {"status": "ready", "revision": result.get("revision")}
    return {"status": "error", "error_code": result.get("error_code") or "UNAVAILABLE",
            "error": result.get("error") or "the file could not be written"}
