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
from dataclasses import dataclass
from types import MappingProxyType

_REQUEST_LOCK = threading.Lock()
_log = logging.getLogger(__name__)
_NATIVE_PROBE_SCHEMA = 1
SYSTEM_ACCESS_SCHEMA = 2
SYSTEM_ACCESS_VERSION = 2
_NATIVE_PROBE_TIMEOUT = 5.0
_NATIVE_REQUEST_TIMEOUT = 120.0
_NATIVE_PROBE_MAX_OUTPUT = 16 * 1024
_NATIVE_PROBE_SCRIPT = (
    'from openprogram.host.system_access import _native_probe_entry; '
    '_native_probe_entry()'
)
@dataclass(frozen=True)
class CapabilitySpec:
    """The single protocol definition for a host capability.

    A spec is deliberately declarative.  A missing implementation is reported
    as ``unknown``/``unsupported`` and is never interpreted as a grant.
    """

    id: str
    label: str
    label_zh: str
    category: str
    subject: str
    request_mode: str
    platforms: tuple[str, ...] = ('Darwin',)
    optional: bool = True
    operations: tuple[str, ...] = ()
    module: str | None = None
    check_method: str | None = None
    request_method: str | None = None
    request_style: str = 'none'
    settings_pane: str | None = None
    settings_label: str = 'Privacy & Security'
    usage_key: str | None = None
    entitlement: str | None = None
    identity_role: str | None = None
    identity_application: str | None = None
    identity_bundle_id: str | None = None
    tcc_service: str | None = None
    package_declarations: tuple[str, ...] = ()
    usage_descriptions: tuple[tuple[str, str], ...] = ()


