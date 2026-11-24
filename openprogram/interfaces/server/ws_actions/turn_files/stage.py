"""Stage and unstage from the Review pane's Unstaged and Staged scopes.

A hunk goes through ``git apply --cached`` (``--reverse`` to unstage):
Git checks the hunk's context against the index, so a hunk the index no
longer matches is a conflict and nothing changes. A whole file goes
through ``git add`` or ``git restore --staged``. The caller has already
checked that the file is in the scope snapshot the client is showing.
"""
from __future__ import annotations

from pathlib import Path

_MAX_HUNK_LINES = 5000
_PREFIXES = (" ", "+", "-")


def _valid_lines(lines) -> bool:
    return (
        isinstance(lines, list)
        and 0 < len(lines) <= _MAX_HUNK_LINES
        and all(
            isinstance(line, str) and line[:1] in _PREFIXES
            and "\n" not in line and "\r" not in line
            for line in lines
        )
        and any(line[0] != " " for line in lines)
    )


def hunk_patch(rel: str, hunk: dict) -> bytes | None:
    """A one-hunk patch for ``rel``, or None when the hunk is malformed.

    ``hunk`` carries ``old_start`` / ``new_start`` (1-based, as in the
    ``@@`` header) and ``lines``: the hunk body, each line with its
    `` ``/``+``/``-`` prefix.
    """
    lines = hunk.get("lines")
    old_start, new_start = hunk.get("old_start"), hunk.get("new_start")
    if (not _valid_lines(lines)
            or not isinstance(old_start, int) or isinstance(old_start, bool) or old_start < 0
            or not isinstance(new_start, int) or isinstance(new_start, bool) or new_start < 0):
        return None
    old_count = sum(1 for line in lines if line[0] != "+")
    new_count = sum(1 for line in lines if line[0] != "-")
    name = rel.replace("\\", "/")
    header = (
        f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
        f"@@ -{old_start},{old_count} +{new_start},{new_count} @@\n"
    )
    return (header + "".join(line + "\n" for line in lines)).encode("utf-8")


def stage_change(root: Path, segment: str, member: dict, hunk: dict | None) -> dict:
    """Stage (``segment`` "unstaged") or unstage (``segment`` "staged")
    one hunk, or the whole file when ``hunk`` is None."""
    from .diff import _git_output

    rel = str(member.get("rel") or "")
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return {"status": "error", "error_code": "INVALID_REQUEST", "error": "unsafe path"}
    if hunk is not None:
        if member.get("op") in {"add", "delete"} or member.get("binary"):
            return {"status": "error", "error_code": "INVALID_REQUEST",
                    "error": "stage the whole file instead"}
        patch = hunk_patch(rel, hunk)
        if patch is None:
            return {"status": "error", "error_code": "INVALID_REQUEST", "error": "invalid hunk"}
        args = ["apply", "--cached", "--whitespace=nowarn"]
        if segment == "staged":
            args.append("--reverse")
        try:
            _git_output(root, *args, "-", input_data=patch)
        except OSError as exc:
            return {"status": "conflict", "error_code": "CONFLICT", "error": str(exc)}
        return {"status": "ready"}
    paths = [rel]
    old_rel = member.get("old_rel")
    if isinstance(old_rel, str) and old_rel and not Path(old_rel).is_absolute() \
            and ".." not in Path(old_rel).parts:
        paths.append(old_rel)
    try:
        if segment == "unstaged":
            _git_output(root, "add", "--all", "--", *paths)
        else:
            _git_output(root, "restore", "--staged", "--", *paths)
    except OSError as exc:
        return {"status": "error", "error_code": "UNAVAILABLE", "error": str(exc)}
    return {"status": "ready"}
