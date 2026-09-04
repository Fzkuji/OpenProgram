"""Never publish fixture login entries into the actual user's LaunchAgents."""
import pytest


@pytest.fixture(autouse=True)
def fixture_recovery_agents(tmp_path, monkeypatch):
    from openprogram.self_update import bootstrap
    monkeypatch.setattr(bootstrap, "_agents_directory", lambda: tmp_path / "Library/LaunchAgents")
