#!/usr/bin/env python3
"""Sign macOS release builds in CI.

    ci-sign-macos.py setup     install the credentials in a temporary keychain
    ci-sign-macos.py sign      Developer ID signature and notarization, using it
    ci-sign-macos.py cleanup   remove the keychain (run it even after a failure)
    ci-sign-macos.py adhoc     no credentials: an ad-hoc signature and -unsigned names

The keychain lives in a directory of its own under RUNNER_TEMP, so that seed
signing and App signing in one job share it and cleanup finds it.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import runpy
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

REQUIRED = ('MAC_CSC_LINK', 'MAC_CSC_KEY_PASSWORD', 'APPLE_ID',
            'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID')
PROFILE = 'OpenProgram-CI'
SIGNER = Path(__file__).with_name('sign-macos-release.py')


def redacted(text):
    """What a tool said, with no secret value in it."""
    for key in REQUIRED:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, '***')
    return text.strip()


def run(*args, attempts=1):
    """Run a tool; on failure say what it said, never what it was given.

    ``attempts`` above one is for a call that reaches Apple over the network,
    which fails now and then for reasons a second try clears.
    """
    for attempt in range(1, attempts + 1):
        result = subprocess.run(args, capture_output=True, text=True)
        if not result.returncode:
            return result.stdout
        said = redacted((result.stderr or '') + (result.stdout or ''))
        print(f'{Path(args[0]).name} {args[1]} failed (try {attempt} of {attempts}):\n{said}',
              file=sys.stderr, flush=True)
        if attempt < attempts:
            time.sleep(20 * attempt)
    raise RuntimeError(f'{Path(args[0]).name} {args[1]} failed')


def state_directory():
    return Path(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()) / 'openprogram-ci-signing'


def setup():
    if any(not os.environ.get(key) for key in REQUIRED):
        raise RuntimeError('All macOS signing and notarization secrets are required')
    work = state_directory()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, mode=0o700)
    original = shlex.split(run('security', 'list-keychains', '-d', 'user'))
    (work / 'original-keychains.json').write_text(json.dumps(original), encoding="utf-8")
    keychain = str(work / 'signing.keychain-db')
    password = secrets.token_hex(16)
    p12 = work / 'identity.p12'
    try:
        p12.write_bytes(base64.b64decode(os.environ['MAC_CSC_LINK'], validate=True))
        p12.chmod(0o600)
        run('security', 'create-keychain', '-p', password, keychain)
        run('security', 'set-keychain-settings', '-lut', '21600', keychain)
        run('security', 'unlock-keychain', '-p', password, keychain)
        run('security', 'import', str(p12), '-k', keychain,
            '-P', os.environ['MAC_CSC_KEY_PASSWORD'], '-T', '/usr/bin/codesign')
        run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-s', '-k', password, keychain)
        run('security', 'list-keychains', '-d', 'user', '-s', *original, keychain)
        # The runner's Xcode installation provides Apple's certificate chain.
        run('xcrun', 'notarytool', 'store-credentials', PROFILE,
            '--apple-id', os.environ['APPLE_ID'], '--team-id', os.environ['APPLE_TEAM_ID'],
            '--password', os.environ['APPLE_APP_SPECIFIC_PASSWORD'], '--keychain', keychain,
            attempts=3)
    finally:
        p12.unlink(missing_ok=True)


def sign(arguments):
    keychain = state_directory() / 'signing.keychain-db'
    if not keychain.exists():
        raise RuntimeError('The signing keychain is missing; run setup first')
    # Keep notarization credentials scoped to this temporary keychain.
    environment = {name: value for name, value in os.environ.items() if name not in REQUIRED}
    environment['OPENPROGRAM_NOTARY_KEYCHAIN'] = str(keychain)
    result = subprocess.run([sys.executable, str(SIGNER), *arguments, '--profile', PROFILE], env=environment)
    if result.returncode:
        raise RuntimeError('macOS release signing failed')


def cleanup():
    work = state_directory()
    original_file = work / 'original-keychains.json'
    if original_file.exists():
        run('security', 'list-keychains', '-d', 'user', '-s', *json.loads(original_file.read_text(encoding="utf-8")))
    keychain = work / 'signing.keychain-db'
    if keychain.exists():
        run('security', 'delete-keychain', str(keychain))
    shutil.rmtree(work, ignore_errors=True)


def adhoc(options):
    """Package a staged App with no Apple credentials.

    Apple Silicon refuses code with no valid signature, so everything is signed
    ad hoc, deepest first. The hardened runtime stays off: with no Team ID it
    would make every library fail validation.
    """
    signer = runpy.run_path(str(SIGNER))
    app = options.app.resolve()
    with (app / 'Contents/Info.plist').open('rb') as stream:
        version = plistlib.load(stream)['CFBundleShortVersionString']
    if not isinstance(version, str) or not all(c.isdigit() or c == '.' for c in version):
        raise RuntimeError('Invalid bundle version')
    options.output.mkdir(parents=True, exist_ok=True)
    name = f'OpenProgram-{version}-mac-{options.arch}-unsigned'
    zip_path, dmg_path = options.output / f'{name}.zip', options.output / f'{name}.dmg'
    if zip_path.exists() or dmg_path.exists():
        raise RuntimeError('Release output already exists')
    signer['clear_extended_attributes'](app)
    code = []
    for item in app.rglob('*'):
        if item.is_symlink():
            continue
        if item.is_file():
            with item.open('rb') as stream:
                if stream.read(4) in signer['MACHO']:
                    code.append(item)
        elif item.is_dir() and item.suffix in ('.app', '.framework', '.xpc', '.bundle'):
            code.append(item)
    for item in sorted(code, key=lambda path: len(path.parts), reverse=True):
        run('codesign', '--force', '--sign', '-', '--timestamp=none', str(item))
    run('codesign', '--force', '--sign', '-', '--timestamp=none', str(app))
    run('codesign', '--verify', '--deep', '--strict', str(app))
    run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(zip_path))
    with tempfile.TemporaryDirectory(prefix='openprogram-adhoc-') as directory:
        image = Path(directory) / 'image'
        image.mkdir()
        run('ditto', str(app), str(image / 'OpenProgram.app'))
        (image / 'Applications').symlink_to('/Applications')
        signer['create_dmg'](image, dmg_path)
    print('Ad-hoc signed ZIP and DMG ready', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('setup')
    commands.add_parser('cleanup')
    commands.add_parser('sign', help='takes the arguments of sign-macos-release.py')
    adhoc_parser = commands.add_parser('adhoc')
    adhoc_parser.add_argument('--app', required=True, type=Path)
    adhoc_parser.add_argument('--output', required=True, type=Path)
    adhoc_parser.add_argument('--arch', required=True, choices=('arm64', 'x64'))
    options, passed_on = parser.parse_known_args()
    if passed_on and options.command != 'sign':
        parser.error(f'unrecognized arguments: {" ".join(passed_on)}')
    if options.command == 'setup':
        setup()
    elif options.command == 'cleanup':
        cleanup()
    elif options.command == 'sign':
        sign(passed_on)
    else:
        adhoc(options)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else 'CI signing setup failed') from None
