"""Capture immutable file versions and bounded content statistics."""
from __future__ import annotations

import difflib
import hashlib
import os
import stat
import uuid
from pathlib import Path
from openprogram._compat import is_link_metadata


_STATS_MAX_BYTES = 1024 * 1024


class MutationJournalError(RuntimeError):
    """A trusted mutation could not be recorded safely."""


def _has_nul(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def _line_stats(before: Path | None, after: Path | None) -> tuple[dict, str]:
    paths = [path for path in (before, after) if path is not None]
    if any(path.stat().st_size > _STATS_MAX_BYTES for path in paths):
        binary = any(_has_nul(path) for path in paths)
        return {"added": None, "removed": None, "binary": binary}, (
            "binary" if binary else "large"
        )
    raw_before = before.read_bytes() if before is not None else b""
    raw_after = after.read_bytes() if after is not None else b""
    if b"\0" in raw_before or b"\0" in raw_after:
        return {"added": None, "removed": None, "binary": True}, "binary"
    old = raw_before.decode("utf-8", errors="replace").splitlines()
    new = raw_after.decode("utf-8", errors="replace").splitlines()
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=old, b=new, autojunk=False,
    ).get_opcodes():
        if tag in {"insert", "replace"}:
            added += j2 - j1
        if tag in {"delete", "replace"}:
            removed += i2 - i1
    return {"added": added, "removed": removed, "binary": False}, "available"


def _capture_regular(source: Path, destination: Path) -> dict:
    """Publish a durable, immutable version before any manifest references it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}")
    try:
        observed = os.lstat(source)
        if (not stat.S_ISREG(observed.st_mode) or is_link_metadata(observed)
                or observed.st_nlink != 1):
            raise OSError("snapshot source must be an ordinary non-linked file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(source, flags), "rb") as handle:
            before = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (observed.st_dev, observed.st_ino):
                raise OSError("snapshot source changed before opening")
            digest = hashlib.sha256()
            size = 0
            flags_out = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(destination, flags_out, 0o600), "wb") as output:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(handle.fileno())
            current = os.lstat(source)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
            if identity(before) != identity(after) or identity(after) != identity(current):
                raise OSError("snapshot source changed while reading")
            if size != after.st_size:
                raise OSError("snapshot size changed while reading")
        if os.name != "nt":
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return {
            "kind": "regular", "digest": f"sha256:{digest.hexdigest()}",
            "blob_ref": destination.name,
            "mode": f"{stat.S_IMODE(after.st_mode):04o}", "size": size,
        }
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise MutationJournalError(f"cannot snapshot {source}: {exc}") from exc


def _capture_manual_blob(source: Path, destination: Path) -> dict:
    try:
        info = os.lstat(source)
    except FileNotFoundError as exc:
        raise MutationJournalError(f"snapshot source is missing: {source}") from exc
    if (not stat.S_ISREG(info.st_mode) or is_link_metadata(info)
            or info.st_nlink != 1 or info.st_size > 64 * 1024 * 1024):
        raise MutationJournalError("document source must be an ordinary file of at most 64 MiB")
    state = _capture_regular(source, destination)
    state["sha256"] = state["digest"].removeprefix("sha256:")
    return state