# Keep the two legacy rows first: old clients display the first row and their
# wire shape remains unchanged.  All other host capabilities are represented
# here even when the current platform backend is declaration-only.
_CAPABILITY_SPECS = (
    CapabilitySpec(
        'screen_recording', 'Screen recording', '屏幕录制', 'desktop', 'runtime',
        'native', operations=('computer_use:desktop',), module='Quartz',
        check_method='CGPreflightScreenCaptureAccess',
        request_method='CGRequestScreenCaptureAccess',
        settings_pane='Privacy_ScreenCapture',
        settings_label='Screen & System Audio Recording',
        # Meetings records the call's sound through the same grant: the
        # worker's ScreenCaptureKit audio stream needs no other declaration.
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime', tcc_service='ScreenCapture',
    ),
    CapabilitySpec(
        'accessibility', 'Desktop control', '桌面控制', 'desktop', 'runtime', 'native',
        operations=('computer_use:desktop',), module='ApplicationServices',
        check_method='AXIsProcessTrusted',
        request_method='AXIsProcessTrustedWithOptions',
        request_style='accessibility_prompt',
        settings_pane='Privacy_Accessibility', settings_label='Accessibility',
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime', tcc_service='Accessibility',
    ),
    CapabilitySpec(
        'apple_events', 'Automation', '自动化', 'integrations', 'target_app', 'targeted',
        operations=('apple_events:*',), settings_pane='Privacy_Automation',
        settings_label='Automation', usage_key='NSAppleEventsUsageDescription',
        entitlement='com.apple.security.automation.apple-events',
        package_declarations=('NSAppleEventsUsageDescription', 'com.apple.security.automation.apple-events'),
        usage_descriptions=(('NSAppleEventsUsageDescription', 'OpenProgram controls applications on your Mac to carry out tasks you request.'),),
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime',
    ),
    CapabilitySpec(
        'calendar', 'Calendar', '日历', 'integrations', 'runtime', 'native',
        operations=('calendar:*',), module='EventKit',
        check_method='authorizationStatusForEntityType_',
        request_method='requestFullAccessToEventsWithCompletion_',
        request_style='eventkit_events', settings_pane='Privacy_Calendars',
        settings_label='Calendars', usage_key='NSCalendarsUsageDescription',
        tcc_service='Calendar',
        package_declarations=(
            'NSCalendarsUsageDescription', 'NSCalendarsFullAccessUsageDescription',
            'com.apple.security.personal-information.calendars',
        ),
        usage_descriptions=(
            ('NSCalendarsUsageDescription', 'OpenProgram accesses your calendars to carry out tasks you request.'),
            ('NSCalendarsFullAccessUsageDescription', 'OpenProgram reads and updates calendar events when you request it.'),
        ),
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime',
    ),
    CapabilitySpec(
        'reminders', 'Reminders', '提醒事项', 'integrations', 'runtime', 'native',
        operations=('reminders:*',), module='EventKit',
        check_method='authorizationStatusForEntityType_',
        request_method='requestFullAccessToRemindersWithCompletion_',
        request_style='eventkit_reminders', settings_pane='Privacy_Reminders',
        settings_label='Reminders', usage_key='NSRemindersUsageDescription',
        tcc_service='Reminders',
        package_declarations=('NSRemindersUsageDescription', 'NSRemindersFullAccessUsageDescription'),
        usage_descriptions=(
            ('NSRemindersUsageDescription', 'OpenProgram accesses reminders to carry out tasks you request.'),
            ('NSRemindersFullAccessUsageDescription', 'OpenProgram reads and updates reminders when you request it.'),
        ),
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime',
    ),
    CapabilitySpec(
        'file_read', 'File read', '文件读取', 'storage', 'user_selected_path', 'operation',
        platforms=('Darwin', 'Linux', 'Windows'), operations=('file:read',),
        settings_label='Files and Folders',
    ),
    CapabilitySpec(
        'file_write', 'File write', '文件写入', 'storage', 'user_selected_path', 'operation',
        platforms=('Darwin', 'Linux', 'Windows'), operations=('file:write',),
        settings_label='Files and Folders',
    ),
    CapabilitySpec(
        'microphone', 'Microphone', '麦克风', 'media', 'runtime', 'native',
        operations=('microphone:*',), module='AVFoundation',
        check_method='authorizationStatusForMediaType_',
        request_method='requestAccessForMediaType_completionHandler_',
        request_style='avfoundation_audio', settings_pane='Privacy_Microphone',
        settings_label='Microphone', tcc_service='Microphone',
        package_declarations=('NSMicrophoneUsageDescription',),
        usage_descriptions=(('NSMicrophoneUsageDescription', 'OpenProgram accesses the microphone when you request audio tasks.'),),
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime',
    ),
    CapabilitySpec(
        'camera', 'Camera', '摄像头', 'media', 'runtime', 'native',
        operations=('camera:*',), module='AVFoundation',
        check_method='authorizationStatusForMediaType_',
        request_method='requestAccessForMediaType_completionHandler_',
        request_style='avfoundation_video', settings_pane='Privacy_Camera',
        settings_label='Camera', tcc_service='Camera',
        package_declarations=('NSCameraUsageDescription', 'com.apple.security.device.camera'),
        usage_descriptions=(('NSCameraUsageDescription', 'OpenProgram accesses the camera when you request camera tasks.'),),
        identity_role='runtime',
        identity_application='/Applications/OpenProgram.app/Contents/Resources/runtime/OpenProgram.app',
        identity_bundle_id='ai.openprogram.runtime',
    ),
)

CAPABILITY_REGISTRY = MappingProxyType({spec.id: spec for spec in _CAPABILITY_SPECS})


def capability_registry() -> tuple[CapabilitySpec, ...]:
    """Return the immutable registry in protocol order."""
    return _CAPABILITY_SPECS


def capability_spec(capability: str) -> CapabilitySpec:
    try:
        return CAPABILITY_REGISTRY[str(capability)]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Unknown system capability: {capability}') from exc


def package_usage_declarations() -> dict[str, str]:
    """Return the usage strings required by the registry for macOS bundles."""
    declarations: dict[str, str] = {}
    expected_keys = {
        key for spec in capability_registry() for key in spec.package_declarations
        if key.startswith('NS')
    }
    for spec in capability_registry():
        for key, text in spec.usage_descriptions:
            if key in declarations and declarations[key] != text:
                raise RuntimeError(f'Conflicting usage description for registry key: {key}')
            declarations[key] = text
    if set(declarations) != expected_keys:
        raise RuntimeError(
            f'Registry package usage metadata is incomplete: '
            f'expected={sorted(expected_keys)}, declared={sorted(declarations)}'
        )
    return {key: declarations[key] for key in sorted(declarations)}


