from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator
from openprogram.host._platform.filesystem import replace_file


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


def _make_private_dirs(directory: Path) -> None:
    """Create ``directory`` and each missing parent readable by the owner only (0700)."""
    missing = []
    here = Path(directory)
    while not here.exists() and here != here.parent:
        missing.append(here)
        here = here.parent
    for folder in reversed(missing):
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(folder, 0o700)  # mkdir's mode is masked by the umask


def _ensure_private_directory(path: Path, *, root: Path) -> Path:
    del root
    directory = Path(path)
    _make_private_dirs(directory)
    return directory


def tighten_private_directory(directory: Path) -> None:
    """Make an existing private folder readable by its owner only (0700)."""
    try:
        if Path(directory).is_dir() and os.stat(directory).st_mode & 0o077:
            os.chmod(directory, 0o700)
    except OSError:
        pass


@contextmanager
def _private_file_lock(
    path: Path,
    *,
    root: Path,
    timeout: float = 10.0,
) -> Iterator[None]:
    del root, timeout
    target = Path(path)
    _make_private_dirs(target.parent)
    key = os.fspath(target.absolute())
    held = getattr(_held_locks, "paths", set())
    if key in held:
        yield
        return

    lock_path = target.with_suffix(target.suffix + ".lock")
    from openprogram.host._platform import locking

    for _attempt in range(50):
        try:
            # Owner-only, like the file it guards (it holds nothing, but
            # nothing in a private folder is readable by others).
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except FileNotFoundError:
            # The folder went (an empty one is tidied away): make it again.
            _make_private_dirs(target.parent)
            continue
        locking.flock(descriptor, locking.LOCK_EX)
        if _is_current_lock(descriptor, lock_path):
            _owner_only(descriptor)
            break
        # The lock file was removed while this process waited for it
        # (``drop_unused_lock``): take the one at the path now.
        locking.flock(descriptor, locking.LOCK_UN)
        os.close(descriptor)
    else:
        raise OSError(f"could not lock {lock_path}")
    held.add(key)
    _held_locks.paths = held
    try:
        yield
    finally:
        held.remove(key)
        locking.flock(descriptor, locking.LOCK_UN)
        os.close(descriptor)


def _owner_only(descriptor: int) -> None:
    """A lock file an earlier version made 0644, made 0600."""
    try:
        if hasattr(os, "fchmod") and os.fstat(descriptor).st_mode & 0o077:
            os.fchmod(descriptor, 0o600)
    except OSError:
        pass


def _is_current_lock(descriptor: int, lock_path: Path) -> bool:
    """Whether the open lock file is still the one at ``lock_path``."""
    try:
        here = os.stat(lock_path)
    except FileNotFoundError:
        return False
    held = os.fstat(descriptor)
    return (here.st_dev, here.st_ino) == (held.st_dev, held.st_ino)


def drop_unused_lock(lock_path: Path, guarded: Path) -> bool:
    """Remove ``lock_path`` when the file it guards does not exist and no
    process holds it (taken without waiting; skipped when held). Removed
    while held here, so a process waiting on it takes a new one."""
    from openprogram.host._platform import locking

    try:
        descriptor = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            locking.flock(descriptor, locking.LOCK_EX | locking.LOCK_NB)
        except OSError:
            return False  # a writer has it
        try:
            if guarded.exists() or not _is_current_lock(descriptor, lock_path):
                return False
            os.unlink(lock_path)
            return True
        except OSError:
            return False
        finally:
            locking.flock(descriptor, locking.LOCK_UN)
    finally:
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
    _make_private_dirs(target.parent)
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
        # The file being replaced stays as its last good copy (<file>.bak),
        # so a later unreadable file is never read as empty.
        from openprogram.state.last_good import keep_previous

        keep_previous(target)
        try:
            replace_file(temporary, target)
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
