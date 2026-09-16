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
        if 'log' in args:
            return '{"status":"Invalid","issues":[{"message":"invalid entitlement"}]}'
        return '{"status":"' + status + '"}'

    monkeypatch.setitem(notarize.__globals__, 'run', command)
    with pytest.raises(RuntimeError, match=status + ': submission-123'):
        notarize(Path('/staged/app.zip'), 'test-profile')
    captured = capsys.readouterr().out
    assert 'Notarization submitted: submission-123' in captured
    assert 'invalid entitlement' in captured
    assert any('info' in call for call in calls)
    assert any('log' in call for call in calls)


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
            'CFBundleShortVersionString': '0.9.5',
        }, stream)
    macho = bytes.fromhex('feedfacf') + b'\0' * 16
    (macos / 'OpenProgram').write_bytes(macho)
    if nested_framework:
        framework = (
            app / 'Contents' / 'Frameworks' / 'Electron Framework.framework'
            / 'Versions' / 'A'
        )
        framework.mkdir(parents=True)
        (framework / 'Electron Framework').write_bytes(macho)
        helper = (
            app / 'Contents' / 'Frameworks' / 'OpenProgram Helper.app'
            / 'Contents' / 'MacOS'
        )
        helper.mkdir(parents=True)
        (helper / 'OpenProgram Helper').write_bytes(macho)
        chrome = (
            app / 'Contents' / 'Resources' / 'Google Chrome for Testing Helper.app'
            / 'Contents' / 'MacOS'
        )
        chrome.mkdir(parents=True)
        (chrome / 'Google Chrome for Testing Helper').write_bytes(macho)
        (app / 'Contents' / 'Resources').mkdir(parents=True, exist_ok=True)
        (app / 'Contents' / 'Resources' / 'native.so').write_bytes(macho)
    resolved = app.resolve()
    extras = {}
    if nested_framework:
        extras = {
            'framework': (
                resolved / 'Contents' / 'Frameworks'
                / 'Electron Framework.framework' / 'Versions' / 'A'
                / 'Electron Framework'
            ),
            'helper_app': resolved / 'Contents' / 'Frameworks' / 'OpenProgram Helper.app',
            'chrome_app': (
                resolved / 'Contents' / 'Resources'
                / 'Google Chrome for Testing Helper.app'
            ),
            'native': resolved / 'Contents' / 'Resources' / 'native.so',
        }
    return resolved, resolved / 'Contents' / 'MacOS' / 'OpenProgram', extras


def test_sign_clears_xattrs_and_uses_keychain(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    app, _main, _extras = _staged_app(tmp_path)
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


def _sign_calls(calls):
    return [
        call for call in calls
        if call and call[0] == 'codesign' and '--sign' in call
    ]


def test_sign_nested_framework_before_outer_app(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    app, main_binary, extras = _staged_app(tmp_path, nested_framework=True)
    calls = []

    def command(*args):
        calls.append(args)
        return ''

    monkeypatch.setitem(ns['sign'].__globals__, 'run', command)
    entitlements = ROOT / 'apps' / 'desktop' / 'build' / 'entitlements.mac.plist'
    ns['sign'](app, 'Developer ID Application: Example', entitlements)
    signed = _sign_calls(calls)
    targets = [call[-1] for call in signed]
    framework_binary = extras['framework']
    framework_bundle = app / 'Contents' / 'Frameworks' / 'Electron Framework.framework'
    assert targets[-1] == str(app)
    assert str(main_binary) not in targets
    assert str(framework_bundle) in targets
    assert targets.index(str(framework_binary)) < targets.index(str(framework_bundle))
    assert targets.index(str(framework_bundle)) < targets.index(str(app))
    assert targets.index(str(extras['helper_app'])) < targets.index(str(app))
    assert targets.index(str(extras['chrome_app'])) < targets.index(str(app))
    by_target = {call[-1]: call for call in signed}
    assert '--entitlements' in by_target[str(app)]
    assert str(entitlements) in by_target[str(app)]
    assert '--entitlements' in by_target[str(extras['helper_app'])]
    assert '--entitlements' in by_target[str(extras['chrome_app'])]
    assert '--entitlements' not in by_target[str(framework_binary)]
    assert '--entitlements' not in by_target[str(framework_bundle)]
    assert '--entitlements' not in by_target[str(extras['native'])]


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


def test_create_dmg_retries_resource_busy(monkeypatch, tmp_path):
    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    calls = []

    def command(*args):
        calls.append(args)
        if len(calls) < 3:
            raise RuntimeError('hdiutil failed (exit 1): hdiutil: create failed - Resource busy')
        return ''

    monkeypatch.setitem(ns['create_dmg'].__globals__, 'run', command)
    monkeypatch.setattr(ns['time'], 'sleep', lambda seconds: None)
    ns['create_dmg'](tmp_path / 'image', tmp_path / 'OpenProgram.dmg')
    assert len(calls) == 3
    assert all(call[0] == 'hdiutil' and 'create' in call for call in calls)


def test_resign_tar_gz_signs_packed_dylib_and_updates_manifest(monkeypatch, tmp_path):
    import hashlib
    import json
    import tarfile

    ns = runpy.run_path(str(ROOT / 'scripts/release/sign-macos-release.py'))
    archive_dir = tmp_path / 'macos-arm64'
    archive_dir.mkdir()
    dylib = 'libtree_sitter_python.dylib'
    payload = bytes.fromhex('feedfacf') + b'\0' * 16
    source = tmp_path / dylib
    source.write_bytes(payload)
    archive = archive_dir / 'bundle.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(source, arcname=dylib)
    original = hashlib.sha256(payload).hexdigest()
    (archive_dir / 'manifest.json').write_text(json.dumps({
        'platform': 'macos-arm64',
        'archive': 'bundle.tar.gz',
        'languages': {
            'python': {
                'file': dylib,
                'symbol': 'tree_sitter_python',
                'sha256': original,
            },
        },
    }))
    calls = []

    def command(*args):
        calls.append(args)
        if args and args[0] == 'codesign':
            path = Path(args[-1])
            path.write_bytes(path.read_bytes() + b'SIGN')
        return ''

    monkeypatch.setitem(ns['resign_tar_gz'].__globals__, 'run', command)
    ns['resign_tar_gz'](archive, 'Developer ID Application: Example')
    assert any(call[0] == 'codesign' and call[-1].endswith(dylib) for call in calls)
    with tarfile.open(archive, 'r:gz') as tar:
        extracted = tar.extractfile(dylib).read()
    assert extracted.endswith(b'SIGN')
    manifest = json.loads((archive_dir / 'manifest.json').read_text())
    digest = hashlib.sha256(extracted).hexdigest()
    assert manifest['languages']['python']['sha256'] == digest
    assert digest != original
