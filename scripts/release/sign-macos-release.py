#!/usr/bin/env python3
"""Sign and notarize a staged desktop application before producing release artifacts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

MACHO = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca',
    'cafebabf', 'bfbafeca',
)}


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=3600 if "notarytool" in args else 600)
    if result.returncode:
        # Arguments can contain credentials. Never echo them or subprocess output.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode})')
    return result.stdout


def notarize(path: Path, profile: str) -> None:
    keychain = os.environ.get('OPENPROGRAM_NOTARY_KEYCHAIN')
    extra = ['--keychain', keychain] if keychain else []
    result = json.loads(run('xcrun', 'notarytool', 'submit', str(path),
                            '--keychain-profile', profile, '--wait', '--output-format', 'json', *extra))
    if result.get('status') != 'Accepted':
        raise RuntimeError(f'Apple notarization did not accept the artifact: {result.get("id", "unknown")}')
    print(f'Notarization Accepted: {result["id"]}', flush=True)


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
    for item in files + sorted(bundles, key=lambda p: len(p.parts), reverse=True) + [app]:
        print(f'Signing {item.relative_to(app) if item != app else app.name}', flush=True)
        run('codesign', '--force', '--sign', identity, '--timestamp', '--options', 'runtime',
            '--entitlements', str(entitlements), str(item))
    run('codesign', '--verify', '--deep', '--strict', str(app))


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
        run('hdiutil', 'create', '-volname', 'OpenProgram', '-srcfolder', str(image),
            '-format', 'UDZO', str(dmg_path))
        run('codesign', '--force', '--sign', args.identity, '--timestamp', str(dmg_path))
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
