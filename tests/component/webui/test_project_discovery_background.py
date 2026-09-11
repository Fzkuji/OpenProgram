"""Background relocation publishes the existing project invalidation event."""
import asyncio

from openprogram.store.project import project_store as projects
from openprogram.store.project.discovery import run_discovery


def test_background_move_updates_registry_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args: 0})())
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    old.rename(tmp_path / 'new')
    async def exercise():
        stop = asyncio.Event()
        notices = []
        def notify():
            notices.append(projects.get_project(project.id).path)
            stop.set()
        await asyncio.wait_for(run_discovery(stop, notify), timeout=5)
        assert notices == [str(tmp_path / 'new')]
    asyncio.run(exercise())
