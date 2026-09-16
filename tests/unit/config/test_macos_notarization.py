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


def test_codesign_passes_ci_keychain(monkeypatch):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    calls = []

    def command(*args):
        calls.append(args)
        return ''

    monkeypatch.setitem(ns['codesign'].__globals__, 'run', command)
    monkeypatch.setenv('OPENPROGRAM_NOTARY_KEYCHAIN', '/private/ci.keychain-db')
    ns['codesign']('--force', '--sign', 'Developer ID Application: Example')
    assert calls == [(
        'codesign', '--keychain', '/private/ci.keychain-db',
        '--force', '--sign', 'Developer ID Application: Example',
    )]
    monkeypatch.delenv('OPENPROGRAM_NOTARY_KEYCHAIN')
    ns['codesign']('--verify', 'OpenProgram.app')
    assert calls[-1] == ('codesign', '--verify', 'OpenProgram.app')


def test_sign_clears_xattrs_and_uses_keychain(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    app = tmp_path / 'OpenProgram.app'
    macos = app / 'Contents' / 'MacOS'
    macos.mkdir(parents=True)
    import plistlib
    with (app / 'Contents' / 'Info.plist').open('wb') as stream:
        plistlib.dump({
            'CFBundleIdentifier': 'ai.openprogram.desktop',
            'CFBundleShortVersionString': '0.9.3',
        }, stream)
    (macos / 'OpenProgram').write_bytes(bytes.fromhex('feedfacf') + b'\0' * 16)
    calls = []

    def command(*args):
        calls.append(args)
        return ''

    monkeypatch.setitem(ns['sign'].__globals__, 'run', command)
    monkeypatch.setenv('OPENPROGRAM_NOTARY_KEYCHAIN', '/private/ci.keychain-db')
    ns['sign'](
        app,
        'Developer ID Application: Example',
        ROOT / 'apps' / 'desktop' / 'build' / 'entitlements.mac.plist',
    )
    assert calls[0] == ('xattr', '-cr', str(app.resolve()))
    signed = [call for call in calls if call and call[0] == 'codesign']
    assert signed
    assert all('--keychain' in call and '/private/ci.keychain-db' in call for call in signed)


def test_run_includes_stderr_without_arguments(monkeypatch):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))

    class Result:
        returncode = 1
        stderr = 'resource fork, Finder information, or similar detritus not allowed\n'
        stdout = ''

    monkeypatch.setattr(ns['subprocess'], 'run', lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError) as error:
        ns['run']('codesign', '--sign', 'secret-identity', 'target')
    message = str(error.value)
    assert 'secret-identity' not in message
    assert 'detritus not allowed' in message
    assert 'codesign failed (exit 1)' in message
