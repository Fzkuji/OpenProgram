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


def test_registry_is_available_while_session_locations_update(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.store.project.discovery import discover_moved_projects
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    old.rename(tmp_path / 'new')
    acquired_results = []
    def relocate_sessions(*args):
        def acquire_registry():
            acquired = projects._reg_lock.acquire(timeout=1)
            if acquired:
                projects._reg_lock.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as executor:
            acquired_results.append(executor.submit(acquire_registry).result(timeout=2))
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': relocate_sessions})())
    assert discover_moved_projects([tmp_path]) == [project.id]
    assert acquired_results == [True]
