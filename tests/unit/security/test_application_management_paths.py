"""Program code is an ordinary place to write."""
import json
from pathlib import Path


def test_package_code_and_software_registration_are_writable(tmp_path, monkeypatch):
    import openprogram
    from openprogram.state import paths
    from openprogram.host import sandbox
    monkeypatch.setattr(openprogram, '__file__', str(tmp_path / 'openprogram/__init__.py'))
    monkeypatch.setattr(paths, 'get_state_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: None)
    harness = tmp_path / 'state' / 'programs' / 'demo_harness'
    harness.mkdir(parents=True)
    (tmp_path / 'state' / 'program-sources.json').write_text(json.dumps({
        'version': 2, 'programs': [{'path': str(harness), 'source': 'fixture', 'kind': 'git'}],
    }))
    for target in [harness / 'demo/agentics/__init__.py', tmp_path / 'state/programs/catalog.json',
                   tmp_path / 'state/program-sources.json',
                   tmp_path / 'openprogram/programs/applications/legacy/agent.py',
                   tmp_path / 'ordinary.txt',
                   tmp_path / 'state' / 'programs' / 'weekly_report' / 'run.py']:
        assert sandbox.validate_write_path(Path(target)) is None
