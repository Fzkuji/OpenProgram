"""Live execution-host access diagnostics. Reading status never requests access.

OS access is independent of tool approval. Optional capabilities are advisory;
absence must not make a headless installation unhealthy.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import socket
from pathlib import Path
import subprocess
import sys
import threading
import time

_REQUEST_LOCK = threading.Lock()
_log = logging.getLogger(__name__)
_NATIVE_PROBE_SCHEMA = 1
_NATIVE_PROBE_TIMEOUT = 5.0
_NATIVE_PROBE_MAX_OUTPUT = 16 * 1024
_NATIVE_PROBE_SCRIPT = (
    'from openprogram.system_access import _native_probe_entry; '
    '_native_probe_entry()'
)
_MAC = {
    'screen_recording': ('Screen recording', 'Quartz', 'CGPreflightScreenCaptureAccess',
                         'Privacy & Security > Screen & System Audio Recording'),
    'accessibility': ('Desktop control', 'ApplicationServices', 'AXIsProcessTrusted',
                       'Privacy & Security > Accessibility'),
}


def _mac_row(capability: str, *, status: str, detail: str) -> dict:
    label, _, _, setting = _MAC[capability]
    row = {'id': capability, 'label': label, 'status': 'unknown', 'optional': True,
           'instruction': f'On this execution Mac, open System Settings > {setting}. '
                          'Authorize the executing application shown by macOS. '
                          'Return here to check again; if macOS requires it, restart that application.',
           'can_request': False}
    if status in {'granted', 'not_granted', 'unavailable'}:
        row.update(status=status, can_request=status == 'not_granted')
    row['detail'] = detail
    return row


def _native_identity() -> dict[str, str]:
    identity = {'executable': str(Path(sys.executable).resolve())}
    try:
        bundle = importlib.import_module('Foundation').NSBundle.mainBundle()
        identity['application'] = str(bundle.objectForInfoDictionaryKey_('CFBundleName') or '')
        identity['bundle_id'] = str(bundle.bundleIdentifier() or '')
        identity['bundle_path'] = str(bundle.bundlePath() or '')
    except Exception:
        _log.debug('native application identity unavailable', exc_info=True)
    return identity


def _native_probe_entry() -> None:
    """Private child entry for nonprompting native checks and explicit requests."""
    request = None
    if len(sys.argv) == 3 and sys.argv[1] == '--request':
        request = sys.argv[2]
        if request not in _MAC:
            raise ValueError('unsupported native capability request')
    elif len(sys.argv) != 1:
        raise ValueError('invalid native probe arguments')

    rows = []
    for capability, (_, module, method, _) in _MAC.items():
        try:
            native = importlib.import_module(module)
            if request == capability:
                if capability == 'screen_recording':
                    native.CGRequestScreenCaptureAccess()
                else:
                    native.AXIsProcessTrustedWithOptions(
                        {native.kAXTrustedCheckOptionPrompt: True}
                    )
            granted = bool(getattr(native, method)())
            rows.append({
                'id': capability,
                'status': 'granted' if granted else 'not_granted',
                'detail': 'Authorized in the fresh named executor.' if granted
                else 'Not authorized in the fresh named executor.',
            })
        except ImportError as exc:
            rows.append({
                'id': capability,
                'status': 'unavailable',
                'detail': f'Native permission dependencies are missing ({type(exc).__name__}).',
            })
        except Exception as exc:
            rows.append({
                'id': capability,
                'status': 'unknown',
                'detail': f'Fresh native check failed ({type(exc).__name__}).',
            })
    print(json.dumps({
        'schema': _NATIVE_PROBE_SCHEMA,
        'identity': _native_identity(),
        'capabilities': rows,
    }, sort_keys=True), flush=True)


def _native_probe(request_capability: str | None = None) -> dict | None:
    executable = os.path.abspath(sys.executable)
    identity_executable = str(Path(executable).resolve())
    command = [executable, '-I', '-B', '-c', _NATIVE_PROBE_SCRIPT]
    if request_capability is not None:
        if request_capability not in _MAC:
            raise ValueError('unsupported native capability request')
        command.extend(['--request', request_capability])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_NATIVE_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.debug('fresh native access probe failed to start: %s', exc)
        return None
    stdout = result.stdout if isinstance(result.stdout, str) else ''
    if result.returncode != 0 or len(stdout.encode('utf-8', 'replace')) > _NATIVE_PROBE_MAX_OUTPUT:
        _log.debug('fresh native access probe exited unsuccessfully: %s', result.returncode)
        return None
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError, RecursionError):
        _log.debug('fresh native access probe returned malformed JSON')
        return None
    if not isinstance(payload, dict) or payload.get('schema') != _NATIVE_PROBE_SCHEMA:
        return None
    identity = payload.get('identity')
    if not isinstance(identity, dict) or identity.get('executable') != identity_executable:
        _log.debug('fresh native access probe identity did not match the executor')
        return None
    capabilities = payload.get('capabilities')
    if not isinstance(capabilities, list):
        return None
    parsed = {}
    for row in capabilities:
        if not isinstance(row, dict) or row.get('id') not in _MAC or row.get('id') in parsed:
            return None
        if row.get('status') not in {'granted', 'not_granted', 'unknown', 'unavailable'}:
            return None
        parsed[row['id']] = {
            'status': row['status'],
            'detail': str(row.get('detail') or 'Fresh native check returned no detail.'),
        }
    if set(parsed) != set(_MAC):
        return None
    return {'identity': identity, 'capabilities': parsed}


def _mac_status(capability: str) -> dict:
    probe = _native_probe()
    if probe is None:
        return _mac_row(
            capability, status='unknown',
            detail='The fresh native permission check failed; authorization is unknown.',
        )
    current = probe['capabilities'].get(capability)
    if current is None:
        return _mac_row(capability, status='unknown', detail='Native authorization is unknown.')
    return _mac_row(capability, **current)


def report() -> dict:
    """Return fresh advisory status for this process, not the connecting client."""
    system = platform.system()
    if system == 'Darwin':
        probe = _native_probe()
        rows = []
        for key in _MAC:
            current = probe['capabilities'].get(key) if probe is not None else None
            rows.append(_mac_row(
                key,
                status=current['status'] if current is not None else 'unknown',
                detail=current['detail'] if current is not None
                else 'The fresh native permission check failed; authorization is unknown.',
            ))
    elif system == 'Linux':
        wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
        display = bool(os.environ.get('DISPLAY'))
        rows = [{
            'id': 'desktop_session', 'label': 'Desktop access', 'optional': True,
            'status': 'unsupported' if wayland else ('unknown' if display else 'unavailable'),
            'detail': ('The current desktop input backend does not provide Wayland portal authorization.' if wayland
                       else 'An X11 display is configured; access must be verified by the desktop backend.' if display
                       else 'No graphical desktop is configured for this process.'),
            'instruction': ('Use a supported X11 desktop session or a browser/VM backend. Do not disable desktop security.' if wayland
                            else 'Run the desktop worker in the intended signed-in graphical session; browser and ordinary CLI tasks do not require desktop access.'),
            'can_request': False,
        }]
    elif system == 'Windows':
        # Session names and administrator membership do not prove desktop access.
        rows = [{
            'id': 'desktop_session', 'label': 'Desktop access', 'optional': True,
            'status': 'unknown', 'detail': 'Desktop access is verified when the target is opened.',
            'instruction': 'Run in the intended signed-in desktop session. Locked screens, UAC secure desktop and higher-privilege applications may be inaccessible. Do not run the whole application as administrator.',
            'can_request': False,
        }]
    else:
        rows = [{'id': 'desktop_session', 'label': 'Desktop access', 'optional': True,
                 'status': 'unsupported', 'detail': 'No desktop permission backend for this platform.',
                 'instruction': 'Use a supported browser or remote VM backend.', 'can_request': False}]
    application = ''
    if system == 'Darwin':
        try:
            bundle = importlib.import_module('Foundation').NSBundle.mainBundle()
            application = str(bundle.objectForInfoDictionaryKey_('CFBundleName') or '')
        except Exception:
            _log.debug("bundle name lookup unavailable", exc_info=True)
    return {'platform': system, 'application': application, 'host': socket.gethostname(), 'executable': sys.executable,
            'pid': os.getpid(), 'checked_at': time.time(), 'capabilities': rows}


def access_manifest_for_tool(tool_name: str, args: dict | None) -> dict | None:
    """Return a pre-effect durable wait manifest for local desktop GUI use.

    Browser and VM GUI paths have their own access boundary.  Only the
    macOS desktop executor has explicit OS capabilities that can be waited on
    before the GUI agent plans or dispatches an effect.
    """
    if str(tool_name) != 'gui_agent' or not isinstance(args, dict):
        return None
    surface = str(args.get('surface') or '').strip().lower()
    if surface not in {'', 'desktop'} or args.get('vm_url'):
        return None
    # The bridge treats a backend without an explicit desktop surface as the
    # browser execution path.
    if not surface and args.get('backend'):
        return None
    snapshot = report()
    if snapshot.get('platform') != 'Darwin':
        return None
    capabilities = [dict(row) for row in snapshot.get('capabilities', ())
                    if isinstance(row, dict) and row.get('id') in _MAC]
    missing = [row for row in capabilities if row.get('status') != 'granted']
    if not missing:
        return None
    required = [str(row['id']) for row in missing]
    return {
        'kind': 'system_access',
        'required_capabilities': required,
        'capabilities': capabilities,
        'prompt': 'Desktop access is required before this GUI task can run.',
        'options': [], 'multi': False, 'allow_custom': False,
        'detail': 'Authorize the required capabilities on the execution computer, then return to OpenProgram.',
        'schema': {}, 'questions': [], 'timeout': None,
        'request_metadata': {
            'tool': 'gui_agent', 'args': dict(args),
            'required_capabilities': required,
            'capabilities': capabilities,
            'source': 'system_access',
        },
        'policy_snapshot': {
            'version': 1, 'kind': 'system_access', 'on_grant': 'continue',
        },
    }


def _open_settings(capability: str) -> bool:
    """Open only the fixed native page after an explicit user setup action."""
    panes = {'screen_recording': 'Privacy_ScreenCapture', 'accessibility': 'Privacy_Accessibility'}
    try:
        appkit = importlib.import_module('AppKit')
        foundation = importlib.import_module('Foundation')
        url = foundation.NSURL.URLWithString_('x-apple.systempreferences:com.apple.preference.security?' + panes[capability])
        return bool(appkit.NSWorkspace.sharedWorkspace().openURL_(url))
    except Exception:
        return False


def request_access(capability: str) -> dict:
    """Explicit local-user setup only; never call from a probe or a model tool."""
    if platform.system() != 'Darwin' or capability not in _MAC:
        raise ValueError('No native permission request for this capability on this platform.')
    if not _REQUEST_LOCK.acquire(blocking=False):
        raise RuntimeError('A system permission request is already in progress.')
    try:
        before = _mac_status(capability)
        if before['status'] == 'granted' or not before['can_request']:
            return before
        _native_probe(request_capability=capability)
        after = _mac_status(capability)
        if after['status'] != 'granted':
            after['settings_opened'] = _open_settings(capability)
        return after
    finally:
        _REQUEST_LOCK.release()


def doctor_rows() -> list[dict]:
    """Optional capability advice never blocks installation or upgrades."""
    snapshot = report()
    return [{'id': 'system_access:' + row['id'], 'ok': True,
             'label': row['label'], 'status': row['status'], 'optional': True,
             'detail': f"{row['status']}: {row['detail']}" +
                       ('' if row['status'] == 'granted' else ' ' + row['instruction'])}
            for row in snapshot['capabilities']]
