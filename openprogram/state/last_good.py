"""The last good generation of a JSON state file.

A file that is there but cannot be read is *unknown*, never empty. A
machine that went down mid-write can leave a state file at its full length
with every byte zero, or cut short; reading that as ``{}`` loses every
setting, and the next read-modify-write makes the loss final.

So every atomic write of a JSON state file first keeps the file it replaces
as ``<file>.bak`` when that file is valid JSON (:func:`keep_previous`), and
a read of a file that is not valid JSON (:func:`load_json`)

- copies the unreadable bytes aside once as ``<file>.bad-<UTC time>``, so
  nothing is lost whatever happens next, and
- answers the ``.bak``'s contents when it is valid, else nothing (``None``),
  and logs which.

Only JSON documents are judged: a file of another kind is replaced as it
is, without a ``.bak``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from openprogram.host._platform.filesystem import replace_file

log = logging.getLogger(__name__)

BACKUP_SUFFIX = ".bak"
UNREADABLE_SUFFIX = ".bad-"


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def _parse(raw: bytes) -> Any:
    """The JSON document in ``raw``; ``ValueError`` when it is not one."""
    text = raw.decode("utf-8")  # UnicodeDecodeError is a ValueError
    if not text.strip():
        raise ValueError("empty document")
    return json.loads(text)


def is_valid_json(raw: bytes | None) -> bool:
    if raw is None:
        return False
    try:
        _parse(raw)
    except ValueError:
        return False
    return True


def keep_previous(path: Path) -> None:
    """Keep the JSON file at ``path`` as its ``.bak`` before it is replaced.

    Called by the atomic writer under the file's lock. A file that is not
    valid JSON is not kept: the ``.bak`` stays the last good one. Never
    raises; a ``.bak`` that could not be written leaves the write itself
    alone.
    """
    if path.suffix != ".json":
        return
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if not is_valid_json(raw):
        return
    backup = backup_path(path)
    try:
        if backup.exists() and backup.read_bytes() == raw:
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{backup.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
            shutil.copymode(path, temporary)
            replace_file(temporary, backup)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not keep %s as %s: %s", path, backup.name, exc)


def _set_aside(path: Path, raw: bytes) -> Path | None:
    """Copy unreadable ``raw`` next to ``path`` once per distinct content."""
    digest = hashlib.sha256(raw).hexdigest()[:12]
    for existing in path.parent.glob(path.name + UNREADABLE_SUFFIX + "*"):
        if existing.name.endswith(digest):
            return existing
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    aside = path.with_name(f"{path.name}{UNREADABLE_SUFFIX}{stamp}-{digest}")
    try:
        aside.write_bytes(raw)
        shutil.copymode(path, aside)
    except OSError as exc:
        log.warning("could not set unreadable %s aside: %s", path, exc)
        return None
    return aside


def load_json(path: Path, raw: bytes | None) -> Any | None:
    """The JSON document ``raw`` read from ``path``, or its last good one.

    ``raw`` is ``None`` for a missing file, which answers ``None``. Valid
    JSON answers as parsed. Anything else is set aside and answered by the
    ``.bak`` when that is valid, else ``None``.
    """
    if raw is None:
        return None
    try:
        return _parse(raw)
    except ValueError:
        pass
    aside = _set_aside(path, raw)
    try:
        backup_raw = backup_path(path).read_bytes()
    except OSError:
        backup_raw = None
    if is_valid_json(backup_raw):
        log.warning(
            "%s is not valid JSON; using its last good copy %s (unreadable bytes kept as %s)",
            path, backup_path(path).name, aside.name if aside else "nothing",
        )
        return _parse(backup_raw)  # type: ignore[arg-type]
    log.error(
        "%s is not valid JSON and has no good copy (unreadable bytes kept as %s)",
        path, aside.name if aside else "nothing",
    )
    return None
