#!/usr/bin/env python3
"""Package the managed interpreter with a stable, visible macOS identity."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

NAME = 'OpenProgram'
BUNDLE = 'OpenProgram Runtime.app'
IDENTIFIER = 'ai.openprogram.runtime'
RELATIVE = f'{BUNDLE}/Contents/MacOS/{NAME}'


def build(root: Path, python: Path, icon: Path) -> Path:
    root, python = root.resolve(), python.resolve()
    python.relative_to(root)
    prefix = python.parent.parent
    version = subprocess.check_output([str(python), '-I', '-c',
        'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], text=True).strip()
    library = prefix / 'lib' / f'libpython{version}.dylib'
    old_id = subprocess.check_output(['otool', '-D', str(library)], text=True).splitlines()[1].strip()
    target = root / BUNDLE
    stage = Path(tempfile.mkdtemp(prefix='.openprogram-runtime-', suffix='.app', dir=root))
    try:
        contents = stage / 'Contents'
        executable = contents / 'MacOS' / NAME
        executable.parent.mkdir(parents=True)
        resources = contents / 'Resources'
        resources.mkdir()
        shutil.copyfile(icon, resources / 'icon.icns')
        with (contents / 'Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': IDENTIFIER, 'CFBundleName': NAME,
                'CFBundleDisplayName': NAME, 'CFBundleExecutable': NAME,
                'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1',
                'CFBundleIconFile': 'icon.icns', 'LSUIElement': True,
                'NSHighResolutionCapable': True}, stream, sort_keys=True)
        relative_home = os.path.relpath(prefix, executable.parent)
        subprocess.run(['clang', str(Path(__file__).with_name('mac-runtime-main.c')),
            '-I' + str(prefix / 'include' / f'python{version}'), '-L' + str(prefix / 'lib'),
            '-mmacosx-version-min=11.0', '-lpython' + version, '-DPYTHON_HOME_RELATIVE=' + json.dumps(relative_home),
            '-o', str(executable)], check=True)
        subprocess.run(['install_name_tool', '-change', old_id,
            '@loader_path/' + os.path.relpath(library, executable.parent), str(executable)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', '--identifier', IDENTIFIER,
            '--timestamp=none', str(stage)], check=True)
        subprocess.run(['codesign', '--verify', '--strict', str(stage)], check=True)
        # Exercise the real embedding and bundle discovery without requesting OS access.
        output = subprocess.check_output([str(executable), '-I', '-B', '-c',
            'import Foundation,sys,json; print(json.dumps([Foundation.NSBundle.mainBundle().bundleIdentifier(),sys.prefix]))'], text=True)
        identity, home = json.loads(output)
        if identity != IDENTIFIER or Path(home).resolve() != prefix:
            raise RuntimeError('Named runtime identity or Python home does not match')
        # Preserve existing executable inode and signature when nothing changed.
        if target.exists():
            files = [p.relative_to(stage) for p in stage.rglob('*') if p.is_file()]
            target_files = {p.relative_to(target) for p in target.rglob('*') if p.is_file()}
            if target_files == set(files) and all((target / p).is_file() and (target / p).read_bytes() == (stage / p).read_bytes() for p in files):
                return target / 'Contents' / 'MacOS' / NAME
            backup = stage.with_name(stage.name + '.previous')
            target.rename(backup)
            try:
                stage.rename(target)
            except BaseException:
                backup.rename(target)
                raise
            shutil.rmtree(backup)
        else:
            stage.rename(target)
        return target / 'Contents' / 'MacOS' / NAME
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime_root', type=Path)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--icon', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.runtime_root, args.python, args.icon))
