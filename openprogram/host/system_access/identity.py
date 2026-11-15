"""Track verified native executor identities and recover stale grants explicitly."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import json
import os
from copy import deepcopy
from pathlib import Path
import re
import subprocess
import sys

_APP = Path('/Applications/OpenProgram.app')
_RUNTIME_APP = _APP / 'Contents/Resources/runtime/OpenProgram.app'
_BUNDLE_ID = 'ai.openprogram.desktop'
_RUNTIME_BUNDLE_ID = 'ai.openprogram.runtime'
def _registry_targets() -> dict[str, tuple[Path, str]]:
    """Resolve identity targets from the system-access capability registry."""
    try:
        from openprogram.host.system_access import capability_identity_targets
        return {
            capability: (Path(metadata['application']), metadata['bundle_id'])
            for capability, metadata in capability_identity_targets().items()
        }
    except Exception:
        # Import-time fallback keeps legacy CLI imports usable during package
        # bootstrap; the normal worker always resolves the registry above.
        return {
            'screen_recording': (_APP, _BUNDLE_ID),
            'accessibility': (_RUNTIME_APP, _RUNTIME_BUNDLE_ID),
        }


_CAPABILITY_TARGETS = _registry_targets()


def _managed_worker() -> bool:
    expected = _APP / 'Contents/Resources/runtime/OpenProgram.app/Contents/MacOS/OpenProgram'
    return (Path(sys.executable).absolute() == expected and
            sys.argv[-2:] == ['worker', 'run'])


@lru_cache(maxsize=4)
def _verified_identity(stamp: tuple, app: Path = _APP, bundle_id: str = _BUNDLE_ID) -> dict | None:
    try:
        check = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)],
                               capture_output=True, timeout=5)
        if check.returncode:
            return None
        result = subprocess.run(['/usr/bin/codesign', '-dvvv', str(app)],
                                capture_output=True, text=True, timeout=5)
        fields = dict(line.split('=', 1) for line in result.stderr.splitlines() if '=' in line)
        requirement = ''
        req = subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(app)],
                             capture_output=True, text=True, timeout=5)
        for line in req.stderr.splitlines():
            if line.startswith('designated => '):
                requirement = line.removeprefix('designated => ').strip()
                break
        digest = fields.get('CDHash', '')
        if (result.returncode or req.returncode or fields.get('Identifier') != bundle_id or
                not requirement or not re.fullmatch(r'[a-f0-9]{40,64}', digest)):
            return None
        return {'bundle_id': bundle_id, 'requirement': requirement, 'hash': digest}
    except (OSError, subprocess.SubprocessError):
        return None


def _identity_for(app: Path, bundle_id: str) -> dict | None:
    try:
        if app.is_symlink():
            return None
        paths = [app / 'Contents/MacOS/OpenProgram', app / 'Contents/_CodeSignature/CodeResources']
        stamp = tuple((s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                      for s in (p.stat() for p in paths))
        return _verified_identity(stamp, app, bundle_id)
    except OSError:
        return None


def _app_identity() -> dict | None:
    return _identity_for(_APP, _BUNDLE_ID)


def _runtime_identity() -> dict | None:
    return _identity_for(_RUNTIME_APP, _RUNTIME_BUNDLE_ID)


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
        # Nested capability receipts and reset markers must be compared by
        # value, otherwise in-place updates are invisible to the commit step.
        original = deepcopy(state)
        yield state
        if state != original:
            payload = json.dumps(state, sort_keys=True).encode()
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            os.ftruncate(fd, len(payload))
            os.fsync(fd)
    finally:
        os.close(fd)


def _valid_grant(value: object, *, capability: str, legacy: bool = False) -> bool:
    expected = _CAPABILITY_TARGETS.get(capability, (None, None))[1]
    return (isinstance(value, dict) and value.get('bundle_id') == expected and
            ((isinstance(value.get('requirement'), str) and bool(value['requirement'])) or
             (legacy and isinstance(value.get('hash'), str))) and
            (not value.get('hash') or
             (isinstance(value.get('hash'), str) and
              re.fullmatch(r'[a-f0-9]{40,64}', value['hash']) is not None)))


def _identity_for_capability(capability: str) -> dict | None:
    target = _CAPABILITY_TARGETS.get(capability)
    if target is None:
        return None
    role = None
    try:
        from openprogram.host.system_access import capability_identity_targets
        role = capability_identity_targets().get(capability, {}).get('role')
    except Exception:
        return None
    if role == 'runtime':
        return _runtime_identity()
    if role == 'containing_app':
        return _app_identity()
    return None


def observe(row: dict) -> dict:
    """Attach recovery advice, recording only the actual worker's successful check."""
    capability = row.get('id')
    if capability not in _CAPABILITY_TARGETS or not _managed_worker():
        return row
    identity = _identity_for_capability(capability)
    if identity is None:
        return row
    try:
        with _receipt() as state:
            if row['status'] == 'granted':
                grants = state.setdefault('granted_by_capability', {})
                if not isinstance(grants, dict):
                    grants = {}
                    state['granted_by_capability'] = grants
                grants[capability] = identity
            elif row['status'] == 'not_granted':
                grants = state.get('granted_by_capability', {})
                old = grants.get(capability) if isinstance(grants, dict) else None
                # Read the pre-requirement screen receipt for compatibility.
                if old is None and capability == 'screen_recording':
                    old = state.get('granted')
                if (_valid_grant(old, capability=capability, legacy=capability == 'screen_recording') and
                        ((old.get('requirement') and old.get('requirement') != identity.get('requirement')) or
                         (not old.get('requirement') and old.get('hash') != identity.get('hash')))):
                    return {**row, 'recovery': 'reauthorize_after_update',
                            'detail': 'The signed OpenProgram executor changed after authorization was granted. Renew system authorization.',
                            'instruction': 'Open system authorization to renew access for the updated OpenProgram application.'}
    except (OSError, ValueError):
        pass  # Missing/unwritable evidence cannot justify resetting a grant.
    return row


