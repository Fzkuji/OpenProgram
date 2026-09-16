#!/usr/bin/env python3
"""Sign and notarize a staged desktop application before producing release artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile

MACHO = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca',
    'cafebabf', 'bfbafeca',
)}


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=3600 if "notarytool" in args else 600)
    if result.returncode:
        # Arguments can contain credentials. Never echo them.
        detail = ''
        if not any(token in {'-P', '--password'} for token in args):
            lines = (result.stderr or result.stdout or '').strip().splitlines()
            if lines:
                detail = ': ' + ' | '.join(line[:200] for line in lines[-2:])
        raise RuntimeError(
            f'{Path(args[0]).name} failed (exit {result.returncode}){detail}'
        )
    return result.stdout


def create_dmg(image: Path, dmg_path: Path) -> None:
    args = (
        'hdiutil', 'create', '-volname', 'OpenProgram', '-srcfolder', str(image),
        '-format', 'UDZO', str(dmg_path),
    )
    last_error: RuntimeError | None = None
    for attempt in range(1, 6):
        try:
            run(*args)
            return
        except RuntimeError as exc:
            last_error = exc
            if 'Resource busy' not in str(exc) or attempt == 5:
                raise
            print(f'hdiutil create retry {attempt} after resource busy', flush=True)
            time.sleep(2 * attempt)
            if dmg_path.exists():
                dmg_path.unlink()
    raise last_error or RuntimeError('hdiutil create failed')


def codesign(*options: str) -> str:
    args = ['codesign']
    keychain = os.environ.get('OPENPROGRAM_NOTARY_KEYCHAIN')
    if keychain:
        args.extend(['--keychain', keychain])
    return run(*args, *options)


def clear_extended_attributes(app: Path) -> None:
    run('xattr', '-cr', str(app))


def uses_app_entitlements(item: Path, app: Path) -> bool:
    return item == app or item.suffix == '.app'


def macho_file(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in MACHO


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def update_adjacent_manifest(archive: Path, digests: dict[str, str]) -> None:
    manifest_path = archive.parent / 'manifest.json'
    if not manifest_path.is_file():
        return
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return
    languages = data.get('languages')
    if not isinstance(languages, dict):
        return
    changed = False
    for entry in languages.values():
        if not isinstance(entry, dict):
            continue
        name = entry.get('file')
        if name in digests:
            entry['sha256'] = digests[name]
            changed = True
    if changed:
        manifest_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def sign_binary(path: Path, identity: str) -> None:
    codesign('--force', '--sign', identity, '--timestamp', '--options', 'runtime', str(path))


def resign_tar_gz(archive: Path, identity: str) -> None:
    with tempfile.TemporaryDirectory(prefix='openprogram-archive-') as directory:
        work = Path(directory)
        extracted = work / 'extracted'
        extracted.mkdir()
        with tarfile.open(archive, 'r:gz') as tar:
            try:
                tar.extractall(extracted, filter='data')
            except TypeError:
                tar.extractall(extracted)
        digests: dict[str, str] = {}
        for item in extracted.rglob('*'):
            if not macho_file(item):
                continue
            sign_binary(item, identity)
            digest = file_digest(item)
            digests[item.name] = digest
            digests[str(item.relative_to(extracted))] = digest
        if not digests:
            return
        rebuilt = work / 'rebuilt.tar.gz'
        with tarfile.open(rebuilt, 'w:gz') as tar:
            for item in sorted(extracted.rglob('*')):
                tar.add(item, arcname=str(item.relative_to(extracted)), recursive=False)
        shutil.copyfile(rebuilt, archive)
        update_adjacent_manifest(archive, digests)


def resign_zip(archive: Path, identity: str) -> None:
    with tempfile.TemporaryDirectory(prefix='openprogram-archive-') as directory:
        work = Path(directory)
        extracted = work / 'extracted'
        extracted.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted)
        digests: dict[str, str] = {}
        for item in extracted.rglob('*'):
            if not macho_file(item):
                continue
            sign_binary(item, identity)
            digest = file_digest(item)
            digests[item.name] = digest
            digests[str(item.relative_to(extracted))] = digest
        if not digests:
            return
        rebuilt = work / 'rebuilt.zip'
        with zipfile.ZipFile(rebuilt, 'w') as bundle:
            for item in sorted(extracted.rglob('*')):
                if item.is_file():
                    bundle.write(item, arcname=str(item.relative_to(extracted)))
        shutil.copyfile(rebuilt, archive)
        update_adjacent_manifest(archive, digests)


def resign_packed_archives(app: Path, identity: str) -> None:
    for item in app.rglob('*'):
        if not item.is_file():
            continue
        name = item.name.lower()
        if name.endswith('.tar.gz') or name.endswith('.tgz'):
            resign_tar_gz(item, identity)
        elif name.endswith('.zip'):
            resign_zip(item, identity)


def notarize(path: Path, profile: str) -> None:
    keychain = os.environ.get('OPENPROGRAM_NOTARY_KEYCHAIN')
    auth = ['--keychain-profile', profile]
    if keychain:
        auth += ['--keychain', keychain]
    result = json.loads(run('xcrun', 'notarytool', 'submit', str(path),
                            *auth, '--output-format', 'json'))
    submission = result.get('id')
    if not isinstance(submission, str) or not submission:
        raise RuntimeError('Apple did not return a notarization submission ID')
    print(f'Notarization submitted: {submission}', flush=True)
    try:
        run('xcrun', 'notarytool', 'wait', submission, *auth, '--timeout', '45m')
    except (RuntimeError, subprocess.SubprocessError):
        # A timeout does not cancel Apple's processing. Query authoritative status
        # before deciding whether the artifact can be published.
        pass
    status = json.loads(run('xcrun', 'notarytool', 'info', submission,
                            *auth, '--output-format', 'json')).get('status')
    if status != 'Accepted':
        try:
            log = run('xcrun', 'notarytool', 'log', submission, *auth)
        except RuntimeError:
            log = ''
        if log.strip():
            print(log.strip()[:20000], flush=True)
        raise RuntimeError(f'Apple notarization {status}: {submission}. '
                           'No release artifacts were published; inspect this submission with notarytool info/log.')
    print(f'Notarization Accepted: {submission}', flush=True)


def sign(app: Path, identity: str, entitlements: Path) -> None:
    if app.is_symlink() or not app.is_dir():
        raise RuntimeError('A real staged application directory is required')
    app = app.resolve()
    if app == Path('/Applications/OpenProgram.app'):
        raise RuntimeError('Sign a staging application, never the installed application')
    with (app / 'Contents/Info.plist').open('rb') as stream:
        if plistlib.load(stream).get('CFBundleIdentifier') != 'ai.openprogram.desktop':
            raise RuntimeError('Expected the OpenProgram desktop application')
    files, bundles = [], []
    for item in app.rglob('*'):
        if item.is_symlink():
            if not item.resolve().is_relative_to(app):
                raise RuntimeError('Application symlink escapes its bundle')
            continue
        if item.is_file():
            with item.open('rb') as stream:
                if stream.read(4) in MACHO:
                    if item.stat().st_nlink != 1:
                        raise RuntimeError('Hard-linked executable in signing input')
                    files.append(item)
        elif item.is_dir() and item.suffix in ('.app', '.framework', '.xpc', '.bundle'):
            bundles.append(item)
    root_macos = app / 'Contents' / 'MacOS'
    nested = [item for item in files if item.parent != root_macos] + bundles
    nested.sort(key=lambda p: len(p.parts), reverse=True)
    clear_extended_attributes(app)
    resign_packed_archives(app, identity)
    for item in nested + [app]:
        print(f'Signing {item.relative_to(app) if item != app else app.name}', flush=True)
        args = ['--force', '--sign', identity, '--timestamp', '--options', 'runtime']
        if uses_app_entitlements(item, app):
            args.extend(['--entitlements', str(entitlements)])
        codesign(*args, str(item))
    codesign('--verify', '--deep', '--strict', str(app))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--arch', required=True, choices=('arm64', 'x64'))
    parser.add_argument('--identity', default=os.environ.get('OPENPROGRAM_SIGNING_IDENTITY'))
    parser.add_argument('--profile', default=os.environ.get('OPENPROGRAM_NOTARY_PROFILE'))
    args = parser.parse_args()
    if not args.identity or not args.profile:
        parser.error('Developer ID identity and notarization keychain profile are required')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.app / 'Contents/Info.plist').open('rb') as stream:
        version = plistlib.load(stream)['CFBundleShortVersionString']
    if not isinstance(version, str) or not all(c.isdigit() or c == '.' for c in version):
        raise RuntimeError('Invalid bundle version')
    name = f'OpenProgram-{version}-mac-{args.arch}'
    targets = [args.output / (name + extension) for extension in ('.zip', '.dmg')]
    if any(p.exists() for p in targets):
        raise RuntimeError('Release output already exists')
    entitlements = Path(__file__).resolve().parents[2] / 'apps/desktop/build/entitlements.mac.plist'
    sign(args.app, args.identity, entitlements)
    with tempfile.TemporaryDirectory(prefix='openprogram-notarize-') as directory:
        work = Path(directory)
        upload = work / 'submission.zip'
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(args.app), str(upload))
        notarize(upload, args.profile)
        run('xcrun', 'stapler', 'staple', str(args.app))
        run('xcrun', 'stapler', 'validate', str(args.app))
        run('spctl', '--assess', '--type', 'execute', '--verbose=2', str(args.app))
        zip_path, dmg_path = work / (name + '.zip'), work / (name + '.dmg')
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(args.app), str(zip_path))
        image = work / 'image'
        image.mkdir()
        run('ditto', str(args.app), str(image / 'OpenProgram.app'))
        (image / 'Applications').symlink_to('/Applications')
        create_dmg(image, dmg_path)
        codesign('--force', '--sign', args.identity, '--timestamp', str(dmg_path))
        notarize(dmg_path, args.profile)
        run('xcrun', 'stapler', 'staple', str(dmg_path))
        run('xcrun', 'stapler', 'validate', str(dmg_path))
        for source, target in zip((zip_path, dmg_path), targets):
            shutil.copyfile(source, target)
    print('Signed and notarized ZIP and DMG ready', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else 'macOS release packaging failed') from None
