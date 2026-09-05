"""Bounded diff primitives shared by scope and diff implementations."""
from __future__ import annotations

import difflib
import hashlib
import os
import stat
from pathlib import Path

from .turn_files_shared import _MAX_DIFF_BYTES, _valid_turn_id


_DIR_FD_CAPABLE = all(fn in os.supports_dir_fd for fn in (os.open, os.stat))


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _validate_directory_path(path: Path) -> None:
    """Reject symlink/junction traversal without relying on ``dir_fd``."""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.parts[0])
    for component in (None, *absolute.parts[1:]):
        if component is not None:
            current = current / component
        info = os.lstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse_point(info)
        ):
            raise OSError("unsafe recovery directory")


def _same_state(first: dict, second: dict) -> bool:
    if first.get("kind") != second.get("kind"):
        return False
    if first.get("kind") == "regular":
        return first.get("digest") == second.get("digest")
    return first.get("kind") == "absent"


def _open_directory_no_symlinks(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise OSError("recovery directory must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parts = absolute.parts
    descriptor = os.open(parts[0], flags)
    try:
        for component in parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise OSError("unsafe recovery directory")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _state_bytes(session_dir: Path, turn_id: str, state: dict) -> tuple[bytes, str]:
    if state.get("kind") == "absent":
        return b"", "available"
    if state.get("kind") != "regular" or not state.get("blob_ref"):
        raise OSError("recorded state is unavailable")
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir

    if not _valid_turn_id(turn_id):
        raise OSError("unsafe turn id")

    blob_ref = str(state["blob_ref"])
    if (
        not blob_ref
        or Path(blob_ref).is_absolute()
        or Path(blob_ref).name != blob_ref
        or "/" in blob_ref
        or "\\" in blob_ref
    ):
        raise OSError("unsafe recovery blob reference")
    directory = turn_backup_dir(session_dir, turn_id)
    if not _DIR_FD_CAPABLE:
        _validate_directory_path(directory)
        blob = directory / blob_ref
        info = os.lstat(blob)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse_point(info)
            or info.st_nlink != 1
        ):
            raise OSError("unsafe recovery blob")
        if state.get("size") is not None and info.st_size != state.get("size"):
            raise OSError("recovery blob size mismatch")
        if info.st_size > _MAX_DIFF_BYTES:
            return b"", "large"
        raw = blob.read_bytes()
        if f"sha256:{hashlib.sha256(raw).hexdigest()}" != state.get("digest"):
            raise OSError("recovery blob digest mismatch")
        if b"\0" in raw:
            return b"", "binary"
        return raw, "available"
    directory_fd = _open_directory_no_symlinks(directory)
    try:
        descriptor = os.open(
            blob_ref,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe recovery blob")
            if state.get("size") is not None and info.st_size != state.get("size"):
                raise OSError("recovery blob size mismatch")
            if info.st_size > _MAX_DIFF_BYTES:
                return b"", "large"
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_DIFF_BYTES:
                    return b"", "large"
            raw = b"".join(chunks)
            if f"sha256:{digest.hexdigest()}" != state.get("digest"):
                raise OSError("recovery blob digest mismatch")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)
    if b"\0" in raw:
        return b"", "binary"
    return raw, "available"


def _net_stats(
    session_dir: Path,
    before_turn: str,
    before: dict,
    after_turn: str,
    after: dict,
    budget: list[int],
) -> tuple[int | None, int | None, bool, str]:
    try:
        before_raw, before_state = _state_bytes(session_dir, before_turn, before)
        after_raw, after_state = _state_bytes(session_dir, after_turn, after)
    except OSError:
        return None, None, False, "unavailable"
    state = before_state if before_state != "available" else after_state
    if state != "available":
        return None, None, state == "binary", state
    cost = len(before_raw) + len(after_raw)
    if cost > budget[0]:
        return None, None, False, "timeout"
    budget[0] -= cost
    before_lines = before_raw.decode("utf-8", errors="replace").splitlines()
    after_lines = after_raw.decode("utf-8", errors="replace").splitlines()
    if len(before_lines) + len(after_lines) > 5_000:
        return None, None, False, "timeout"
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=before_lines, b=after_lines, autojunk=True,
    ).get_opcodes():
        if tag in {"insert", "replace"}:
            added += j2 - j1
        if tag in {"delete", "replace"}:
            removed += i2 - i1
    return added, removed, False, "available"


__all__ = ["_net_stats", "_open_directory_no_symlinks", "_same_state", "_state_bytes"]
