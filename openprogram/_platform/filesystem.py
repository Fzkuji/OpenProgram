"""Platform filesystem."""
from __future__ import annotations
from .. import _compat as state


def filesystem_path(path) -> str:
    """Return a Win32-safe spelling for a local filesystem path.

    Python still reaches legacy ``MAX_PATH`` APIs when Windows long-path
    policy is disabled.  The extended-length prefix bypasses that process-
    external registry dependency without changing the path on POSIX.  Keep
    this conversion at the compatibility seam so product modules do not grow
    scattered ``\\\\?\\`` branches.
    """

    value = state._os.fspath(path)
    if state._sys.platform != "win32" or not isinstance(value, str):
        return value
    if value.startswith("\\\\?\\"):
        return value
    absolute = state._os.path.abspath(value)
    if len(absolute) < 248:
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def restrict_descriptor_to_user(descriptor: int) -> None:
    """Apply POSIX descriptor mode without rewriting inherited Windows ACLs."""
    if state._sys.platform != "win32":
        state._os.fchmod(descriptor, 0o600)


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
    p = state._os.fspath(path)
    if state._sys.platform == "win32":
        return
    try:
        state._os.chmod(p, 0o600)
    except OSError:
        pass


def restrict_directory_to_user(path) -> None:
    """Apply owner-only POSIX mode; preserve inherited ACLs on Windows W1."""

    p = state._os.fspath(path)
    if state._sys.platform == "win32":
        return
    try:
        state._os.chmod(p, 0o700)
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
            if isinstance(error, PermissionError) and operation in (state._os.unlink, state._os.remove):
                info = state._os.lstat(filename)
                if (stat.S_ISREG(info.st_mode) and not state.is_link_metadata(info)
                        and getattr(info, "st_file_attributes", 0) & 1):
                    state._os.chmod(filename, info.st_mode | stat.S_IWRITE)
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
    if state._sys.platform == "win32":
        return not bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if info.st_uid != state._os.getuid():
        return False
    mode = stat.S_IMODE(info.st_mode)
    return mode == exact_mode if exact_mode is not None else not bool(mode & 0o077)


def open_regular_binary(path):
    """Open a stable regular file for streaming, without changing its access policy."""
    import stat

    native = state.filesystem_path(path)
    before = state._os.lstat(native)
    if not stat.S_ISREG(before.st_mode) or state.is_link_metadata(before):
        raise ValueError("not a regular file")
    flags = (state._os.O_RDONLY | getattr(state._os, "O_NONBLOCK", 0)
             | getattr(state._os, "O_BINARY", 0) | getattr(state._os, "O_NOFOLLOW", 0))
    fd = state._os.open(native, flags)
    try:
        info = state._os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or state.is_link_metadata(info)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("file changed while opening")
        return state._os.fdopen(fd, "rb")
    except BaseException:
        state._os.close(fd)
        raise


def read_user_state_bytes(path, *, limit: int) -> bytes:
    """Bounded regular-file read using the host's user-state metadata policy."""
    import stat
    before = state._os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or not state.user_private_metadata(before):
        raise ValueError("invalid user-state file")
    flags = (state._os.O_RDONLY | getattr(state._os, "O_NONBLOCK", 0)
             | getattr(state._os, "O_BINARY", 0) | getattr(state._os, "O_NOFOLLOW", 0))
    fd = state._os.open(path, flags)
    with state._os.fdopen(fd, "rb") as stream:
        info = state._os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or not state.user_private_metadata(info)
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

    if state._DIRECTORY_FD_SUPPORTED:
        return state.os.open(path, state.os.O_RDONLY | getattr(state.os, "O_DIRECTORY", 0)
                       | getattr(state.os, "O_NOFOLLOW", 0))
    target = Path(path).absolute()
    for component in reversed((target, *target.parents)):
        info = component.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError(state.errno.ELOOP, "directory is a link or not a directory")
    return str(target)


def directory_child(handle, name):
    if isinstance(handle, str):
        return state.directory_handle(state.os.path.join(handle, name))
    return state.os.open(name, state.os.O_RDONLY | getattr(state.os, "O_DIRECTORY", 0)
                   | getattr(state.os, "O_NOFOLLOW", 0), dir_fd=handle)


def directory_close(handle):
    if not isinstance(handle, str):
        state.os.close(handle)


def directory_duplicate(handle):
    return state.directory_handle(handle) if isinstance(handle, str) else state.os.dup(handle)


def directory_read_file(handle, name):
    """Return a binary file descriptor without following a reparse point."""
    import stat

    flags = state.os.O_RDONLY | getattr(state.os, "O_BINARY", 0) | getattr(state.os, "O_NOFOLLOW", 0)
    if not isinstance(handle, str):
        return state.os.open(name, flags, dir_fd=handle)
    state.directory_handle(handle)
    path = state.os.path.join(handle, name)
    before = state.os.lstat(path)
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise OSError(state.errno.ELOOP, "file is a link or not a regular file")
    fd = state.os.open(path, flags)
    after = state.os.fstat(fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        state.os.close(fd)
        raise OSError(state.errno.ESTALE, "file changed while opening")
    return fd
