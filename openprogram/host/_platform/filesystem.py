"""Platform filesystem."""
from __future__ import annotations
import errno
import os
import sys as _sys


_DIRECTORY_FD_SUPPORTED = os.open in os.supports_dir_fd and os.scandir in os.supports_fd


def filesystem_path(path) -> str:
    """Return a Win32-safe spelling for a local filesystem path.

    Python still reaches legacy ``MAX_PATH`` APIs when Windows long-path
    policy is disabled.  The extended-length prefix bypasses that process-
    external registry dependency without changing the path on POSIX.  Keep
    this conversion at the compatibility seam so product modules do not grow
    scattered ``\\\\?\\`` branches.
    """

    value = os.fspath(path)
    if _sys.platform != "win32" or not isinstance(value, str):
        return value
    if value.startswith("\\\\?\\"):
        return value
    absolute = os.path.abspath(value)
    if len(absolute) < 248:
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


# ``os.open`` hands back a text-mode descriptor on Windows unless this flag
# is set: ``os.write`` then turns every LF into CRLF and ``os.read`` stops at
# Ctrl-Z, so byte offsets and hashes stop matching what was written. Every
# descriptor that is written or read through ``os.write`` / ``os.read``
# carries it. Zero on POSIX.
O_BINARY = getattr(os, "O_BINARY", 0)

# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION.
_SHARING_VIOLATIONS = frozenset({5, 32, 33})
_SHARING_RETRY_SECONDS = 1.0


def is_sharing_violation(error: BaseException) -> bool:
    """True when ``error`` is Windows refusing a path another handle holds."""
    return _sys.platform == "win32" and (
        isinstance(error, PermissionError)
        or getattr(error, "winerror", None) in _SHARING_VIOLATIONS
    )


def retry_sharing_violation(operation):
    """Run ``operation`` through Windows' brief sharing violations.

    Windows refuses to replace, rename, delete or open a file while another
    handle holds it without delete sharing: a reader in this or another
    process, an indexer, or an antivirus scan of the bytes just written. The
    refusal clears within milliseconds, so it is retried for one second and
    then raised. POSIX has no such refusal and runs ``operation`` once.
    """
    import time

    deadline = time.monotonic() + _SHARING_RETRY_SECONDS
    delay = 0.002
    while True:
        try:
            return operation()
        except OSError as exc:
            if not is_sharing_violation(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def replace_file(source, target) -> None:
    """Atomically publish ``source`` as ``target``, on every platform.

    ``os.replace`` with the retry Windows needs while ``target`` is open
    elsewhere. Every temp-file-then-rename write of profile state goes
    through here, so a concurrent reader delays a save instead of failing it.
    """
    native_source = filesystem_path(source)
    native_target = filesystem_path(target)
    retry_sharing_violation(lambda: os.replace(native_source, native_target))


def restrict_descriptor_to_user(descriptor: int) -> None:
    """Apply POSIX descriptor mode without rewriting inherited Windows ACLs."""
    if _sys.platform != "win32":
        os.fchmod(descriptor, 0o600)


def restrict_to_user(path) -> None:
    """Apply owner-only POSIX mode; preserve inherited ACLs on Windows W1.

    POSIX: ``chmod 0o600`` (owner read/write, nothing for group/other) —
    identical to the bare ``os.chmod(path, 0o600)`` call sites this
    replaces.

    Windows W1 deliberately preserves the inherited NTFS ACL. POSIX mode
    bits have no equivalent there, while rewriting inherited ACLs has made
    state unreadable for domain accounts, OneDrive folders, and managed
    machines. Credential-specific ACL hardening
    therefore remains outside W1; callers still get the normal user-profile
    ACL without a compatibility-breaking mutation.
    """
    p = os.fspath(path)
    if _sys.platform == "win32":
        return
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def restrict_directory_to_user(path) -> None:
    """Apply owner-only POSIX mode; preserve inherited ACLs on Windows W1."""

    p = os.fspath(path)
    if _sys.platform == "win32":
        return
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass


def is_link_metadata(info) -> bool:
    """Recognize POSIX links and Windows directory junction/reparse entries."""
    import stat
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def remove_tree(path, *, ignore_errors: bool = False) -> None:
    """Remove owned directories, retrying read-only Windows Git objects."""
    import shutil
    import stat

    def onerror(operation, filename, exc_info):
        error = exc_info[1]
        try:
            if isinstance(error, PermissionError) and operation in (os.unlink, os.remove):
                info = os.lstat(filename)
                if (stat.S_ISREG(info.st_mode) and not is_link_metadata(info)
                        and getattr(info, "st_file_attributes", 0) & 1):
                    os.chmod(filename, info.st_mode | stat.S_IWRITE)
                    operation(filename)
                    return
        except OSError:
            if not ignore_errors:
                raise
            return
        if not ignore_errors:
            raise error

    # onerror supports the project's Python 3.11 minimum. Passing
    # ignore_errors to shutil would bypass the read-only recovery callback.
    shutil.rmtree(path, onerror=onerror)


def user_private_metadata(info, *, exact_mode: int | None = None) -> bool:
    """POSIX ownership policy; Windows uses inherited profile ACLs unchanged."""
    import stat
    if _sys.platform == "win32":
        return not bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if info.st_uid != os.getuid():
        return False
    mode = stat.S_IMODE(info.st_mode)
    return mode == exact_mode if exact_mode is not None else not bool(mode & 0o077)


def open_regular_binary(path):
    """Open a stable regular file for streaming, without changing its access policy."""
    import stat

    native = filesystem_path(path)
    before = os.lstat(native)
    if not stat.S_ISREG(before.st_mode) or is_link_metadata(before):
        raise ValueError("not a regular file")
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(native, flags)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or is_link_metadata(info)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("file changed while opening")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_user_state_bytes(path, *, limit: int) -> bytes:
    """Bounded regular-file read using the host's user-state metadata policy."""
    import stat
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or not user_private_metadata(before):
        raise ValueError("invalid user-state file")
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or not user_private_metadata(info)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
                or info.st_size > limit):
            raise ValueError("invalid user-state file")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("user-state file exceeds read limit")
        return raw


def directory_handle(path):
    """Open a no-follow directory; path fallback where dir_fd is unavailable.

    The fallback validates ancestors on each operation but cannot provide POSIX
    descriptor-relative atomicity against concurrent directory replacement.
    It deliberately does not alter Windows ACLs or require directory open().
    """
    import stat
    from pathlib import Path

    if _DIRECTORY_FD_SUPPORTED:
        return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
    target = Path(path).absolute()
    for component in reversed((target, *target.parents)):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError(errno.ELOOP, "directory is a link or not a directory")
    return str(target)


def directory_child(handle, name):
    if isinstance(handle, str):
        return directory_handle(os.path.join(handle, name))
    return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                   | getattr(os, "O_NOFOLLOW", 0), dir_fd=handle)


def directory_close(handle):
    if not isinstance(handle, str):
        os.close(handle)


def directory_duplicate(handle):
    return directory_handle(handle) if isinstance(handle, str) else os.dup(handle)


def directory_read_file(handle, name):
    """Return a binary file descriptor without following a reparse point."""
    import stat

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not isinstance(handle, str):
        return os.open(name, flags, dir_fd=handle)
    directory_handle(handle)
    path = os.path.join(handle, name)
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise OSError(errno.ELOOP, "file is a link or not a regular file")
    fd = os.open(path, flags)
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise OSError(errno.ESTALE, "file changed while opening")
    return fd
