"""Public startup and website artifact regressions for Windows users."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_dispatcher_import_does_not_require_unix_fcntl():
    result = subprocess.run(
        [sys.executable, '-c',
         """import builtins
original_import = builtins.__import__
def platform_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'fcntl' and (globals or {}).get('__name__') == 'openprogram.agent.permissions.file_state':
        raise ModuleNotFoundError("No module named 'fcntl'")
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = platform_import
from openprogram.agent.dispatcher import process_user_turn
assert callable(process_user_turn)
"""],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which('bash') is None, reason='site publishes on Linux with bash')
def test_site_assembly_publishes_both_root_installers(tmp_path):
    workflow = yaml.safe_load((ROOT / '.github/workflows/docs-pages.yml').read_text())
    assembly = next(step['run'] for step in workflow['jobs']['publish']['steps']
                    if step.get('name') == 'Assemble the site')
    site = tmp_path / 'docs/_site'
    shutil.copytree(ROOT / 'docs/_static_root', site)
    website = tmp_path / 'website'
    website.mkdir()
    (website / 'index.html').write_text('<!doctype html><title>fixture</title>')
    result = subprocess.run(['bash', '-e', '-c', assembly], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    for source, destination in [('install.sh', 'install'), ('install.ps1', 'install.ps1')]:
        assert (tmp_path / '_publish' / destination).read_bytes() == (site / source).read_bytes()
