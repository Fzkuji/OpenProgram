"""Inspect file contents and verify recorded parent identities."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from openprogram._compat import is_link_metadata


_DIR_FD_APPLY_SUPPORTED = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.rename in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and os.link in os.supports_follow_symlinks
    and os.unlink in os.supports_dir_fd
)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _digest_fd(descriptor: int) -> str:
    value = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _file_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "special"


def _inspect_state(path: str) -> dict:
    try:
        chain = _capture_parent_chain(path)
        if not _DIR_FD_APPLY_SUPPORTED:
            parent = _verify_parent_path(path, chain)
        else:
            descriptor = _open_verified_parent(path, chain)
    except (FileNotFoundError, NotADirectoryError):
        return {"kind": "absent"}
    except OSError:
        return {"kind": "unsafe_parent"}
    if not _DIR_FD_APPLY_SUPPORTED:
        return _inspect_state_path(parent / Path(path).name)
    try:
        return _inspect_state_at(descriptor, Path(path).name)
    finally:
        os.close(descriptor)


def _capture_parent_chain(path: str) -> dict:
    target = Path(path)
    if not target.is_absolute() or not target.name:
        raise OSError(f"history path must be an absolute file path: {path}")
    parts = target.parent.parts
    if not parts:
        raise OSError(f"history path has no parent: {path}")
    current = Path(parts[0])
    root_info = os.lstat(current)
    if not stat.S_ISDIR(root_info.st_mode) or is_link_metadata(root_info):
        raise OSError(f"unsafe root for history path: {path}")
    components = []
    for name in parts[1:]:
        current = current / name
        info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode) or is_link_metadata(info):
            raise OSError(f"unsafe parent for history path: {current}")
        components.append({"name": name, "dev": info.st_dev, "ino": info.st_ino})
    return {
        "root": parts[0],
        "root_dev": root_info.st_dev,
        "root_ino": root_info.st_ino,
        "components": components,
    }


def _verify_parent_path(path: str, chain: dict) -> Path:
    # Windows has no equivalent dir_fd primitive. This fallback rechecks each
    # parent with lstat but cannot prevent symlink-swap races; that weaker
    # guarantee is an accepted tradeoff for making restore available there.
    current = Path(str(chain["root"]))
    info = os.lstat(current)
    if (
        not stat.S_ISDIR(info.st_mode)
        or is_link_metadata(info)
        or (info.st_dev, info.st_ino) != (
            chain.get("root_dev"), chain.get("root_ino"),
        )
    ):
        raise OSError(f"history root changed before apply: {path}")
    for component in chain.get("components", []):
        current = current / component["name"]
        info = os.lstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or is_link_metadata(info)
            or (info.st_dev, info.st_ino) != (
                component.get("dev"), component.get("ino"),
            )
        ):
            raise OSError(f"history parent changed before apply: {path}")
    return current


def _open_verified_parent(path: str, chain: dict) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(chain["root"]), flags | nofollow)
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (
            chain.get("root_dev"), chain.get("root_ino"),
        ):
            raise OSError(f"history root changed before apply: {path}")
        for component in chain.get("components", []):
            child = os.open(
                component["name"], flags | nofollow, dir_fd=descriptor,
            )
            try:
                child_info = os.fstat(child)
                if (child_info.st_dev, child_info.st_ino) != (
                    component.get("dev"), component.get("ino"),
                ):
                    raise OSError(f"history parent changed before apply: {path}")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inspect_state_path(path: Path) -> dict:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"kind": "absent"}
    if is_link_metadata(info):
        return {"kind": "symlink"}
    if not stat.S_ISREG(info.st_mode):
        return {"kind": _file_kind(info.st_mode)}
    if info.st_nlink != 1:
        return {"kind": "hardlink", "links": info.st_nlink}
    file_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        return {
            "kind": "regular",
            "digest": _digest_fd(file_descriptor),
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size,
        }
    finally:
        os.close(file_descriptor)


def _inspect_state_at(descriptor: int, name: str) -> dict:
    try:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return {"kind": "absent"}
    if not stat.S_ISREG(info.st_mode):
        return {"kind": _file_kind(info.st_mode)}
    if info.st_nlink != 1:
        return {"kind": "hardlink", "links": info.st_nlink}
    file_descriptor = os.open(
        name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=descriptor,
    )
    try:
        return {
            "kind": "regular",
            "digest": _digest_fd(file_descriptor),
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": info.st_size,
        }
    finally:
        os.close(file_descriptor)


def _state_matches(actual: dict, expected: dict) -> bool:
    if actual.get("kind") != expected.get("kind"):
        return False
    if expected.get("kind") == "regular":
        return actual.get("digest") == expected.get("digest")
    return expected.get("kind") == "absent"


def _same_recorded_state(first: dict, second: dict) -> bool:
    if first.get("kind") != second.get("kind"):
        return False
    if first.get("kind") == "regular":
        return first.get("digest") == second.get("digest")
    return first.get("kind") == "absent"


def _blob_is_exact(state: dict) -> bool:
    if state.get("kind") != "regular":
        return state.get("kind") == "absent"
    blob = Path(str(state.get("blob_path") or ""))
    if not blob.is_file():
        return False
    try:
        return _digest(blob) == state.get("digest")
    except OSError:
        return False
