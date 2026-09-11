"""New management locations retain the non-configurable write boundary."""
from pathlib import Path


def test_package_code_and_software_registration_are_protected(tmp_path, monkeypatch):
    import openprogram
    from openprogram import paths, sandbox
    from openprogram.protected_paths import packages_root, application_catalog_path
    monkeypatch.setattr(openprogram, '__file__', str(tmp_path / 'openprogram/__init__.py'))
    monkeypatch.setattr(paths, 'get_state_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: None)
    for target in [Path(packages_root()) / 'package/agent.py', Path(application_catalog_path()),
                   tmp_path / 'openprogram/programs/applications/legacy/agent.py']:
        assert sandbox.validate_write_path(target) is not None
    assert sandbox.validate_write_path(tmp_path / 'ordinary.txt') is None
