"""Independent file credential for the local stdio MCP server."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

MCP_TOKEN_ENV = "OPENPROGRAM_MCP_TOKEN"

_CREATE_FAILED = "could not create MCP server token"
_EXISTS = "MCP server token already exists"
_FILE_INVALID = "MCP server token file is unavailable or invalid"
_ENV_MISSING = f"{MCP_TOKEN_ENV} is required"
_AUTH_FAILED = "MCP server authentication failed"


class MCPTokenError(RuntimeError):
    """Sanitized token creation or authentication failure."""


def token_path() -> Path:
    from openprogram.paths import get_state_dir

    return Path(get_state_dir()) / "mcp_server_token"


def _verify_private_regular(info: os.stat_result, message: str) -> None:
    if sys.platform == "win32" or not hasattr(os, "geteuid"):
        raise MCPTokenError(message)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
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


def create_token(path: Path | None = None) -> str:
    """Create one private token file without replacing a concurrent winner."""
    target = Path(path) if path is not None else token_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        raise MCPTokenError(_CREATE_FAILED) from None

    descriptor: int | None = None
    temporary: Path | None = None
    published = False
    published_info: os.stat_result | None = None
    succeeded = False
    try:
        token = secrets.token_urlsafe(32)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        initial_info = os.fstat(descriptor)
        _verify_private_regular(initial_info, _CREATE_FAILED)
        _write_all(descriptor, token.encode("ascii"))
        os.fsync(descriptor)

        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise MCPTokenError(_EXISTS) from None
        published = True
        published_info = initial_info

        os.fchmod(descriptor, 0o600)
        published_info = os.fstat(descriptor)
        final_info = os.stat(target, follow_symlinks=False)
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
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if published and not succeeded and published_info is not None:
            try:
                current = os.stat(target, follow_symlinks=False)
                if _same_file(current, published_info):
                    target.unlink()
            except OSError:
                pass


def _read_stored_token(path: Path) -> str:
    descriptor: int | None = None
    try:
        before = os.lstat(path)
        _verify_private_regular(before, _FILE_INVALID)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _verify_private_regular(opened, _FILE_INVALID)
        if not _same_file(before, opened):
            raise MCPTokenError(_FILE_INVALID)
        payload = os.read(descriptor, 4097)
        if len(payload) > 4096:
            raise MCPTokenError(_FILE_INVALID)
        return payload.decode("ascii")
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
    stored = _read_stored_token(Path(path) if path is not None else token_path())
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
