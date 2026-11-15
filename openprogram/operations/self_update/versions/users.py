"""Which installs use this version store (``<root>/users.json``).

The store under ``~/.openprogram/runtime`` holds several GB of downloaded
runtime versions, and the command-line install, the App and a ``dev``
profile can share it. Each install adds itself here when it is installed and
again on every start (an install from before the file existed registers on
its first start), and removes itself when it is uninstalled. The uninstaller
of the last one offers to delete the store, unticked: a downloaded version
can be fetched again, and the conversations, settings and credentials in
``~/.openprogram`` are never part of it.

Entries are ``{"install_id", "kind": "app" | "cli", "path"}``. Writers take
``users.json.lock`` and publish the file with ``replace_file``, so two
installs registering at once both stay listed.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from openprogram.host._platform import locking
from openprogram.host._platform.filesystem import replace_file

FILE = "users.json"
KINDS = ("app", "cli")


def install_id(kind: str, path: str) -> str:
    """One id per install: its kind and its folder, compared without case on Windows."""
    folder = os.path.normcase(os.path.abspath(path)) if path else ""
    return hashlib.sha256(f"{kind}\0{folder}".encode("utf-8")).hexdigest()[:16]


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / f"{FILE}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        locking.flock(fd, locking.LOCK_EX)
        try:
            yield
        finally:
            locking.flock(fd, locking.LOCK_UN)
    finally:
        os.close(fd)


def users(root: Path) -> list[dict]:
    """The installs listed in ``root``; an unreadable file lists none."""
    try:
        loaded = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = loaded.get("users") if isinstance(loaded, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("install_id"), str)]


def _write(root: Path, rows: list[dict]) -> None:
    target = root / FILE
    temporary = root / f".{FILE}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps({"schema": 1, "users": rows}, indent=2), encoding="utf-8")
    replace_file(temporary, target)


def register(root: Path, kind: str, path: str) -> str:
    """List this install in ``root`` (once); returns its id."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    root = Path(root)
    ident = install_id(kind, path)
    with _locked(root):
        rows = users(root)
        if not any(row["install_id"] == ident for row in rows):
            rows.append({"install_id": ident, "kind": kind, "path": path})
            _write(root, rows)
    return ident


def unregister(root: Path, kind: str, path: str) -> list[dict]:
    """Remove this install from ``root``; returns the installs still listed."""
    root = Path(root)
    if not root.is_dir():
        return []
    ident = install_id(kind, path)
    with _locked(root):
        rows = users(root)
        left = [row for row in rows if row["install_id"] != ident]
        if len(left) != len(rows):
            _write(root, left)
    return left


def register_running_install() -> None:
    """Register the install this interpreter runs for, when it runs from the store.

    An App sets ``OPENPROGRAM_IMMUTABLE_RUNTIME`` and names its folder in
    ``OPENPROGRAM_APP_PATH``; anything else is the command-line install, named
    by its launcher.
    """
    from .layout import current_runtime
    runtime = current_runtime()
    if runtime is None:
        return
    app = os.environ.get("OPENPROGRAM_APP_PATH", "")
    if app:
        register(runtime.layout.root, "app", app)
    else:
        register(runtime.layout.root, "cli", str(runtime.layout.launcher().parent))


__all__ = ["FILE", "install_id", "register", "register_running_install", "unregister", "users"]
