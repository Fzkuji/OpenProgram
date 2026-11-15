import sys
from types import SimpleNamespace

import pytest

from openprogram.host import system_access


def test_native_executor_prefers_named_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    helper = tmp_path / 'OpenProgram'
    helper.write_text('runtime')
    monkeypatch.setattr('openprogram.operations.worker.lifecycle.worker_executable', lambda: str(helper))
    assert system_access._native_executor() == str(helper.resolve())


@pytest.mark.parametrize('capability,style', [
    ('calendar', 'eventkit_events'),
    ('reminders', 'eventkit_reminders'),
    ('microphone', 'avfoundation_audio'),
    ('camera', 'avfoundation_video'),
])
def test_native_request_styles_use_explicit_completion_callbacks(capability, style):
    callbacks = []

    class Store:
        @classmethod
        def authorizationStatusForEntityType_(cls, entity):
            return 0

        def requestFullAccessToEventsWithCompletion_(self, callback):
            callbacks.append(('calendar', callback)); callback(True, None)

        def requestFullAccessToRemindersWithCompletion_(self, callback):
            callbacks.append(('reminders', callback)); callback(True, None)

        def requestAccessToEntityType_completion_(self, entity, callback):
            callbacks.append(('legacy', callback)); callback(True, None)

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    class Device:
        @classmethod
        def authorizationStatusForMediaType_(cls, media):
            return 0

        @classmethod
        def requestAccessForMediaType_completionHandler_(cls, media, callback):
            callbacks.append((media, callback)); callback(True)

    native = SimpleNamespace(
        EKEntityTypeEvent=0, EKEntityTypeReminder=1, EKEventStore=Store,
        AVMediaTypeAudio='audio', AVMediaTypeVideo='video', AVCaptureDevice=Device,
    )
    spec = system_access.capability_spec(capability)
    assert spec.request_style == style
    system_access._native_request(spec, native)
    assert callbacks


def test_macos_checks_both_and_does_not_prompt(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_native_probe', lambda: {
        'identity': {'executable': sys.executable},
        'capabilities': {
            'screen_recording': {'status': 'not_granted', 'detail': 'fresh'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        },
    })
    report = system_access.report()
    rows = {row['id']: row for row in report['capabilities']}
    assert rows['screen_recording']['status'] == 'not_granted'
    assert rows['accessibility']['status'] == 'granted'