def _reset_screen_grant() -> None:
    _reset_capability('screen_recording')


def _reset_capability(capability: str) -> None:
    target = _CAPABILITY_TARGETS.get(capability)
    if target is None:
        raise ValueError(f'Unknown system capability: {capability}')
    try:
        from openprogram.host.system_access import capability_identity_targets
        service = capability_identity_targets()[capability]['tcc_service']
    except Exception as exc:
        raise ValueError(f'Unknown system capability: {capability}') from exc
    bundle_id = target[1]
    result = subprocess.run(['/usr/bin/tccutil', 'reset', service, bundle_id],
                            capture_output=True, timeout=5)
    if result.returncode:
        raise RuntimeError('Could not renew OpenProgram system authorization. Open System Settings to remove and add OpenProgram.')


def prepare_request(row: dict) -> None:
    """Consume a verified stale receipt once, before the existing native request."""
    capability = row.get('id')
    if (capability not in _CAPABILITY_TARGETS or row.get('status') != 'not_granted' or
            row.get('recovery') != 'reauthorize_after_update' or not _managed_worker()):
        return
    identity = _identity_for_capability(capability)
    if identity is None:
        return
    try:
        with _receipt() as state:
            grants = state.get('granted_by_capability', {})
            old = grants.get(capability) if isinstance(grants, dict) else None
            if old is None and capability == 'screen_recording':
                old = state.get('granted')
            marker = state.setdefault('reset_for', {})
            if not isinstance(marker, dict):
                marker = {}
                state['reset_for'] = marker
            if (not _valid_grant(old, capability=capability, legacy=capability == 'screen_recording') or
                    (old.get('requirement') and old.get('requirement') == identity.get('requirement')) or
                    (not old.get('requirement') and old.get('hash') == identity.get('hash')) or
                    (isinstance(marker.get(capability), dict) and
                     marker[capability].get('bundle_id') == identity.get('bundle_id') and
                     marker[capability].get('requirement') == identity.get('requirement')) or
                    (capability == 'screen_recording' and
                     marker.get('bundle_id') == identity.get('bundle_id') and
                     not marker.get('requirement') and marker.get('hash') == identity.get('hash'))):
                return
            marker[capability] = identity
            # Preserve the original screen receipt shape for older readers.
            if capability == 'screen_recording':
                marker.update(identity)
        # Persist before the OS call: failure or a crash must not cause a reset loop.
    except (OSError, ValueError) as exc:
        raise RuntimeError('Could not save authorization recovery. Open System Settings to renew OpenProgram access.') from exc
    try:
        # Keep the legacy screen hook as the compatibility seam for existing
        # callers and tests; accessibility always uses its fixed target.
        if capability == 'screen_recording':
            _reset_screen_grant()
        else:
            _reset_capability(capability)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Could not renew OpenProgram system authorization. Open System Settings to remove and add OpenProgram.') from exc