def package_entitlements() -> frozenset[str]:
    """Return registry-declared entitlements expected in the signed bundle."""
    return frozenset(
        key for spec in capability_registry() for key in spec.package_declarations
        if key.startswith('com.apple.')
    )


def validate_package_consistency(
    actual_usage: dict[str, str], actual_entitlements: set[str] | frozenset[str],
) -> None:
    """Fail on drift between registry declarations and a built macOS bundle."""
    expected_usage = package_usage_declarations()
    expected_entitlements = package_entitlements()
    if dict(actual_usage) != expected_usage:
        raise RuntimeError(
            f'Package usage declarations drift from registry: '
            f'expected={sorted(expected_usage)}, actual={sorted(actual_usage)}'
        )
    missing_entitlements = set(expected_entitlements) - set(actual_entitlements)
    if missing_entitlements:
        raise RuntimeError(
            f'Package entitlements drift from registry: '
            f'missing={sorted(missing_entitlements)}, actual={sorted(actual_entitlements)}'
        )


def validate_capability_registry() -> None:
    ids = [spec.id for spec in _CAPABILITY_SPECS]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise RuntimeError('System access capability ids must be unique and non-empty.')
    for spec in _CAPABILITY_SPECS:
        if spec.request_mode not in {'native', 'settings', 'targeted', 'operation', 'none'}:
            raise RuntimeError(f'Unknown request mode for {spec.id}.')
        if spec.subject not in {'containing_app', 'runtime', 'target_app', 'user_selected_path', 'provider_session'}:
            raise RuntimeError(f'Unknown identity subject for {spec.id}.')
        if spec.request_mode == 'native' and not (spec.module and spec.check_method):
            raise RuntimeError(f'Native capability {spec.id} has no nonprompting check.')
        if spec.request_mode == 'native' and not spec.request_method:
            raise RuntimeError(f'Native capability {spec.id} has no request method.')
        if spec.request_style not in {
            'none', 'accessibility_prompt', 'eventkit_events',
            'eventkit_reminders', 'avfoundation_audio', 'avfoundation_video',
        }:
            raise RuntimeError(f'Unknown native request style for {spec.id}.')
        if spec.request_mode in {'native', 'settings', 'targeted'} and not spec.settings_pane:
            raise RuntimeError(f'Capability {spec.id} has no settings destination.')
        if spec.identity_role and spec.identity_role not in {'containing_app', 'runtime'}:
            raise RuntimeError(f'Unknown identity role for {spec.id}.')
        if not spec.label or not spec.label_zh:
            raise RuntimeError(f'Capability {spec.id} must have English and Chinese labels.')
        declarations = set(spec.package_declarations)
        if spec.usage_key and spec.usage_key not in declarations:
            raise RuntimeError(f'Capability {spec.id} usage_key is missing from package declarations.')
        if spec.entitlement and spec.entitlement not in declarations:
            raise RuntimeError(f'Capability {spec.id} entitlement is missing from package declarations.')
        for operation in spec.operations:
            prefix, separator, action = operation.partition(':')
            if (not operation or not separator or not prefix or not action or
                    prefix not in {'computer_use', 'apple_events', 'calendar',
                                   'reminders', 'file', 'microphone', 'camera'}):
                raise RuntimeError(f'Invalid operation mapping for {spec.id}.')
    package_usage_declarations()


validate_capability_registry()

# Compatibility view used by the existing native probe tests and old callers.
_MAC = {
    spec.id: (spec.label, spec.module, spec.check_method, spec.settings_label)
    for spec in _CAPABILITY_SPECS if spec.request_mode == 'native'
}


def capability_identity_targets() -> dict[str, dict[str, str]]:
    """Return registry identity metadata without exposing mutable specs."""
    return {
        spec.id: {
            'application': spec.identity_application,
            'bundle_id': spec.identity_bundle_id,
            'role': spec.identity_role,
            'tcc_service': spec.tcc_service,
        }
        for spec in capability_registry()
        if spec.identity_application and spec.identity_bundle_id
    }