def test_granted_request_is_noop(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: (_ for _ in ()).throw(AssertionError('must not open settings')))
    monkeypatch.setattr(system_access, '_native_probe', lambda request_capability=None: {
        'identity': {'executable': sys.executable},
        'capabilities': {
            'screen_recording': {'status': 'granted', 'detail': 'fresh'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        },
    })
    assert system_access.request_access('screen_recording')['status'] == 'granted'


def test_linux_is_not_granted_from_display(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    assert system_access.report()['capabilities'][0]['status'] == 'unavailable'
    monkeypatch.setenv('DISPLAY', ':0')
    assert system_access.report()['capabilities'][0]['status'] == 'unknown'
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    assert system_access.report()['capabilities'][0]['status'] == 'unsupported'


def test_probe_failure_is_not_denial(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_native_probe', lambda: None)
    assert system_access.report()['capabilities'][0]['status'] == 'unknown'


def test_optional_access_does_not_block_installation(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    assert all(row['ok'] and row['optional'] for row in system_access.doctor_rows())


def test_windows_never_assumes_administrator_means_access(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Windows')
    row = system_access.report()['capabilities'][0]
    assert row['status'] == 'unknown'
    assert not row['can_request']


def test_explicit_request_only_prompts_missing_capability(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    calls = []
    opened = []
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: opened.append(cap) or True)
    def probe(request_capability=None, **kwargs):
        calls.append(request_capability)
        status = 'not_granted'
        return {
            'identity': {'executable': sys.executable},
            'capabilities': {
                'screen_recording': {'status': status, 'detail': 'fresh'},
                'accessibility': {'status': status, 'detail': 'fresh'},
            },
        }
    monkeypatch.setattr(system_access, '_native_probe', probe)
    row = system_access.request_access('accessibility')
    assert calls == [None, 'accessibility', None]
    assert opened == []
    assert 'settings_opened' not in row
    assert row['status'] == 'not_granted'
    row = system_access.request_access('accessibility', open_settings=True)
    assert opened == ['accessibility']
    assert row['settings_opened']


def test_request_timeout_or_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_native_probe', lambda request_capability=None, **kwargs: None)
    row = system_access.request_access('accessibility')
    assert row['status'] == 'unknown'
    assert row['detail']

def test_open_settings_skips_native_request(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    calls = []
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: calls.append(cap) or True)
    monkeypatch.setattr(system_access, '_native_probe', lambda *args, **kwargs: {'identity': {'executable': sys.executable}, 'capabilities': {'screen_recording': {'status': 'granted', 'detail': 'fresh'}, 'accessibility': {'status': 'not_granted', 'detail': 'fresh'}}})
    row = system_access.request_access('accessibility', open_settings=True)
    assert calls == ['accessibility']
    assert row['settings_opened'] is True


def test_unified_setup_requests_each_registered_native_capability_once(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    requested = []
    monkeypatch.setattr(system_access, 'request_access', lambda capability: requested.append(capability) or {
        'id': capability, 'status': 'granted', 'detail': 'fresh',
    })
    monkeypatch.setattr(system_access, 'report', lambda: {
        'capabilities': [
            {'id': 'screen_recording', 'status': 'granted'},
            {'id': 'accessibility', 'status': 'granted'},
        ],
    })
    result = system_access.setup_all_access()
    assert requested == [spec.id for spec in system_access.capability_registry()
                         if spec.request_mode == 'native']
    assert result['status'] == 'granted'
    assert result['remaining_capabilities'] == []


@pytest.mark.parametrize('status', ['unknown', 'unavailable'])
def test_explicit_settings_remains_available_when_probe_cannot_request(monkeypatch, status):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    calls = []
    monkeypatch.setattr(system_access, '_mac_status', lambda cap: {
        'id': cap, 'status': status, 'can_request': False,
    })
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: calls.append(cap) or True)
    row = system_access.request_access('accessibility', open_settings=True)
    assert row['settings_opened'] is True
    assert calls == ['accessibility']


def test_app_control_manifest_requires_missing_macos_capabilities(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_native_probe', lambda: {
        'identity': {'executable': sys.executable},
        'capabilities': {
            'screen_recording': {'status': 'not_granted', 'detail': 'fresh'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        },
    })

    manifest = system_access.access_manifest_for_tool(
        'computer_use_agent', {'target': 'app', 'action': 'click', 'args': {'app': 'Notes'}}
    )

    assert manifest is not None
    assert manifest['kind'] == 'system_access'
    assert manifest['required_capabilities'] == ['screen_recording']
    assert manifest['request_metadata']['tool'] == 'computer_use_agent'
    assert manifest['policy_snapshot']['on_grant'] == 'continue'


@pytest.mark.parametrize('status', ['unavailable', 'unsupported'])
def test_unresolvable_app_access_does_not_create_permanent_wait(monkeypatch, status):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_native_probe', lambda: {
        'identity': {'executable': sys.executable},
        'capabilities': {
            'screen_recording': {'status': status, 'detail': 'unavailable'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        },
    })
    call = {'target': 'app', 'action': 'click', 'args': {'app': 'Notes'}}
    state = system_access.required_access_state('computer_use_agent', call)
    assert state['state'] == 'infeasible'
    assert state['reason_code'] == 'system_access_unavailable'
    assert system_access.access_manifest_for_tool('computer_use_agent', call) is None


@pytest.mark.parametrize('args', [
    {'target': 'browser', 'action': 'observe'},
    {'target': 'vm', 'action': 'click', 'args': {'x': 1, 'y': 2}},
    {'target': 'app', 'action': 'list_apps'},
])
def test_pages_vm_and_app_listing_skip_system_access_manifest(monkeypatch, args):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    assert system_access.access_manifest_for_tool('computer_use_agent', args) is None


def test_desktop_grants_found_once_are_not_probed_again_under_the_same_record(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    probes, accessibility = [], ['not_granted']

    def probe():
        probes.append(accessibility[0])
        return {
            'identity': {'executable': sys.executable},
            'capabilities': {
                'screen_recording': {'status': 'granted', 'detail': 'fresh'},
                'accessibility': {'status': accessibility[0], 'detail': 'fresh'},
            },
        }

    monkeypatch.setattr(system_access, '_native_probe', probe)
    call = {'target': 'app', 'action': 'click', 'args': {'app': 'Notes', 'element_index': 1}}
    found = set()

    # While a grant is missing every call probes, so the grant is seen as soon as it is given.
    assert system_access.access_manifest_for_tool('computer_use_agent', call, found)['kind'] == 'system_access'
    assert system_access.access_manifest_for_tool('computer_use_agent', call, found)['kind'] == 'system_access'
    assert len(probes) == 2 and not found

    accessibility[0] = 'granted'
    assert system_access.access_manifest_for_tool('computer_use_agent', call, found) is None
    assert len(probes) == 3 and found == {'computer_use:desktop'}

    # Found: the later steps under this record start no process.
    for _ in range(3):
        assert system_access.access_manifest_for_tool('computer_use_agent', call, found) is None
        assert system_access.required_access_state('computer_use_agent', call, found)['state'] == 'ready'
    assert len(probes) == 3

    # Another record (the next turn) and a caller that keeps none probe for themselves.
    assert system_access.access_manifest_for_tool('computer_use_agent', call, set()) is None
    assert system_access.access_manifest_for_tool('computer_use_agent', call) is None
    assert len(probes) == 5
