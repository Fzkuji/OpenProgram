import sys

from openprogram import system_access


def test_public_report_and_request_recover_updated_app_once(monkeypatch, tmp_path):
    from openprogram import system_access_identity as recovery
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.desktop'}
    monkeypatch.setattr(recovery, '_app_identity', lambda: dict(identity))
    status = {'screen': 'granted'}
    calls = []
    def probe(request_capability=None):
        if request_capability:
            calls.append(('request', request_capability))
        return {'identity': {'executable': sys.executable}, 'capabilities': {
            'screen_recording': {'status': status['screen'], 'detail': 'fresh'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        }}
    monkeypatch.setattr(system_access, '_native_probe', probe)
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: True)
    monkeypatch.setattr(recovery, '_reset_screen_grant', lambda: calls.append(('reset', 'ai.openprogram.desktop')))
    assert system_access.report()['capabilities'][0]['status'] == 'granted'
    identity['hash'] = 'b' * 40
    status['screen'] = 'not_granted'
    row = system_access.report()['capabilities'][0]
    assert row['recovery'] == 'reauthorize_after_update'
    assert not calls
    system_access.request_access('screen_recording')
    assert calls == [('reset', 'ai.openprogram.desktop'), ('request', 'screen_recording')]
    system_access.request_access('screen_recording')
    assert calls.count(('reset', 'ai.openprogram.desktop')) == 1


def test_no_reset_without_verified_changed_grant(monkeypatch, tmp_path):
    from openprogram import system_access_identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.desktop'}
    monkeypatch.setattr(recovery, '_app_identity', lambda: identity)
    monkeypatch.setattr(recovery, '_reset_screen_grant', lambda: (_ for _ in ()).throw(AssertionError('unexpected reset')))
    denied = {'id': 'screen_recording', 'status': 'not_granted'}
    assert recovery.observe(denied) == denied
    recovery.prepare_request(denied)
    recovery.observe({**denied, 'status': 'granted'})
    assert recovery.observe(denied) == denied  # Revocation with unchanged app.
    identity = {**identity, 'hash': 'b' * 40}
    unknown = {**denied, 'status': 'unknown'}
    assert recovery.observe(unknown) == unknown
    recovery.prepare_request(unknown)
    ax = {'id': 'accessibility', 'status': 'not_granted'}
    assert recovery.observe(ax) == ax
    recovery.prepare_request(ax)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: False)
    assert recovery.observe(denied) == denied
    recovery.prepare_request({'recovery': 'reauthorize_after_update'})


def test_failed_reset_is_not_repeated_after_restart(monkeypatch, tmp_path):
    import json
    import pytest
    from openprogram import system_access_identity as recovery
    p = tmp_path / 'receipt.json'
    p.write_text(json.dumps({'granted': {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.desktop'}}))
    monkeypatch.setattr(recovery, '_state_path', lambda: p)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    monkeypatch.setattr(recovery, '_app_identity', lambda: {'hash': 'b' * 40, 'bundle_id': 'ai.openprogram.desktop'})
    calls = []
    def fail():
        calls.append(1)
        raise OSError('failed')
    monkeypatch.setattr(recovery, '_reset_screen_grant', fail)
    row = recovery.observe({'id': 'screen_recording', 'status': 'not_granted'})
    with pytest.raises(RuntimeError, match='Could not renew'):
        recovery.prepare_request(row)
    # The persisted marker, not an in-memory guard, prevents another reset.
    assert json.loads(p.read_text())['reset_for']['hash'] == 'b' * 40
    recovery.prepare_request(row)
    assert len(calls) == 1


def test_receipt_symlink_and_corrupt_state_cannot_reset(monkeypatch, tmp_path):
    from openprogram import system_access_identity as recovery
    p = tmp_path / 'receipt.json'
    victim = tmp_path / 'other'
    victim.write_text('unchanged')
    p.symlink_to(victim)
    monkeypatch.setattr(recovery, '_state_path', lambda: p)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    monkeypatch.setattr(recovery, '_app_identity', lambda: {'hash': 'b' * 40, 'bundle_id': 'ai.openprogram.desktop'})
    row = {'id': 'screen_recording', 'status': 'granted'}
    assert recovery.observe(row) == row
    assert victim.read_text() == 'unchanged'
    p.unlink()
    p.write_text('invalid json')
    assert recovery.observe({**row, 'status': 'not_granted'}) == {**row, 'status': 'not_granted'}


def test_platform_reset_command_is_fixed_and_bounded(monkeypatch):
    from types import SimpleNamespace
    from openprogram import system_access_identity as recovery
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(recovery.subprocess, 'run', run)
    recovery._reset_screen_grant()
    assert calls == [(['/usr/bin/tccutil', 'reset', 'ScreenCapture', 'ai.openprogram.desktop'],
                      {'capture_output': True, 'timeout': 5})]
