import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from openprogram.host import system_access


def _probe_payload(executable, *, screen_recording='granted', accessibility='granted'):
    statuses = {capability: 'granted' for capability in system_access._MAC}
    statuses.update(screen_recording=screen_recording, accessibility=accessibility)
    return {
        'schema': 1,
        'identity': {'executable': executable},
        'capabilities': [
            {'id': capability, 'status': status, 'detail': 'fresh'}
            for capability, status in statuses.items()
        ],
    }


def test_report_uses_fresh_named_executor_probe_when_worker_cache_is_stale(monkeypatch):
    commands = []
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setitem(
        system_access.sys.modules,
        "Quartz",
        SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False),
    )
    monkeypatch.setitem(
        system_access.sys.modules,
        "ApplicationServices",
        SimpleNamespace(AXIsProcessTrusted=lambda: False),
    )
    executor = system_access._native_executor()
    fresh = _probe_payload(str(Path(executor).resolve()))

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(fresh), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    report = system_access.report()
    rows = {row["id"]: row for row in report["capabilities"]}

    assert rows["screen_recording"]["status"] == "granted"
    assert rows["accessibility"]["status"] == "granted"
    assert len(commands) == 1
    assert commands[0][0][:3] == [executor, "-I", "-B"]
    assert "_native_probe_entry" in commands[0][0][4]
    assert commands[0][1]["timeout"] > 0


def test_report_preserves_unavailable_dependency_status(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    executable = str(Path(system_access._native_executor()).resolve())
    fresh = _probe_payload(executable, screen_recording='unavailable')
    fresh['capabilities'][0]['detail'] = 'Native permission dependencies are missing (ImportError).'
    monkeypatch.setattr(
        subprocess, 'run',
        lambda command, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(fresh), stderr='',
        ),
    )
    rows = {row['id']: row for row in system_access.report()['capabilities']}
    assert rows['screen_recording']['status'] == 'unavailable'
    assert rows['screen_recording']['can_request'] is False
    assert 'missing' in rows['screen_recording']['detail']


def test_native_probe_entry_marks_missing_dependency_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(system_access.sys, 'argv', ['-c'])

    def import_module(name):
        if name == 'Quartz':
            raise ImportError('Quartz is missing')
        if name == 'ApplicationServices':
            return SimpleNamespace(AXIsProcessTrusted=lambda: True)
        if name == 'EventKit':
            return SimpleNamespace(
                EKEntityTypeEvent=0, EKEntityTypeReminder=1,
                EKEventStore=SimpleNamespace(
                    authorizationStatusForEntityType_=lambda entity: 3,
                ),
            )
        if name == 'AVFoundation':
            return SimpleNamespace(
                AVMediaTypeAudio='audio', AVMediaTypeVideo='video',
                AVCaptureDevice=SimpleNamespace(
                    authorizationStatusForMediaType_=lambda media: 3,
                ),
            )
        if name == 'Foundation':
            raise ImportError('Foundation is missing')
        raise AssertionError(name)

    monkeypatch.setattr(system_access.importlib, 'import_module', import_module)
    system_access._native_probe_entry()
    payload = json.loads(capsys.readouterr().out)
    rows = {row['id']: row for row in payload['capabilities']}
    assert rows['screen_recording']['status'] == 'unavailable'
    assert 'missing' in rows['screen_recording']['detail']
    assert rows['accessibility']['status'] == 'granted'


def test_native_probe_entry_checks_eventkit_and_avfoundation_without_request(monkeypatch, capsys):
    monkeypatch.setattr(system_access.sys, 'argv', ['-c'])

    class Store:
        @classmethod
        def authorizationStatusForEntityType_(cls, entity):
            return 3

    class Device:
        @classmethod
        def authorizationStatusForMediaType_(cls, media):
            return 3

    def import_module(name):
        if name == 'Quartz':
            return SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True)
        if name == 'ApplicationServices':
            return SimpleNamespace(AXIsProcessTrusted=lambda: True)
        if name == 'EventKit':
            return SimpleNamespace(EKEntityTypeEvent=0, EKEntityTypeReminder=1, EKEventStore=Store)
        if name == 'AVFoundation':
            return SimpleNamespace(AVMediaTypeAudio='audio', AVMediaTypeVideo='video', AVCaptureDevice=Device)
        if name == 'Foundation':
            raise ImportError('Foundation is mocked away')
        raise AssertionError(name)

    monkeypatch.setattr(system_access.importlib, 'import_module', import_module)
    system_access._native_probe_entry()
    payload = json.loads(capsys.readouterr().out)
    rows = {row['id']: row for row in payload['capabilities']}
    assert all(rows[capability]['status'] == 'granted' for capability in (
        'calendar', 'reminders', 'microphone', 'camera',
    ))


def test_report_rejects_probe_for_another_executable(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    executable = os.path.abspath(system_access._native_executor())
    monkeypatch.setattr(
        subprocess, 'run',
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_probe_payload('/Applications/Other.app/Contents/MacOS/Other')),
            stderr='',
        ),
    )
    rows = {row['id']: row for row in system_access.report()['capabilities']}
    assert rows['screen_recording']['status'] == 'unknown'
    assert rows['accessibility']['status'] == 'unknown'
    assert executable != '/Applications/Other.app/Contents/MacOS/Other'


def test_request_access_uses_the_same_named_helper(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    executable = os.path.abspath(system_access._native_executor())
    identity = str(Path(executable).resolve())
    commands = []
    payloads = iter([
        _probe_payload(identity, accessibility='not_granted'),
        _probe_payload(identity, accessibility='not_granted'),
        _probe_payload(identity, accessibility='granted'),
    ])

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(next(payloads)), stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    result = system_access.request_access('accessibility')
    assert result['status'] == 'granted'
    assert len(commands) == 3
    assert all(command[:3] == [executable, '-I', '-B'] for command, _ in commands)
    assert commands[1][0][-2:] == ['--request', 'accessibility']
    assert commands[0][1]['timeout'] == system_access._NATIVE_PROBE_TIMEOUT
    assert commands[1][1]['timeout'] == system_access._NATIVE_REQUEST_TIMEOUT


def test_probe_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', timeout)
    rows = {row['id']: row for row in system_access.report()['capabilities']}
    assert all(row['status'] == 'unknown' for row in rows.values())
