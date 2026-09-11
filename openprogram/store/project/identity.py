"""Directory identity separate from display path and project id."""
from __future__ import annotations

import os
from pathlib import Path

from . import native


def inode_token(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_dev}:{stat.st_ino}"


def capture_directory_identity(path: str | Path) -> dict[str, str]:
    """Record native identity at registration or explicit locate/relocate."""
    folder = Path(path).expanduser()
    token = inode_token(folder)
    bookmark = ""
    volume = ""
    try:
        bookmark = native.create_bookmark(folder) or ""
        volume = native.volume_id(folder) or ""
    except Exception:
        bookmark = bookmark or ""
    return {
        "directory_identity": token,
        "native_bookmark": bookmark,
        "volume_id": volume,
    }


def captured_identity_matches(project, path: str | Path) -> bool:
    """True when ``path`` is the same directory the project was bound to."""
    folder = Path(path).expanduser()
    if not folder.is_dir():
        return False
    stored = getattr(project, "directory_identity", "") or ""
    current = inode_token(folder)
    if stored and current:
        if stored == current:
            return True
        if stored.partition(":")[0] == current.partition(":")[0]:
            # A bookmark may fall back to the old pathname after replacement.
            # It cannot override a different inode on the same device.
            return False
    bookmark = getattr(project, "native_bookmark", "") or ""
    if bookmark:
        resolved = native.resolve_bookmark(bookmark)
        if resolved is not None:
            try:
                return Path(resolved).resolve() == folder.resolve()
            except OSError:
                return False
    return False


def path_is_replacement(project, path: str | Path | None = None) -> bool:
    """True when the registered path exists but is not the original directory."""
    folder = Path(path or project.path).expanduser()
    if not folder.is_dir():
        return False
    stored = getattr(project, "directory_identity", "") or ""
    if not stored and not (getattr(project, "native_bookmark", "") or ""):
        # A legacy record has no evidence that the current directory is the
        # one originally registered.  Treat it as unverified until explicit
        # Locate captures a native identity.
        return True
    return not captured_identity_matches(project, folder)


def resolve_bookmark_path(project) -> Path | None:
    bookmark = getattr(project, "native_bookmark", "") or ""
    if not bookmark:
        return None
    resolved = native.resolve_bookmark(bookmark)
    if not resolved:
        return None
    path = Path(resolved)
    return path if path.is_dir() else None
