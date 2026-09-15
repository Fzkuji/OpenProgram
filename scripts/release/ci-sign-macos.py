#!/usr/bin/env python3
"""Install CI signing credentials in an ephemeral keychain and run release signing."""
import base64
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} {args[1]} failed')
    return result.stdout


def main():
    required = ('MAC_CSC_LINK', 'MAC_CSC_KEY_PASSWORD', 'APPLE_ID',
                'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID')
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError('All macOS signing and notarization secrets are required')
    original = shlex.split(run('security', 'list-keychains', '-d', 'user'))
    with tempfile.TemporaryDirectory(prefix='openprogram-ci-signing-') as directory:
        work = Path(directory)
        keychain = str(work / 'signing.keychain-db')
        password = secrets.token_hex(16)
        try:
            p12 = work / 'identity.p12'
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
            profile = 'OpenProgram-CI'
            run('xcrun', 'notarytool', 'store-credentials', profile,
                '--apple-id', os.environ['APPLE_ID'], '--team-id', os.environ['APPLE_TEAM_ID'],
                '--password', os.environ['APPLE_APP_SPECIFIC_PASSWORD'], '--keychain', keychain)
            # Keep notarization credentials scoped to this temporary keychain.
            env = {name: value for name, value in os.environ.items() if name not in required}
            env['OPENPROGRAM_NOTARY_KEYCHAIN'] = keychain
            command = [sys.executable, str(Path(__file__).with_name('sign-macos-release.py')),
                       *sys.argv[1:], '--profile', profile]
            result = subprocess.run(command, env=env)
            if result.returncode:
                raise RuntimeError('macOS release signing failed')
        finally:
            run('security', 'list-keychains', '-d', 'user', '-s', *original)
            if Path(keychain).exists():
                run('security', 'delete-keychain', keychain)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else 'CI signing setup failed') from None