def _capability_row(spec: CapabilitySpec, *, status: str, detail: str) -> dict:
    """Build the stable row shape plus registry metadata."""
    settings = (f'Privacy & Security > {spec.settings_label}'
                if spec.settings_pane else 'Use the operation-specific access flow.')
    settings_url = (
        'x-apple.systempreferences:com.apple.preference.security?' + spec.settings_pane
        if spec.settings_pane else None
    )
    instruction = (
        f'Open System Settings > {settings} on this Mac and turn on OpenProgram. '
        'This page updates when you come back.'
        if spec.settings_pane else
        'Access is checked when the operation runs. Select the path or target application explicitly.'
    )
    row = {
        'id': spec.id,
        'label': spec.label,
        'label_zh': spec.label_zh,
        'status': status,
        'optional': spec.optional,
        'category': spec.category,
        'setup_group': spec.category,
        'subject': spec.subject,
        'identity_role': spec.identity_role or spec.subject,
        'identity_scope': spec.subject,
        'identity_bundle_id': spec.identity_bundle_id,
        'identity_application': spec.identity_application,
        'identity': {
            'role': spec.identity_role or spec.subject,
            'scope': spec.subject,
            'bundle_id': spec.identity_bundle_id,
            'application': spec.identity_application,
        },
        'request_mode': spec.request_mode,
        'settings_available': bool(spec.settings_pane),
        'settings_pane': spec.settings_pane,
        'settings_url': settings_url,
        'operations': list(spec.operations),
        'required_operations': list(spec.operations),
        'instruction': instruction,
        'can_request': status == 'not_granted' and spec.request_mode == 'native',
        'detail': detail,
    }
    if spec.usage_key:
        row['usage_key'] = spec.usage_key
    if spec.entitlement:
        row['entitlement'] = spec.entitlement
    if spec.subject == 'target_app':
        row['target_identity'] = {'status': 'required', 'bundle_id': None}
    elif spec.subject == 'user_selected_path':
        row['target_identity'] = {'status': 'required', 'path': None}
    row['package_declarations'] = list(spec.package_declarations)
    return row


def _mac_row(capability: str, *, status: str, detail: str) -> dict:
    """Legacy helper retained for callers that only know the two old ids."""
    return _capability_row(capability_spec(capability), status=status, detail=detail)


def _declaration_row(spec: CapabilitySpec, system: str) -> dict:
    if system not in spec.platforms:
        return _capability_row(
            spec, status='unsupported',
            detail=f'The {spec.label.lower()} backend is not supported on {system}.',
        )
    if spec.request_mode == 'targeted':
        return _capability_row(
            spec, status='unknown',
            detail='Authorization is target-specific; provide the target application to check it.',
        )
    if spec.request_mode == 'operation':
        return _capability_row(
            spec, status='unknown',
            detail='Authorization is scoped to the selected path and is checked by the real operation.',
        )
    return _capability_row(
        spec, status='unknown',
        detail='This capability is declared but no nonprompting backend is available in this runtime.',
    )


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


def _native_executor() -> str:
    """Return the one signed executor used for every macOS native check.

    A packaged worker is launched through the embedded, signed runtime app.
    Falling back to the current interpreter is limited to source/check-out
    installs where no named runtime exists; a bare ``python3`` can therefore
    never become a second identity in an installed OpenProgram run.
    """
    if platform.system() != 'Darwin':
        return os.path.abspath(sys.executable)
    try:
        from openprogram.operations.worker.lifecycle import worker_executable
        candidate = Path(worker_executable()).resolve()
        if candidate.is_file() and candidate.name == 'OpenProgram':
            return str(candidate)
    except (OSError, RuntimeError, ImportError):
        _log.debug('named runtime executor unavailable', exc_info=True)
    return os.path.abspath(sys.executable)


def _native_media_type(spec: CapabilitySpec, native):
    return getattr(
        native,
        'AVMediaTypeAudio' if spec.request_style == 'avfoundation_audio' else 'AVMediaTypeVideo',
    )


