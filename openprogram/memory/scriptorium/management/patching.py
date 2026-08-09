"""Restricted unified-diff application for the memory write transaction.

Text create/update/delete under ``topics/**`` and ``core.md`` only. Renames,
copies, mode changes, symlinks and binary hunks are rejected rather than
approximated, so a patch can never move data outside the writable surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .transaction import TransactionError, validate_writable_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_REJECTED_HEADERS = (
    ("rename from", "patch renames are not supported"),
    ("rename to", "patch renames are not supported"),
    ("copy from", "patch copies are not supported"),
    ("copy to", "patch copies are not supported"),
    ("old mode", "patch mode changes are not supported"),
    ("new mode", "patch mode changes are not supported"),
    ("new file mode 120000", "symlink creation is not supported"),
    ("deleted file mode 120000", "symlink deletion is not supported"),
    ("GIT binary patch", "binary patches are not supported"),
    ("Binary files", "binary patches are not supported"),
)


@dataclass
class FilePatch:
    path: str
    hunks: list[tuple[int, list[str]]]
    creates: bool = False
    deletes: bool = False


def apply_patch(stage_dir: Path, patch: str) -> list[str]:
    """Apply ``patch`` inside ``stage_dir`` and return changed paths."""
    if not patch.strip():
        raise TransactionError("INVALID_ARGUMENT", "patch is empty")
    files = _parse(patch)
    if not files:
        raise TransactionError(
            "INVALID_ARGUMENT", "patch contains no file sections"
        )
    changed: list[str] = []
    for entry in files:
        validate_writable_path(entry.path)
        target = stage_dir / entry.path
        if entry.deletes:
            if not target.is_file():
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch deletes a file that does not exist",
                    path=entry.path,
                )
            target.unlink()
            changed.append(entry.path)
            continue
        original = (
            target.read_text(encoding="utf-8").splitlines(keepends=True)
            if target.is_file()
            else []
        )
        if entry.creates and original:
            raise TransactionError(
                "PATCH_CONFLICT",
                "patch creates a file that already exists",
                path=entry.path,
            )
        if not entry.creates and not original:
            raise TransactionError(
                "PATCH_CONFLICT",
                "patch modifies a file that does not exist",
                path=entry.path,
            )
        updated = _apply_hunks(entry, original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(updated), encoding="utf-8")
        changed.append(entry.path)
    return sorted(set(changed))


def _parse(patch: str) -> list[FilePatch]:
    lines = patch.splitlines()
    files: list[FilePatch] = []
    current: FilePatch | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        for marker, message in _REJECTED_HEADERS:
            if line.startswith(marker):
                raise TransactionError("INVALID_ARGUMENT", message)
        if line.startswith("--- "):
            old = _strip_prefix(line[4:])
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise TransactionError(
                    "INVALID_ARGUMENT", "malformed patch: --- without +++"
                )
            new = _strip_prefix(lines[index + 1][4:])
            path = new if new != "/dev/null" else old
            if path == "/dev/null":
                raise TransactionError(
                    "INVALID_ARGUMENT", "patch section has no file path"
                )
            current = FilePatch(
                path=path,
                hunks=[],
                creates=old == "/dev/null",
                deletes=new == "/dev/null",
            )
            files.append(current)
            index += 2
            continue
        match = _HUNK.match(line)
        if match:
            if current is None:
                raise TransactionError(
                    "INVALID_ARGUMENT", "patch hunk outside a file section"
                )
            start = int(match.group(1))
            body: list[str] = []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if candidate.startswith(("--- ", "diff --git")) or _HUNK.match(
                    candidate
                ):
                    break
                if candidate.startswith(("+", "-", " ", "\\")):
                    body.append(candidate)
                    index += 1
                    continue
                if not candidate:
                    # A context line whose content is empty loses its leading
                    # space in many editors; treat it as blank context.
                    body.append(" ")
                    index += 1
                    continue
                break
            current.hunks.append((start, body))
            continue
        index += 1
    return files


def _strip_prefix(value: str) -> str:
    path = value.split("\t")[0].strip()
    if path in ("/dev/null", ""):
        return "/dev/null"
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _apply_hunks(entry: FilePatch, original: list[str]) -> list[str]:
    if not entry.hunks:
        raise TransactionError(
            "INVALID_ARGUMENT",
            "patch file section has no hunks",
            path=entry.path,
        )
    result: list[str] = []
    cursor = 0
    for start, body in sorted(entry.hunks, key=lambda item: item[0]):
        begin = max(start - 1, 0)
        if begin < cursor:
            raise TransactionError(
                "PATCH_CONFLICT",
                "overlapping patch hunks",
                path=entry.path,
            )
        result.extend(original[cursor:begin])
        cursor = begin
        for raw in body:
            if raw.startswith("\\"):
                continue
            marker, text = raw[0], raw[1:]
            if marker == "+":
                result.append(text + "\n")
                continue
            if cursor >= len(original):
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch context runs past end of file",
                    path=entry.path,
                    details={"line": cursor + 1},
                )
            existing = original[cursor]
            if existing.rstrip("\n") != text.rstrip("\n"):
                raise TransactionError(
                    "PATCH_CONFLICT",
                    "patch context does not match file contents",
                    path=entry.path,
                    details={
                        "line": cursor + 1,
                        "expected": text,
                        "found": existing.rstrip("\n"),
                    },
                )
            if marker == " ":
                result.append(existing)
            cursor += 1
    result.extend(original[cursor:])
    return result
