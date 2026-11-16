"""Atomic whole-file writes, for the small state files that are replaced
rather than appended to."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from openprogram.host._platform.filesystem import filesystem_path, retry_sharing_violation


def read_text_with_retry(fpath: Path) -> str:
    """Read UTF-8 text through Windows' transient replace window."""
    native_path = filesystem_path(fpath)
    return retry_sharing_violation(
        lambda: Path(native_path).read_text(encoding="utf-8")
    )


def atomic_write_text(fpath: Path, text: str) -> Path:
    """Write ``text`` to ``fpath`` via a private temp file + rename.

    The temp file carries a random suffix because the destination is
    NOT single-writer: a state file can be rewritten by every branch
    write, and the Stage-2 auto-namer runs on a background thread while
    the job runner archives the same branch. A shared ``<name>.tmp``
    let those two threads write the same staging file at once — the
    rename then published a file holding both writers' bytes (invalid
    JSON, so the next rebuild read an empty meta and lost the session's
    title / head / branches), and the loser of the rename race raised
    FileNotFoundError. ``os.replace`` is atomic per file, so a private
    temp per write makes last-writer-wins the only outcome.

    The parent directory is fsynced after the rename, not just the file.
    A rename is a directory change, so fsyncing only the file's contents
    leaves the rename itself unflushed: a power loss then reverts the
    destination to its previous bytes even though the write "succeeded".
    """
    # Profile state is owner-only on POSIX; Windows retains inherited ACLs.
    # Apply the mode at creation so even the unpublished bytes remain private.
    tmp = fpath.with_name(f"{fpath.name}.{uuid.uuid4().hex}.tmp")
    native_tmp = filesystem_path(tmp)
    native_target = filesystem_path(fpath)
    created = False
    try:
        descriptor = os.open(
            native_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        from openprogram.host._platform.filesystem import restrict_to_user
        restrict_to_user(native_tmp)
        retry_sharing_violation(
            lambda: os.replace(native_tmp, native_target)
        )
        _fsync_directory(fpath.parent)
    finally:
        # No-op after a successful replace; cleans up when write_text
        # or replace raised so a failed write leaves no litter.
        if created:
            try:
                os.unlink(native_tmp)
            except FileNotFoundError:
                pass
    return fpath


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry, where the platform has such a thing.

    Windows has no directory descriptor to sync and raises trying; that
    is not a failure of the write, which has already landed.
    """
    try:
        descriptor = os.open(filesystem_path(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