def _native_event_type(spec: CapabilitySpec, native):
    return getattr(
        native,
        'EKEntityTypeEvent' if spec.request_style == 'eventkit_events' else 'EKEntityTypeReminder',
    )


def _native_check(spec: CapabilitySpec, native) -> bool:
    """Perform a non-prompting check for one registry entry."""
    if spec.request_style.startswith('eventkit_'):
        status = native.EKEventStore.authorizationStatusForEntityType_(_native_event_type(spec, native))
        return int(status) in {3, 4}
    if spec.request_style.startswith('avfoundation_'):
        status = native.AVCaptureDevice.authorizationStatusForMediaType_(_native_media_type(spec, native))
        return int(status) == 3
    return bool(getattr(native, spec.check_method)())


def _native_wait_for_callback(invoke) -> None:
    """Wait for an EventKit/AVFoundation completion callback in the child."""
    completed = threading.Event()

    def completion(*_args):
        completed.set()

    invoke(completion)
    if not completed.wait(_NATIVE_REQUEST_TIMEOUT - 1):
        raise TimeoutError('native authorization callback timed out')


def _native_request(spec: CapabilitySpec, native) -> None:
    """Request one capability after an explicit local-owner action."""
    if spec.request_style == 'accessibility_prompt':
        method = getattr(native, spec.request_method)
        method({native.kAXTrustedCheckOptionPrompt: True})
        return
    if spec.request_style.startswith('eventkit_'):
        store = native.EKEventStore.alloc().init()
        method = getattr(store, spec.request_method, None)
        if method is not None:
            _native_wait_for_callback(method)
            return
        legacy = getattr(store, 'requestAccessToEntityType_completion_', None)
        if legacy is None:
            raise AttributeError(f'EventKit request method is unavailable for {spec.id}')
        _native_wait_for_callback(lambda done: legacy(_native_event_type(spec, native), done))
        return
    if spec.request_style.startswith('avfoundation_'):
        method = getattr(native.AVCaptureDevice, spec.request_method)
        _native_wait_for_callback(
            lambda done: method(_native_media_type(spec, native), done)
        )
        return
    getattr(native, spec.request_method)()


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
            spec = capability_spec(capability)
            native = importlib.import_module(module)
            if request == capability:
                _native_request(spec, native)
            granted = _native_check(spec, native)
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
    print(json.dumps({  # noqa: T201 - JSON output
        'schema': _NATIVE_PROBE_SCHEMA,
        'identity': _native_identity(),
        'capabilities': rows,
    }, sort_keys=True), flush=True)


def _native_probe(request_capability: str | None = None, *, timeout: float = _NATIVE_PROBE_TIMEOUT) -> dict | None:
    executable = _native_executor()
    identity_executable = str(Path(executable).resolve())
    command = [executable, '-I', '-B', '-c', _NATIVE_PROBE_SCRIPT]
    if request_capability is not None:
        if request_capability not in _MAC:
            raise ValueError('unsupported native capability request')
        command.extend(['--request', request_capability])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
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
    spec = capability_spec(capability)
    if spec.request_mode != 'native':
        return _declaration_row(spec, 'Darwin')
    probe = _native_probe()
    if probe is None:
        return _mac_row(
            capability, status='unknown',
            detail='The fresh native permission check failed; authorization is unknown.',
        )
    current = probe['capabilities'].get(capability)
    if current is None:
        return _mac_row(capability, status='unknown', detail='Native authorization is unknown.')
    from openprogram.host.system_access.identity import observe
    return observe(_mac_row(capability, **current))


