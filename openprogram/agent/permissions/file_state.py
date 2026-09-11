"""Persist file preconditions with an approval, and check before mutation."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import stat

from openprogram._compat import flock, LOCK_EX, is_link_metadata

_active_files: ContextVar[dict | None] = ContextVar("approval_files", default=None)


def fingerprint(path: str) -> dict:
    resolved = os.path.realpath(path)
    try:
        with open(path, 'rb') as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError('File changed while checking it')
        return {'resolved': resolved, 'sha256': digest, 'device': after.st_dev, 'inode': after.st_ino}
    except FileNotFoundError:
        return {'resolved': resolved, 'missing': True}


def capture(tool: str, args: dict) -> dict[str, dict]:
    from openprogram.worktree.path_resolve import resolve_path
    if tool in {'edit', 'write'}:
        paths = [resolve_path(str(args['file_path']))[0]]
    elif tool == 'apply_patch':
        from openprogram.programs.tools.files.apply_patch import _parse_sections
        paths = [path for _, path, _ in _parse_sections(str(args['patch']))]
    else:
        return {}
    return {os.path.abspath(path): fingerprint(path) for path in paths}


def validate(files: dict[str, dict]) -> None:
    for path, expected in files.items():
        actual = fingerprint(path)
        if actual != expected:
            if actual.get('missing') and not expected.get('missing'):
                raise ValueError(f'File no longer exists: {path}. Nothing was changed. Do not recreate it using this approval.')
            raise ValueError(f'File state changed: {path}. Nothing was changed. Read its current contents and request fresh approval for any new operation.')


@contextmanager
def file_scope():
    token = _active_files.set(current_files())
    try:
        yield
    finally:
        _active_files.reset(token)


def current_files() -> dict[str, dict]:
    active = _active_files.get()
    if active is not None:
        return active
    from openprogram.agent.run_control import get_preapproved_wait_id
    wait_id = get_preapproved_wait_id()
    if not wait_id:
        return {}
    from openprogram.execution import default_store
    from openprogram.execution.waits import DurableWaitStore
    wait = DurableWaitStore(default_store()).get_wait(wait_id)
    if wait is None:
        raise ValueError('Approval is unavailable')
    return dict(wait.request.get('file_preconditions') or {})


def check_current(path: str | None = None) -> None:
    files = current_files()
    if path is not None:
        key = os.path.abspath(path)
        files = {key: files[key]} if key in files else {}
    validate(files)


def write_checked(path: str, content: str) -> None:
    """Do not truncate a changed file or recreate a deleted approved target."""
    files = current_files()
    expected = files.get(os.path.abspath(path))
    if expected is None:
        Path(path).write_text(content, encoding='utf-8')
        return
    validate({path: expected})
    if not expected.get('missing'):
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or is_link_metadata(info):
            raise ValueError('Approved target must be a regular file')
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    flags |= os.O_CREAT | os.O_EXCL if expected.get('missing') else 0
    fd = os.open(path, flags, 0o666)
    with os.fdopen(fd, 'r+b') as stream:
        # Serialize approved writers before checking the opened file again.
        flock(stream.fileno(), LOCK_EX)
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('Approved target must be a regular file')
        if not expected.get('missing'):
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if (digest, st.st_dev, st.st_ino) != (expected['sha256'], expected['device'], expected['inode']):
                raise ValueError('File changed before writing; operation was not applied')
        elif st.st_size:
            raise ValueError('File changed before writing; operation was not applied')
        current = os.lstat(path)
        if (not stat.S_ISREG(st.st_mode) or is_link_metadata(current)
                    or (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino)):
            raise ValueError('Approved target must remain a regular file')
        if os.path.realpath(path) != expected['resolved']:
            raise ValueError('File path changed before writing; operation was not applied')
        stream.seek(0)
        stream.write(content.encode('utf-8'))
        stream.truncate()
        stream.flush()
    files[os.path.abspath(path)] = fingerprint(path)
