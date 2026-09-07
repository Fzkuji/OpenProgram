"""Live execution-host access diagnostics. Reading status never requests access.

OS access is independent of tool approval. Optional capabilities are advisory;
absence must not make a headless installation unhealthy.
"""
from __future__ import annotations

import importlib
import os
import platform
import socket
import sys
import threading
import time

_REQUEST_LOCK = threading.Lock()
_MAC = {
    'screen_recording': ('Screen recording', 'Quartz', 'CGPreflightScreenCaptureAccess',
                         'Privacy & Security > Screen & System Audio Recording'),
    'accessibility': ('Desktop control', 'ApplicationServices', 'AXIsProcessTrusted',
                       'Privacy & Security > Accessibility'),
}


def _mac_status(capability: str) -> dict:
    label, module, method, setting = _MAC[capability]
    row = {'id': capability, 'label': label, 'status': 'unknown', 'optional': True,
           'instruction': f'On this execution Mac, open System Settings > {setting}. '
                          'Authorize the executing application shown by macOS. '
                          'Return here to check again; if macOS requires it, restart that application.',
           'can_request': False}
    try:
        granted = bool(getattr(importlib.import_module(module), method)())
        row.update(status='granted' if granted else 'not_granted', can_request=not granted)
        row['detail'] = 'Authorized for this process.' if granted else 'Not authorized for this process.'
    except ImportError:
        row.update(status='unavailable', detail='Native permission dependencies are missing. Repair the installation.')
    except Exception as exc:
        row['detail'] = f'Permission check failed ({type(exc).__name__}); authorization is unknown.'
    return row


def report() -> dict:
    """Return fresh advisory status for this process, not the connecting client."""
    system = platform.system()
    if system == 'Darwin':
        rows = [_mac_status(key) for key in _MAC]
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
    return {'platform': system, 'host': socket.gethostname(), 'executable': sys.executable,
            'pid': os.getpid(), 'checked_at': time.time(), 'capabilities': rows}


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
        if capability == 'screen_recording':
            importlib.import_module('Quartz').CGRequestScreenCaptureAccess()
        else:
            ax = importlib.import_module('ApplicationServices')
            ax.AXIsProcessTrustedWithOptions({ax.kAXTrustedCheckOptionPrompt: True})
        return _mac_status(capability)
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