def report() -> dict:
    """Return fresh advisory status for this process, not the connecting client."""
    system = platform.system()
    execution_identity = _native_identity()
    if system == 'Darwin':
        probe = _native_probe()
        if probe is not None and isinstance(probe.get('identity'), dict):
            execution_identity = dict(probe['identity'])
        rows = []
        for spec in capability_registry():
            current = probe['capabilities'].get(spec.id) if (
                probe is not None and spec.id in _MAC) else None
            if current is not None:
                row = _capability_row(spec, **current)
            elif spec.id in _MAC:
                row = _capability_row(
                    spec, status='unknown',
                    detail='The fresh native permission check failed; authorization is unknown.',
                )
            else:
                row = _declaration_row(spec, system)
            if spec.id in _MAC:
                from openprogram.host.system_access.identity import observe
                row = observe(row)
            rows.append(row)
    elif system == 'Linux':
        wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
        display = bool(os.environ.get('DISPLAY'))
        desktop_row = {
            'id': 'desktop_session', 'kind': 'host_session', 'label': 'Desktop access',
            'label_zh': '桌面访问', 'optional': True, 'category': 'desktop',
            'setup_group': 'desktop', 'subject': 'desktop_session',
            'request_mode': 'none', 'settings_available': False,
            'operations': ['desktop_session:*'], 'required_operations': ['desktop_session:*'],
            'identity_scope': 'host_session', 'identity_role': None,
            'identity_bundle_id': None, 'identity_application': None,
            'status': 'unsupported' if wayland else ('unknown' if display else 'unavailable'),
            'detail': ('The current desktop input backend does not provide Wayland portal authorization.' if wayland
                       else 'An X11 display is configured; access must be verified by the desktop backend.' if display
                       else 'No graphical desktop is configured for this process.'),
            'instruction': ('Use a supported X11 desktop session or a browser/VM backend. Do not disable desktop security.' if wayland
                            else 'Run the desktop worker in the intended signed-in graphical session; browser and ordinary CLI tasks do not require desktop access.'),
            'can_request': False,
        }
        rows = [desktop_row] + [_declaration_row(spec, system) for spec in capability_registry()]
    elif system == 'Windows':
        # Session names and administrator membership do not prove desktop access.
        desktop_row = {
            'id': 'desktop_session', 'kind': 'host_session', 'label': 'Desktop access',
            'label_zh': '桌面访问', 'optional': True, 'category': 'desktop',
            'setup_group': 'desktop', 'subject': 'desktop_session',
            'request_mode': 'none', 'settings_available': False,
            'operations': ['desktop_session:*'], 'required_operations': ['desktop_session:*'],
            'identity_scope': 'host_session', 'identity_role': None,
            'identity_bundle_id': None, 'identity_application': None,
            'status': 'unknown', 'detail': 'Desktop access is verified when the target is opened.',
            'instruction': 'Run in the intended signed-in desktop session. Locked screens, UAC secure desktop and higher-privilege applications may be inaccessible. Do not run the whole application as administrator.',
            'can_request': False,
        }
        rows = [desktop_row] + [_declaration_row(spec, system) for spec in capability_registry()]
    else:
        desktop_row = {'id': 'desktop_session', 'kind': 'host_session', 'label': 'Desktop access',
                 'label_zh': '桌面访问', 'optional': True, 'category': 'desktop',
                 'setup_group': 'desktop', 'subject': 'desktop_session',
                 'request_mode': 'none', 'settings_available': False,
                 'operations': ['desktop_session:*'], 'required_operations': ['desktop_session:*'],
                 'identity_scope': 'host_session', 'identity_role': None,
                 'identity_bundle_id': None, 'identity_application': None,
                 'status': 'unsupported', 'detail': 'No desktop permission backend for this platform.',
                 'instruction': 'Use a supported browser or remote VM backend.', 'can_request': False}
        rows = [desktop_row] + [_declaration_row(spec, system) for spec in capability_registry()]
    application = ''
    if system == 'Darwin':
        try:
            bundle = importlib.import_module('Foundation').NSBundle.mainBundle()
            application = str(bundle.objectForInfoDictionaryKey_('CFBundleName') or '')
        except Exception:
            _log.debug("bundle name lookup unavailable", exc_info=True)
    return {
        'schema': SYSTEM_ACCESS_SCHEMA,
        'version': SYSTEM_ACCESS_VERSION,
        'platform': system,
        'application': application,
        'host': socket.gethostname(),
        'executable': sys.executable,
        'pid': os.getpid(),
        'identity': execution_identity,
        'checked_at': time.time(),
        'capabilities': rows,
    }


