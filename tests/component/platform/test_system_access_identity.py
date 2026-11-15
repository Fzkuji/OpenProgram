import sys

from openprogram.host import system_access


def test_public_report_and_request_recover_updated_app_once(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.runtime'}
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: dict(identity))
    status = {'screen': 'granted'}
    calls = []
    def probe(request_capability=None, *, timeout=5.0):
        if request_capability:
            calls.append(('request', request_capability))
        return {'identity': {'executable': sys.executable}, 'capabilities': {
            'screen_recording': {'status': status['screen'], 'detail': 'fresh'},
            'accessibility': {'status': 'granted', 'detail': 'fresh'},
        }}
    monkeypatch.setattr(system_access, '_native_probe', probe)
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: True)
    monkeypatch.setattr(recovery, '_reset_screen_grant', lambda: calls.append(('reset', 'ai.openprogram.runtime')))
    assert system_access.report()['capabilities'][0]['status'] == 'granted'
    identity['hash'] = 'b' * 40
    status['screen'] = 'not_granted'
    row = system_access.report()['capabilities'][0]
    assert row['recovery'] == 'reauthorize_after_update'
    assert not calls
    system_access.request_access('screen_recording')
    assert calls == [('reset', 'ai.openprogram.runtime'), ('request', 'screen_recording')]
    system_access.request_access('screen_recording')
    assert calls.count(('reset', 'ai.openprogram.runtime')) == 1


def test_no_reset_without_verified_changed_grant(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.runtime'}
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: identity)
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
    from openprogram.host.system_access import identity as recovery
    p = tmp_path / 'receipt.json'
    p.write_text(json.dumps({'granted': {'hash': 'a' * 40, 'bundle_id': 'ai.openprogram.runtime'}}))
    monkeypatch.setattr(recovery, '_state_path', lambda: p)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: {'hash': 'b' * 40, 'bundle_id': 'ai.openprogram.runtime'})
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
    from openprogram.host.system_access import identity as recovery
    p = tmp_path / 'receipt.json'
    victim = tmp_path / 'other'
    victim.write_text('unchanged')
    p.symlink_to(victim)
    monkeypatch.setattr(recovery, '_state_path', lambda: p)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: {'hash': 'b' * 40, 'bundle_id': 'ai.openprogram.runtime'})
    row = {'id': 'screen_recording', 'status': 'granted'}
    assert recovery.observe(row) == row
    assert victim.read_text() == 'unchanged'
    p.unlink()
    p.write_text('invalid json')
    assert recovery.observe({**row, 'status': 'not_granted'}) == {**row, 'status': 'not_granted'}


def test_platform_reset_command_is_fixed_and_bounded(monkeypatch):
    from types import SimpleNamespace
    from openprogram.host.system_access import identity as recovery
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(recovery.subprocess, 'run', run)
    recovery._reset_screen_grant()
    assert calls == [(['/usr/bin/tccutil', 'reset', 'ScreenCapture', 'ai.openprogram.runtime'],
                      {'capture_output': True, 'timeout': 5})]


def test_accessibility_uses_runtime_requirement_and_fixed_reset(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    outer = {'bundle_id': 'ai.openprogram.desktop', 'requirement': 'outer-v1', 'hash': 'a' * 40}
    runtime = {'bundle_id': 'ai.openprogram.runtime', 'requirement': 'runtime-v1', 'hash': 'b' * 40}
    monkeypatch.setattr(recovery, '_app_identity', lambda: dict(outer))
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: dict(runtime))
    assert recovery.observe({'id': 'accessibility', 'status': 'granted'})['status'] == 'granted'
    runtime['hash'] = 'c' * 40
    runtime['requirement'] = 'runtime-v2'
    row = recovery.observe({'id': 'accessibility', 'status': 'not_granted'})
    assert row['recovery'] == 'reauthorize_after_update'
    calls = []
    monkeypatch.setattr(recovery, '_reset_capability', lambda capability: calls.append(capability))
    recovery.prepare_request(row)
    assert calls == ['accessibility']
    recovery.prepare_request(row)
    assert calls == ['accessibility']


def test_requirement_change_without_hash_change_is_recovery(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'bundle_id': 'ai.openprogram.runtime', 'requirement': 'runtime-v1', 'hash': 'a' * 40}
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: dict(identity))
    recovery.observe({'id': 'accessibility', 'status': 'granted'})
    identity['requirement'] = 'runtime-v2'
    row = recovery.observe({'id': 'accessibility', 'status': 'not_granted'})
    assert row['recovery'] == 'reauthorize_after_update'


def test_nested_receipts_and_reset_marker_are_persisted(monkeypatch, tmp_path):
    import json
    from openprogram.host.system_access import identity as recovery
    p = tmp_path / 'receipt.json'
    monkeypatch.setattr(recovery, '_state_path', lambda: p)
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identities = {
        'screen_recording': {'bundle_id': 'ai.openprogram.runtime', 'requirement': 'screen-v1', 'hash': 'a' * 40},
        'accessibility': {'bundle_id': 'ai.openprogram.runtime', 'requirement': 'ax-v1', 'hash': 'b' * 40},
    }
    monkeypatch.setattr(recovery, '_identity_for_capability', lambda capability: dict(identities[capability]))
    recovery.observe({'id': 'screen_recording', 'status': 'granted'})
    recovery.observe({'id': 'accessibility', 'status': 'granted'})
    state = json.loads(p.read_text())
    assert set(state['granted_by_capability']) == {'screen_recording', 'accessibility'}
    identities['accessibility']['requirement'] = 'ax-v2'
    row = recovery.observe({'id': 'accessibility', 'status': 'not_granted'})
    monkeypatch.setattr(recovery, '_reset_capability', lambda capability: None)
    recovery.prepare_request(row)
    state = json.loads(p.read_text())
    assert state['reset_for']['accessibility']['requirement'] == 'ax-v2'


def test_stable_requirement_with_new_hash_does_not_repeat_reset(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    identity = {'bundle_id': 'ai.openprogram.runtime', 'requirement': 'stable', 'hash': 'a' * 40}
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: dict(identity))
    recovery.observe({'id': 'accessibility', 'status': 'granted'})
    identity['hash'] = 'b' * 40
    assert recovery.observe({'id': 'accessibility', 'status': 'not_granted'}) == {
        'id': 'accessibility', 'status': 'not_granted'}


def test_wrong_bundle_and_empty_legacy_receipt_never_recover(monkeypatch, tmp_path):
    from openprogram.host.system_access import identity as recovery
    monkeypatch.setattr(recovery, '_state_path', lambda: tmp_path / 'receipt.json')
    monkeypatch.setattr(recovery, '_managed_worker', lambda: True)
    monkeypatch.setattr(recovery, '_runtime_identity', lambda: {
        'bundle_id': 'ai.openprogram.runtime', 'requirement': 'new', 'hash': 'a' * 40})
    with recovery._receipt() as state:
        state['granted'] = {'bundle_id': 'wrong.bundle', 'hash': 'b' * 40}
    row = recovery.observe({'id': 'screen_recording', 'status': 'not_granted'})
    assert 'recovery' not in row
