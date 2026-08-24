from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator


class PrivateAtomicWriteError(OSError):
    """Compatibility error for ordinary credential-file writes."""

    def __init__(
        self,
        code: str,
        path: Path,
        *,
        committed: bool,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(f"{code} failed for {path}")
        self.code = code
        self.path = path
        self.committed = committed
        self.__cause__ = cause


@dataclass(frozen=True)
class PrivateAtomicWriteResult:
    revision: str


_MISSING_REVISION = (
    "missing:sha256:"
    + hashlib.sha256(b"openprogram-private-file-missing-v1").hexdigest()
)
_held_locks = threading.local()


def _revision(raw: bytes | None) -> str:
    if raw is None:
        return _MISSING_REVISION
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _ensure_private_directory(path: Path, *, root: Path) -> Path:
    del root
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@contextmanager
def _private_file_lock(
    path: Path,
    *,
    root: Path,
    timeout: float = 10.0,
) -> Iterator[None]:
    del root, timeout
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = os.fspath(target.absolute())
    held = getattr(_held_locks, "paths", set())
    if key in held:
        yield
        return

    lock_path = target.with_suffix(target.suffix + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    from openprogram import _compat as file_lock

    file_lock.flock(descriptor, file_lock.LOCK_EX)
    held.add(key)
    _held_locks.paths = held
    try:
        yield
    finally:
        held.remove(key)
        file_lock.flock(descriptor, file_lock.LOCK_UN)
        os.close(descriptor)


def _read_private_bytes(path: Path, *, root: Path) -> bytes | None:
    del root
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _private_atomic_write(
    path: Path,
    writer: Callable[[BinaryIO], object],
    *,
    root: Path,
    expected_revision: str | None = None,
    lock_timeout: float = 10.0,
) -> PrivateAtomicWriteResult:
    del expected_revision
    with _private_file_lock(path, root=root, timeout=lock_timeout):
        return _atomic_write(path, writer)


def _atomic_write(
    path: Path, writer: Callable[[BinaryIO], object]
) -> PrivateAtomicWriteResult:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        try:
            with os.fdopen(descriptor, "w+b") as handle:
                writer(handle)
                handle.flush()
        except Exception as exc:
            raise PrivateAtomicWriteError(
                "write", target, committed=False, cause=exc
            ) from exc
        try:
            # O_RDWR, not O_RDONLY: Windows _commit/fsync requires a writable
            # handle and raises EBADF on a read-only descriptor.
            descriptor = os.open(temporary, os.O_RDWR)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise PrivateAtomicWriteError(
                "fsync", target, committed=False, cause=exc
            ) from exc
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise PrivateAtomicWriteError(
                "replace", target, committed=False, cause=exc
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return PrivateAtomicWriteResult(_revision(target.read_bytes()))


def _private_atomic_update(
    path: Path,
    updater: Callable[[bytes | None], bytes],
    *,
    root: Path,
    expected_revision: str | None = None,
    lock_timeout: float = 10.0,
) -> PrivateAtomicWriteResult:
    del expected_revision
    with _private_file_lock(path, root=root, timeout=lock_timeout):
        try:
            updated = updater(_read_private_bytes(path, root=root))
            if not isinstance(updated, bytes):
                raise TypeError("credential file updater must return bytes")
        except PrivateAtomicWriteError:
            raise
        except Exception as exc:
            raise PrivateAtomicWriteError(
                "serialization", Path(path), committed=False, cause=exc
            ) from exc
        return _atomic_write(path, lambda handle: handle.write(updated))


def private_file_revision(
    path: Path,
    *,
    root: Path,
    lock_timeout: float = 10.0,
) -> str:
    with _private_file_lock(path, root=root, timeout=lock_timeout):
        return _revision(_read_private_bytes(path, root=root))


def _private_unlink(
    path: Path,
    *,
    root: Path,
    lock_timeout: float = 10.0,
) -> bool:
    del root, lock_timeout
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PrivateAtomicWriteError(
            "delete", Path(path), committed=False, cause=exc
        ) from exc
    return True