def _desktop_operation(tool_name: str, args: dict | None) -> str | None:
    """The registry operation a host-bound tool call needs, or None when it needs no grant."""
    if not isinstance(args, dict):
        return None
    name = str(tool_name)
    if name == 'computer_use_agent':
        # Application windows need the desktop grants; Pages and a VM do not.
        # Listing applications reads nothing from the screen or the windows.
        if args.get('target') != 'app':
            return None
        named = isinstance(args.get('args'), dict) and bool(args['args'].get('app'))
        opening = 'get_app_state' if named else 'list_apps'  # A call without an action reads the app, or lists apps.
        steps = args.get('actions') if isinstance(args.get('actions'), list) else [{'action': args.get('action') or opening}]
        if not args.get('code') and steps and all(
                isinstance(step, dict) and str(step.get('action') or '').strip() in {'list_apps', 'guide'}
                for step in steps):
            return None
        return 'computer_use:desktop'
    return None


def required_access_state(tool_name: str, args: dict | None, found: set[str] | None = None) -> dict | None:
    """Return one registry-derived preflight result for a host-bound tool.

    ``waiting`` is recoverable by the durable system-access wait. Native
    dependency and platform failures are terminal for this operation, so they
    do not create a wait that can never become granted. Direct function calls
    and the canonical safe point consume this same result.

    ``found`` is the caller's record of the operations whose grants a probe
    has already found. A probe starts a fresh process (about 0.4 s), so a
    turn keeps one record and probes before its first desktop step only,
    not before each. A grant goes away only when the user withdraws it in
    System Settings; the desktop backend then fails the step and names the
    setting, and the next turn probes again.
    """
    operation = _desktop_operation(tool_name, args)
    if operation is None:
        return None
    if found is not None and operation in found:
        return {'state': 'ready', 'capabilities': [], 'required_capabilities': []}
    snapshot = report()
    if snapshot.get('platform') != 'Darwin':
        return None
    required_ids = {
        spec.id for spec in capability_registry()
        if operation in spec.operations
    }
    capabilities = [dict(row) for row in snapshot.get('capabilities', ())
                    if isinstance(row, dict) and row.get('id') in required_ids]
    if not capabilities or all(row.get('status') == 'granted' for row in capabilities):
        if found is not None:
            found.add(operation)
        return {'state': 'ready', 'capabilities': capabilities, 'required_capabilities': []}
    terminal = [row for row in capabilities
                if row.get('status') in {'unsupported', 'unavailable'}]
    if terminal:
        return {
            'state': 'infeasible',
            'reason_code': 'system_access_unavailable',
            'capabilities': capabilities,
            'required_capabilities': [str(row['id']) for row in terminal],
            'detail': 'The desktop access backend is unavailable on this execution host.',
        }
    return {
        'state': 'waiting',
        'reason_code': 'system_access_required',
        'capabilities': capabilities,
        'required_capabilities': [str(row['id']) for row in capabilities
                                  if row.get('status') != 'granted'],
    }


def access_manifest_for_tool(tool_name: str, args: dict | None, found: set[str] | None = None) -> dict | None:
    """Return a pre-effect durable wait manifest for local desktop GUI use.

    Default tasks select their capability before requesting desktop access.
    Explicit macOS desktop entries retain their pre-effect access boundary.
    ``found`` is a turn's record of grants already found (see
    :func:`required_access_state`).
    """
    if _desktop_operation(tool_name, args) is None:
        return None
    state = required_access_state(tool_name, args, found)
    if not state or state.get('state') != 'waiting':
        return None
    capabilities = list(state['capabilities'])
    required = list(state['required_capabilities'])
    return _desktop_access_manifest(tool_name, args, capabilities, required)


def missing_access_error(tool_name: str, args: dict | None, found: set[str] | None = None) -> str | None:
    """The tool error for a missing macOS grant in a turn nobody can answer a card in."""
    if _desktop_operation(tool_name, args) is None:
        return None
    state = required_access_state(tool_name, args, found)
    if not state or state.get('state') != 'waiting':
        return None
    required = set(state['required_capabilities'])
    rows = [row for row in state['capabilities'] if row.get('id') in required]
    return (f'{tool_name} cannot run: macOS has not granted ' + ', '.join(str(row['label']) for row in rows) + '. '
            + ' '.join(str(row['instruction']) for row in rows))


