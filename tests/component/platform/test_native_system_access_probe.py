import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from openprogram import system_access


def _probe_payload(executable, *, screen_recording='granted', accessibility='granted'):
    return {
        'schema': 1,
        'identity': {'executable': executable},
        'capabilities': [
            {'id': 'screen_recording', 'status': screen_recording, 'detail': 'fresh'},
            {'id': 'accessibility', 'status': accessibility, 'detail': 'fresh'},
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
    fresh = _probe_payload(str(Path(system_access.sys.executable).resolve()))

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(fresh), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    report = system_access.report()
    rows = {row["id"]: row for row in report["capabilities"]}

    assert rows["screen_recording"]["status"] == "granted"
    assert rows["accessibility"]["status"] == "granted"
    assert len(commands) == 1
    assert commands[0][0][:3] == [os.path.abspath(system_access.sys.executable), "-I", "-B"]
    assert "_native_probe_entry" in commands[0][0][4]
    assert commands[0][1]["timeout"] > 0


def test_report_preserves_unavailable_dependency_status(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    executable = str(Path(system_access.sys.executable).resolve())
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


def test_report_rejects_probe_for_another_executable(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    executable = os.path.abspath(system_access.sys.executable)
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
    executable = os.path.abspath(system_access.sys.executable)
    identity = str(Path(executable).resolve())
    commands = []
    payloads = iter([
        _probe_payload(identity, accessibility='not_granted'),
        _probe_payload(identity, accessibility='not_granted'),
        _probe_payload(identity, accessibility='granted'),
    ])

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(next(payloads)), stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    result = system_access.request_access('accessibility')
    assert result['status'] == 'granted'
    assert len(commands) == 3
    assert all(command[:3] == [executable, '-I', '-B'] for command in commands)
    assert commands[1][-2:] == ['--request', 'accessibility']


def test_probe_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', timeout)
    rows = {row['id']: row for row in system_access.report()['capabilities']}
    assert all(row['status'] == 'unknown' for row in rows.values())
