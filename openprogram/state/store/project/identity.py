"""Portable folder identity stored alongside the user's working files."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from openprogram.host._platform.filesystem import replace_file

_PREFIX = "marker:"
_marker_lock = threading.RLock()


def inode_token(path: Path) -> str:
    """Read legacy records during upgrade; never generate new persistent IDs."""
    try:
        value = path.stat()
    except OSError:
        return ""
    return f"{value.st_dev}:{value.st_ino}"


def read_marker(path: str | Path) -> str:
    folder = Path(path).expanduser()
    directory = folder / ".openprogram"
    marker = directory / "project.json"
    try:
        if directory.is_symlink() or marker.is_symlink():
            return ""
        info = marker.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return ""
        value = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("id"), str):
            return ""
        return _PREFIX + str(uuid.UUID(value["id"]))
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def capture_directory_identity(path: str | Path, *, replace: bool = False) -> dict[str, str]:
    """Publish a marker on registration or explicit Locate, never on reads."""
    from openprogram.state.paths import get_state_dir
    from openprogram.state.store.session.session_lock import registry_file_lock

    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"not a directory: {folder}")
    lock_name = "project-marker-" + hashlib.sha256(os.fsencode(folder)).hexdigest()
    with _marker_lock, registry_file_lock(Path(get_state_dir()) / "projects", lock_name):
        directory = folder / ".openprogram"
        marker = directory / "project.json"
        if directory.is_symlink() or marker.is_symlink():
            raise ValueError("project identity metadata must not be a symbolic link")
        existing = read_marker(folder)
        if marker.exists() and not existing:
            raise ValueError("project identity file is invalid; restore or remove it before locating the folder")
        token = existing if existing and not replace else _PREFIX + str(uuid.uuid4())
        if token != existing:
            directory.mkdir(exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({"version": 1, "id": token.removeprefix(_PREFIX)}, output)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                replace_file(temporary, marker)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {"directory_identity": token, "native_bookmark": "", "volume_id": ""}


def is_portable(project) -> bool:
    return str(getattr(project, "directory_identity", "")).startswith(_PREFIX)


def captured_identity_matches(project, path: str | Path) -> bool:
    folder = Path(path).expanduser()
    if not folder.is_dir():
        return False
    stored = getattr(project, "directory_identity", "") or ""
    if is_portable(project):
        return read_marker(folder) == stored
    # Compatibility only: verified old records can be upgraded at their old
    # path. A changed device number is not proof of a new or existing identity.
    return bool(stored and stored == inode_token(folder))


def path_is_replacement(project, path: str | Path | None = None) -> bool:
    folder = Path(path or project.path).expanduser()
    return folder.is_dir() and not captured_identity_matches(project, folder)
