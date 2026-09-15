"""Notarization status, rather than a wait process exit, gates publication."""
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('status', ['In Progress', 'Invalid'])
def test_nonaccepted_submission_stops_publication(monkeypatch, capsys, status):
    notarize = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))['notarize']
    calls = []

    def command(*args):
        calls.append(args)
        if 'submit' in args:
            return '{"id":"submission-123"}'
        if 'wait' in args:
            raise RuntimeError('wait timed out')
        return '{"status":"' + status + '"}'

    monkeypatch.setitem(notarize.__globals__, 'run', command)
    with pytest.raises(RuntimeError, match=status + ': submission-123'):
        notarize(Path('/staged/app.zip'), 'test-profile')
    assert 'Notarization submitted: submission-123' in capsys.readouterr().out
    assert any('info' in call for call in calls)


def test_accepted_status_after_wait_error_allows_publication(monkeypatch):
    notarize = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))['notarize']
    calls = []

    def command(*args):
        calls.append(args)
        if 'submit' in args:
            return '{"id":"submission-123"}'
        if 'wait' in args:
            raise RuntimeError('connection interrupted')
        return '{"status":"Accepted"}'

    monkeypatch.setitem(notarize.__globals__, 'run', command)
    monkeypatch.setenv('OPENPROGRAM_NOTARY_KEYCHAIN', '/private/ci.keychain-db')
    notarize(Path('/staged/app.zip'), 'test-profile')
    assert all('--keychain' in call and '/private/ci.keychain-db' in call for call in calls)


def test_missing_submission_id_stops_before_wait(monkeypatch):
    notarize = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))['notarize']
    calls = []

    def command(*args):
        calls.append(args)
        return '{}'

    monkeypatch.setitem(notarize.__globals__, 'run', command)
    with pytest.raises(RuntimeError, match='submission ID'):
        notarize(Path('/staged/app.zip'), 'test-profile')
    assert len(calls) == 1