def _desktop_access_manifest(tool_name, args, capabilities, required):
    return {
        'kind': 'system_access',
        'required_capabilities': required,
        'capabilities': capabilities,
        'prompt': 'Desktop access is required before this step can run.',
        'options': [], 'multi': False, 'allow_custom': False,
        'detail': 'Authorize the required capabilities on the execution computer, then return to OpenProgram.',
        'schema': {}, 'questions': [], 'timeout': None,
        'request_metadata': {
            'tool': str(tool_name), 'args': dict(args),
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
    spec = capability_spec(capability)
    if not spec.settings_pane:
        return False
    try:
        appkit = importlib.import_module('AppKit')
        foundation = importlib.import_module('Foundation')
        url = foundation.NSURL.URLWithString_('x-apple.systempreferences:com.apple.preference.security?' + spec.settings_pane)
        return bool(appkit.NSWorkspace.sharedWorkspace().openURL_(url))
    except Exception:
        return False


def request_access(capability: str, *, open_settings: bool = False) -> dict:
    """Explicit local-user setup only; never call from a probe or a model tool."""
    spec = capability_spec(capability)
    if platform.system() != 'Darwin':
        raise ValueError('No native permission request for this capability on this platform.')
    if spec.request_mode != 'native':
        result = _declaration_row(spec, 'Darwin')
        if open_settings and spec.settings_pane:
            result['settings_opened'] = _open_settings(capability)
        return result
    if not _REQUEST_LOCK.acquire(blocking=False):
        raise RuntimeError('A system permission request is already in progress.')
    try:
        before = _mac_status(capability)
        if before['status'] == 'granted':
            return before
        if open_settings:
            result = _mac_status(capability)
            result['settings_opened'] = _open_settings(capability)
            return result
        if not before['can_request']:
            return before
        from openprogram.host.system_access.identity import prepare_request
        prepare_request(before)
        requested = _native_probe(request_capability=capability, timeout=_NATIVE_REQUEST_TIMEOUT)
        if requested is None:
            return _mac_row(capability, status='unknown', detail='Native authorization request did not complete; authorization was not confirmed.')
        after = _mac_status(capability)
        return after
    finally:
        _REQUEST_LOCK.release()


def setup_all_access() -> dict:
    """Run the explicit first-run setup for every native runtime capability.

    The caller must be the local owner and must have initiated the action from
    the visible settings UI.  macOS still owns the final decision for each
    privacy category; this function only requests the registered capabilities
    through the one signed runtime identity and returns one consolidated report.
    """
    if platform.system() != 'Darwin':
        snapshot = report()
        return {
            'status': 'unsupported', 'requested_capabilities': [],
            'remaining_capabilities': [row['id'] for row in snapshot['capabilities']
                                       if row.get('status') != 'granted'],
            'capabilities': snapshot['capabilities'],
        }
    requested = []
    errors = []
    for spec in capability_registry():
        if spec.request_mode != 'native':
            continue
        requested.append(spec.id)
        try:
            request_access(spec.id)
        except (RuntimeError, ValueError) as exc:
            errors.append({'id': spec.id, 'error': str(exc)})
    snapshot = report()
    remaining = [row['id'] for row in snapshot['capabilities']
                 if row.get('id') in requested and row.get('status') != 'granted']
    return {
        'status': 'granted' if not remaining and not errors else 'partial',
        'requested_capabilities': requested,
        'remaining_capabilities': remaining,
        'errors': errors,
        'capabilities': snapshot['capabilities'],
    }


def doctor_rows() -> list[dict]:
    """Optional capability advice never blocks installation or upgrades."""
    snapshot = report()
    return [{'id': 'system_access:' + row['id'], 'ok': True,
             'label': row['label'], 'status': row['status'], 'optional': True,
             'detail': f"{row['status']}: {row['detail']}" +
                       ('' if row['status'] == 'granted' else ' ' + row['instruction'])}
            for row in snapshot['capabilities']]
