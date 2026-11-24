"""Telling a document's windows that its file changed on disk.

A window showing a file used to ask for its metadata every few seconds.
Now the window asks once (``GET /api/documents/stat``), which starts
following that file: the Worker watches the file's folder for that name
(:func:`openprogram.host._platform.changes.watch`), and a change to it is
published as :data:`TOPIC` keyed by :func:`key_of` the file. The window
reads the metadata again only when that key arrives.

The newest :data:`LIMIT` files followed stay watched; an older one is let
go, and its window starts following it again with its next stat.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

from openprogram.host._platform import changes

TOPIC = "documents"
#: How many files are followed at once.
LIMIT = 64

_lock = threading.Lock()
#: key -> the file, its folder resolved through links, newest last.
_files: "OrderedDict[str, Path]" = OrderedDict()

changes.publish(TOPIC)


def _located(target: Path) -> Path:
    """``target`` in its real folder; the name itself is kept, so a file
    replaced by a rename (an atomic save) is the same file."""
    target = Path(target)
    return Path(os.path.realpath(target.parent)) / target.name


def key_of(target: Path) -> str:
    """The key a change to ``target`` is published under."""
    return hashlib.sha256(str(_located(target)).encode("utf-8")).hexdigest()[:24]


def _key_in_folder(changed: Path) -> str:
    # Events name real paths, so the changed path is already located.
    return hashlib.sha256(str(changed).encode("utf-8")).hexdigest()[:24]


def follow(target: Path) -> str:
    """Publish changes to ``target`` from now on; returns its key."""
    located = _located(target)
    key = key_of(located)
    with _lock:
        if key in _files:
            _files.move_to_end(key)
            return key
        _files[key] = located
        folders = {located.parent}
        while len(_files) > LIMIT:
            _old_key, old = _files.popitem(last=False)
            folders.add(old.parent)
        # One watch per folder, naming every file followed in it.
        for folder in folders:
            names = {path.name for path in _files.values() if path.parent == folder}
            changes.unwatch(folder, TOPIC)
            if names:
                changes.watch(folder, TOPIC, names=names, key=_key_in_folder)
    return key


def _forget_all() -> None:
    """Stop following every file (tests)."""
    with _lock:
        folders = {path.parent for path in _files.values()}
        _files.clear()
        for folder in folders:
            changes.unwatch(folder, TOPIC)


__all__ = ["LIMIT", "TOPIC", "follow", "key_of"]
