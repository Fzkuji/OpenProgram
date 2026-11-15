"""Delete a tree this root owns, and say what could not go.

``shutil.rmtree(ignore_errors=True)`` leaves a file behind without a word. On
Windows that is every read-only file (Git writes its objects read-only) and
every file a running process or a scanner still holds; on POSIX it is anything
inside a directory without write permission. Here a refused entry is made
writable and tried once more, and whatever still stands is returned, so the
caller can report it and the next pass can try again.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import sys

from openprogram.host._platform.filesystem import is_link_metadata


def _make_writable(path: str) -> None:
    """Clear what refuses a delete: the read-only bit on Windows, a missing owner write bit on POSIX."""
    try:
        info = os.lstat(path)
        if is_link_metadata(info):
            return  # chmod would reach through a link or junction to a tree that is not ours.
        wanted = stat.S_IMODE(info.st_mode) | stat.S_IWUSR
        if stat.S_ISDIR(info.st_mode):
            wanted |= stat.S_IRUSR | stat.S_IXUSR
        os.chmod(path, wanted)
    except OSError:
        pass


def remove_tree(path: Path | str) -> list[str]:
    """Remove ``path`` and return the entries that would not go.

    An empty list means it is gone. A missing ``path`` is gone too.
    """
    root = os.fspath(path)
    refused: list[str] = []
    retried: set[str] = set()

    def handle(function, name, error) -> None:
        name = os.fspath(name)
        if isinstance(error, FileNotFoundError):
            return
        if not isinstance(error, PermissionError) or name in retried:
            refused.append(name)
            return
        retried.add(name)
        if name != root:
            # The root's parent is not part of the tree; its permissions stay as they are.
            _make_writable(os.path.dirname(name))
        _make_writable(name)
        try:
            info = os.lstat(name)
            if stat.S_ISDIR(info.st_mode) and not is_link_metadata(info):
                # It could not be listed or removed: walk it again now that it can be.
                _rmtree(name, handle)
            else:
                os.unlink(name)
        except FileNotFoundError:
            pass
        except OSError:
            refused.append(name)

    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return []
    except OSError:
        return [root]
    if is_link_metadata(info) or not stat.S_ISDIR(info.st_mode):
        try:
            os.unlink(root)
        except OSError as error:
            handle(os.unlink, root, error)
    else:
        _rmtree(root, handle)
    standing = [name for name in dict.fromkeys(refused) if os.path.lexists(name)]
    # A directory that stays only because something inside it did is not named again.
    remaining = [name for name in standing
                 if not any(other.startswith(name + os.sep) for other in standing)]
    if not remaining and os.path.lexists(root):
        remaining.append(root)
    return remaining


def _rmtree(path: str, handle) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handle)
    else:
        shutil.rmtree(path, onerror=lambda function, name, info: handle(function, name, info[1]))


__all__ = ["remove_tree"]
