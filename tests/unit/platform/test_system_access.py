from types import SimpleNamespace
import sys

import pytest

from openprogram import system_access


def test_macos_checks_both_and_does_not_prompt(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    calls = []
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False))
    monkeypatch.setitem(sys.modules, 'ApplicationServices', SimpleNamespace(AXIsProcessTrusted=lambda: calls.append('ax') or True))
    report = system_access.report()
    rows = {row['id']: row for row in report['capabilities']}
    assert rows['screen_recording']['status'] == 'not_granted'
    assert rows['accessibility']['status'] == 'granted'
    assert calls == ['ax']


def test_granted_request_is_noop(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: (_ for _ in ()).throw(AssertionError('must not open settings')))
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True))
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
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace())
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
    monkeypatch.setitem(sys.modules, 'ApplicationServices', SimpleNamespace(
        AXIsProcessTrusted=lambda: False, kAXTrustedCheckOptionPrompt='prompt',
        AXIsProcessTrustedWithOptions=lambda options: calls.append(options)))
    row = system_access.request_access('accessibility')
    assert calls == [{'prompt': True}]
    assert opened == ['accessibility']
    assert row['settings_opened']
    assert row['status'] == 'not_granted'


def test_gui_agent_desktop_manifest_requires_missing_macos_capabilities(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False))
    monkeypatch.setitem(sys.modules, 'ApplicationServices', SimpleNamespace(AXIsProcessTrusted=lambda: True))

    manifest = system_access.access_manifest_for_tool(
        'gui_agent', {'task': 'Open the app', 'surface': 'desktop'}
    )

    assert manifest is not None
    assert manifest['kind'] == 'system_access'
    assert manifest['required_capabilities'] == ['screen_recording']
    assert manifest['request_metadata']['tool'] == 'gui_agent'
    assert manifest['policy_snapshot']['on_grant'] == 'continue'


@pytest.mark.parametrize('args', [
    {'task': 'Use browser', 'surface': 'browser'},
    {'task': 'Use VM', 'surface': 'vm', 'vm_url': 'http://vm'},
])
def test_gui_agent_remote_surfaces_skip_system_access_manifest(monkeypatch, args):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    assert system_access.access_manifest_for_tool('gui_agent', args) is None
