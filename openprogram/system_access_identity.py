"""Recover obsolete ad-hoc ScreenCapture grants at explicit local setup only."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys

_APP = Path('/Applications/OpenProgram.app')
_BUNDLE_ID = 'ai.openprogram.desktop'


def _managed_worker() -> bool:
    expected = _APP / 'Contents/Resources/runtime/OpenProgram.app/Contents/MacOS/OpenProgram'
    return (Path(sys.executable).absolute() == expected and
            sys.argv[-2:] == ['worker', 'run'])


@lru_cache(maxsize=4)
def _verified_identity(stamp: tuple) -> dict | None:
    try:
        check = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(_APP)],
                               capture_output=True, timeout=5)
        if check.returncode:
            return None
        result = subprocess.run(['/usr/bin/codesign', '-dvvv', str(_APP)],
                                capture_output=True, text=True, timeout=5)
        fields = dict(line.split('=', 1) for line in result.stderr.splitlines() if '=' in line)
        digest = fields.get('CDHash', '')
        if (result.returncode or fields.get('Identifier') != _BUNDLE_ID or
                fields.get('Signature') != 'adhoc' or not re.fullmatch(r'[a-f0-9]{40,64}', digest)):
            return None
        return {'bundle_id': _BUNDLE_ID, 'hash': digest}
    except (OSError, subprocess.SubprocessError):
        return None


def _app_identity() -> dict | None:
    try:
        if _APP.is_symlink():
            return None
        paths = [_APP / 'Contents/MacOS/OpenProgram', _APP / 'Contents/_CodeSignature/CodeResources']
        stamp = tuple((s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                      for s in (p.stat() for p in paths))
        return _verified_identity(stamp)
    except OSError:
        return None


def _state_path() -> Path:
    # OS grants are per-user, not per CLI profile.
    return Path.home() / '.openprogram' / 'system-access-grant.json'


@contextmanager
def _receipt():
    import fcntl
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_size > 4096:
            raise OSError('Invalid system access receipt')
        raw = os.read(fd, 4096)
        state = json.loads(raw) if raw else {}
        if not isinstance(state, dict):
            raise ValueError('Invalid system access receipt')
        original = dict(state)
        yield state
        if state != original:
            payload = json.dumps(state, sort_keys=True).encode()
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            os.ftruncate(fd, len(payload))
            os.fsync(fd)
    finally:
        os.close(fd)


def _valid_grant(value: object) -> bool:
    return (isinstance(value, dict) and value.get('bundle_id') == _BUNDLE_ID and
            isinstance(value.get('hash'), str) and
            re.fullmatch(r'[a-f0-9]{40,64}', value['hash']) is not None)


def observe(row: dict) -> dict:
    """Attach recovery advice, recording only the actual worker's successful check."""
    if row.get('id') != 'screen_recording' or not _managed_worker():
        return row
    identity = _app_identity()
    if identity is None:
        return row
    try:
        with _receipt() as state:
            if row['status'] == 'granted':
                state.clear()
                state.update(granted=identity)
            elif row['status'] == 'not_granted':
                old = state.get('granted')
                if _valid_grant(old) and old['hash'] != identity['hash']:
                    return {**row, 'recovery': 'reauthorize_after_update',
                            'detail': 'The application changed after recording access was granted. Renew system authorization.',
                            'instruction': 'Open system authorization to renew access for the updated OpenProgram application.'}
    except (OSError, ValueError):
        pass  # Missing/unwritable evidence cannot justify resetting a grant.
    return row


def _reset_screen_grant() -> None:
    result = subprocess.run(['/usr/bin/tccutil', 'reset', 'ScreenCapture', _BUNDLE_ID],
                            capture_output=True, timeout=5)
    if result.returncode:
        raise RuntimeError('Could not renew OpenProgram recording authorization. Open System Settings to remove and add OpenProgram.')


def prepare_request(row: dict) -> None:
    """Consume a verified stale receipt once, before the existing native request."""
    if (row.get('id') != 'screen_recording' or row.get('status') != 'not_granted' or
            row.get('recovery') != 'reauthorize_after_update' or not _managed_worker()):
        return
    identity = _app_identity()
    if identity is None:
        return
    try:
        with _receipt() as state:
            old = state.get('granted')
            if (not _valid_grant(old) or old['hash'] == identity['hash'] or
                    state.get('reset_for') == identity):
                return
            state['reset_for'] = identity
        # Persist before the OS call: failure or a crash must not cause a reset loop.
    except (OSError, ValueError) as exc:
        raise RuntimeError('Could not save authorization recovery. Open System Settings to renew OpenProgram access.') from exc
    try:
        _reset_screen_grant()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Could not renew OpenProgram recording authorization. Open System Settings to remove and add OpenProgram.') from exc
