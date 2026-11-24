"""Settings pages hold what they read until the Worker says the config
file changed, instead of reading again when an answer gets old."""
from __future__ import annotations

from pathlib import Path

from openprogram.host._platform import changes


def test_settings_pages_are_told_when_the_config_file_changes(tmp_path: Path, monkeypatch):
    from openprogram.interfaces.server.routes.settings import config as settings_config

    watched: list[tuple] = []
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(changes, "watch", lambda path, topic, **kw: watched.append((Path(path), topic, kw)))
    settings_config.watch_config()
    assert settings_config.SETTINGS_TOPIC in changes._published
    assert watched == [(tmp_path, "settings", {"names": {"config.json"}})]


def test_windows_are_told_when_a_saved_agent_changes(tmp_path: Path, monkeypatch):
    from openprogram.interfaces.server.routes.catalog import agents as agents_routes
    from openprogram.runtime.agent_registry import manager

    watched: list[tuple] = []
    monkeypatch.setattr(manager, "agent_dir", lambda agent_id: tmp_path / "agents" / agent_id)
    monkeypatch.setattr(changes, "watch", lambda path, topic, **kw: watched.append((Path(path), topic, kw)))
    agents_routes.watch_agents()
    assert agents_routes.AGENTS_TOPIC in changes._published
    assert watched == [(tmp_path / "agents", "agents", {"recursive": True, "names": {"agent.json"}})]
