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


def _staged_app(tmp_path, nested_framework=False):
    import plistlib
    app = tmp_path / 'OpenProgram.app'
    macos = app / 'Contents' / 'MacOS'
    macos.mkdir(parents=True)
    with (app / 'Contents' / 'Info.plist').open('wb') as stream:
        plistlib.dump({
            'CFBundleIdentifier': 'ai.openprogram.desktop',
            'CFBundleShortVersionString': '0.9.4',
        }, stream)
    macho = bytes.fromhex('feedfacf') + b'\0' * 16
    (macos / 'OpenProgram').write_bytes(macho)
    framework_binary = None
    if nested_framework:
        framework = (
            app / 'Contents' / 'Frameworks' / 'Electron Framework.framework'
            / 'Versions' / 'A'
        )
        framework.mkdir(parents=True)
        framework_binary = framework / 'Electron Framework'
        framework_binary.write_bytes(macho)
    resolved = app.resolve()
    return (
        resolved,
        (resolved / 'Contents' / 'MacOS' / 'OpenProgram'),
        None if framework_binary is None else (
            resolved / 'Contents' / 'Frameworks' / 'Electron Framework.framework'
            / 'Versions' / 'A' / 'Electron Framework'
        ),
    )


def test_sign_clears_xattrs_and_uses_keychain(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    app, _main, _framework = _staged_app(tmp_path)
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
    assert calls[0] == ('xattr', '-cr', str(app))
    signed = [call for call in calls if call and call[0] == 'codesign']
    assert signed
    assert all('--keychain' in call and '/private/ci.keychain-db' in call for call in signed)


def test_sign_nested_framework_before_outer_app(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    app, main_binary, framework_binary = _staged_app(tmp_path, nested_framework=True)
    calls = []

    def command(*args):
        calls.append(args)
        return ''

    monkeypatch.setitem(ns['sign'].__globals__, 'run', command)
    ns['sign'](
        app,
        'Developer ID Application: Example',
        ROOT / 'apps' / 'desktop' / 'build' / 'entitlements.mac.plist',
    )
    signed = [
        call[-1] for call in calls
        if call and call[0] == 'codesign' and '--sign' in call
    ]
    assert signed[0] == str(framework_binary)
    assert signed[-1] == str(app)
    assert str(main_binary) not in signed
    framework_bundle = app / 'Contents' / 'Frameworks' / 'Electron Framework.framework'
    assert str(framework_bundle) in signed
    assert signed.index(str(framework_binary)) < signed.index(str(framework_bundle))
    assert signed.index(str(framework_bundle)) < signed.index(str(app))


def test_run_includes_stderr_without_arguments(monkeypatch):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))

    class Result:
        returncode = 1
        stderr = (
            'code object is not signed at all\n'
            'In subcomponent: /tmp/OpenProgram.app/Contents/Frameworks/'
            'Electron Framework.framework\n'
        )
        stdout = ''

    monkeypatch.setattr(ns['subprocess'], 'run', lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError) as error:
        ns['run']('codesign', '--sign', 'secret-identity', 'target')
    message = str(error.value)
    assert 'secret-identity' not in message
    assert 'code object is not signed at all' in message
    assert 'Electron Framework.framework' in message
    assert 'codesign failed (exit 1)' in message
