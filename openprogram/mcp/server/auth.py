"""Independent file credential for the local stdio MCP server."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

MCP_TOKEN_ENV = "OPENPROGRAM_MCP_TOKEN"

_CREATE_FAILED = "could not create MCP server token"
_EXISTS = "MCP server token already exists"
_FILE_INVALID = "MCP server token file is unavailable or invalid"
_ENV_MISSING = f"{MCP_TOKEN_ENV} is required"
_AUTH_FAILED = "MCP server authentication failed"
_MAX_TOKEN_BYTES = 4096


class MCPTokenError(RuntimeError):
    """Sanitized token creation or authentication failure."""


def token_path() -> Path:
    from openprogram.paths import get_state_dir

    return Path(get_state_dir()) / "mcp_server_token"


def _verify_private_regular(info: os.stat_result, message: str) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise MCPTokenError(message)
    if sys.platform != "win32" and hasattr(os, "geteuid"):
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise MCPTokenError(message)


def _verify_directory(info: os.stat_result, message: str, *, private: bool) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise MCPTokenError(message)
    if sys.platform != "win32" and hasattr(os, "geteuid"):
        if info.st_uid != os.geteuid():
            raise MCPTokenError(message)
        if private and stat.S_IMODE(info.st_mode) & 0o077:
            raise MCPTokenError(message)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("token write made no progress")
        view = view[written:]


def _selected_target(path: Path | None, message: str) -> Path:
    try:
        selected = Path(path) if path is not None else Path(token_path())
        if ".." in selected.parts:
            raise ValueError("parent traversal is not allowed")
        target = selected if selected.is_absolute() else Path.cwd() / selected
        if not target.name:
            raise ValueError("token path has no file name")
        return target
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(message) from None


def _prepare_parent(target: Path, *, create: bool, message: str) -> os.stat_result:
    try:
        current = Path(target.anchor)
        info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode):
            raise MCPTokenError(message)
        for part in target.parent.parts[1:]:
            current /= part
            try:
                next_info = os.lstat(current)
            except FileNotFoundError:
                if not create:
                    raise MCPTokenError(message) from None
                _verify_directory(info, message, private=False)
                if (
                    sys.platform != "win32"
                    and stat.S_IMODE(info.st_mode) & 0o022
                ):
                    raise MCPTokenError(message)
                try:
                    os.mkdir(current, 0o700)
                except FileExistsError:
                    pass
                next_info = os.lstat(current)
                _verify_directory(next_info, message, private=True)
            if not stat.S_ISDIR(next_info.st_mode):
                raise MCPTokenError(message)
            info = next_info
        _verify_directory(info, message, private=True)
        return info
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(message) from None


def _revalidate_parent(target: Path, expected: os.stat_result, message: str) -> None:
    current = _prepare_parent(target, create=False, message=message)
    if not _same_file(current, expected):
        raise MCPTokenError(message)


def _open_parent(target: Path, expected: os.stat_result, message: str) -> int:
    required = (os.open, os.link, os.stat, os.unlink)
    if (
        sys.platform == "win32"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in required)
    ):
        raise MCPTokenError(message)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened = os.fstat(descriptor)
        _verify_directory(opened, message, private=True)
        if not _same_file(opened, expected):
            raise MCPTokenError(message)
        _revalidate_parent(target, expected, message)
        return descriptor
    except Exception as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(exc, MCPTokenError):
            raise
        raise MCPTokenError(message) from None


def _revalidate_open_parent(
    target: Path, descriptor: int, expected: os.stat_result, message: str
) -> None:
    opened = os.fstat(descriptor)
    _verify_directory(opened, message, private=True)
    if not _same_file(opened, expected):
        raise MCPTokenError(message)
    _revalidate_parent(target, expected, message)


def _create_token_windows(target: Path, parent_info: os.stat_result) -> str:
    """Create and atomically publish an MCP token without POSIX ``dir_fd``.

    Windows has no CPython descriptor-relative link/unlink API. A unique file
    is written and fsynced in the already validated parent, then published by
    an atomic hard-link create. The target is never replaced, concurrent
    creators still have one winner, every ancestor is revalidated around the
    write, and no ACL or POSIX mode mutation is attempted.
    """

    descriptor: int | None = None
    temporary: Path | None = None
    published = False
    published_info: os.stat_result | None = None
    succeeded = False
    try:
        token = secrets.token_urlsafe(32)
        temporary = target.with_name(
            f".{target.name}.{secrets.token_hex(12)}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        initial_info = os.fstat(descriptor)
        _verify_private_regular(initial_info, _CREATE_FAILED)
        _revalidate_parent(target, parent_info, _CREATE_FAILED)
        _write_all(descriptor, token.encode("ascii"))
        os.fsync(descriptor)
        _revalidate_parent(target, parent_info, _CREATE_FAILED)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise MCPTokenError(_EXISTS) from None
        published = True
        published_info = os.lstat(target)
        _verify_private_regular(published_info, _CREATE_FAILED)
        if not _same_file(initial_info, published_info):
            raise MCPTokenError(_CREATE_FAILED)
        _revalidate_parent(target, parent_info, _CREATE_FAILED)
        succeeded = True
        return token
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(_CREATE_FAILED) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if published and not succeeded and published_info is not None:
            try:
                current = os.lstat(target)
                if _same_file(current, published_info):
                    target.unlink()
            except OSError:
                pass


def create_token(path: Path | None = None) -> str:
    """Create one private token file without replacing a concurrent winner."""
    try:
        target = _selected_target(path, _CREATE_FAILED)
        parent_info = _prepare_parent(target, create=True, message=_CREATE_FAILED)
        if sys.platform == "win32":
            return _create_token_windows(target, parent_info)
        parent_descriptor = _open_parent(target, parent_info, _CREATE_FAILED)
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(_CREATE_FAILED) from None

    descriptor: int | None = None
    temporary_name: str | None = None
    published = False
    published_info: os.stat_result | None = None
    succeeded = False
    try:
        token = secrets.token_urlsafe(32)
        temporary_name = f".{target.name}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_descriptor)
        initial_info = os.fstat(descriptor)
        _verify_private_regular(initial_info, _CREATE_FAILED)
        _revalidate_open_parent(target, parent_descriptor, parent_info, _CREATE_FAILED)
        _write_all(descriptor, token.encode("ascii"))
        os.fsync(descriptor)
        _revalidate_open_parent(target, parent_descriptor, parent_info, _CREATE_FAILED)

        try:
            os.link(
                temporary_name,
                target.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise MCPTokenError(_EXISTS) from None
        published = True
        published_info = initial_info
        _revalidate_open_parent(target, parent_descriptor, parent_info, _CREATE_FAILED)

        os.fchmod(descriptor, 0o600)
        published_info = os.fstat(descriptor)
        final_info = os.stat(
            target.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        _verify_private_regular(published_info, _CREATE_FAILED)
        _verify_private_regular(final_info, _CREATE_FAILED)
        if not _same_file(published_info, final_info):
            raise MCPTokenError(_CREATE_FAILED)
        succeeded = True
        return token
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(_CREATE_FAILED) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError:
                pass
        if published and not succeeded and published_info is not None:
            try:
                current = os.stat(
                    target.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _same_file(current, published_info):
                    os.unlink(target.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        try:
            os.close(parent_descriptor)
        except OSError:
            pass


def _read_stored_token(path: Path) -> str:
    descriptor: int | None = None
    try:
        parent_info = _prepare_parent(path, create=False, message=_FILE_INVALID)
        before = os.lstat(path)
        _verify_private_regular(before, _FILE_INVALID)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _verify_private_regular(opened, _FILE_INVALID)
        if not _same_file(before, opened):
            raise MCPTokenError(_FILE_INVALID)
        _revalidate_parent(path, parent_info, _FILE_INVALID)
        if opened.st_size > _MAX_TOKEN_BYTES:
            raise MCPTokenError(_FILE_INVALID)
        payload = bytearray()
        while len(payload) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(payload))
            if not chunk:
                raise MCPTokenError(_FILE_INVALID)
            payload.extend(chunk)
        if os.read(descriptor, 1):
            raise MCPTokenError(_FILE_INVALID)
        after = os.fstat(descriptor)
        _verify_private_regular(after, _FILE_INVALID)
        if (
            not _same_file(opened, after)
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise MCPTokenError(_FILE_INVALID)
        _revalidate_parent(path, parent_info, _FILE_INVALID)
        return bytes(payload).decode("ascii")
    except MCPTokenError:
        raise
    except Exception:
        raise MCPTokenError(_FILE_INVALID) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def authenticate_from_environment(
    environ: Mapping[str, str] = os.environ,
    path: Path | None = None,
) -> str:
    """Authenticate one process environment and return its stable client id."""
    presented = environ.get(MCP_TOKEN_ENV)
    if not isinstance(presented, str) or not presented:
        raise MCPTokenError(_ENV_MISSING)
    target = _selected_target(path, _FILE_INVALID)
    stored = _read_stored_token(target)
    try:
        matched = hmac.compare_digest(stored, presented)
    except Exception:
        raise MCPTokenError(_AUTH_FAILED) from None
    if not matched:
        raise MCPTokenError(_AUTH_FAILED)
    return hashlib.sha256(stored.encode("ascii")).hexdigest()[:16]


__all__ = [
    "MCP_TOKEN_ENV",
    "MCPTokenError",
    "authenticate_from_environment",
    "create_token",
    "token_path",
]
