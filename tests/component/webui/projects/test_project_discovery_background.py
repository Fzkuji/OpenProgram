"""Startup reconciliation publishes invalidation without idle discovery."""
import asyncio

from openprogram.state.store.project import project_store as projects
from openprogram.state.store.project.discovery import run_discovery


def test_background_move_updates_registry_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(
        'openprogram.state.store.session.session_store.default_store',
        lambda: type('Store', (), {'relocate_project_sessions': lambda *args, **kwargs: 0})())
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    old.rename(tmp_path / 'new')
    async def exercise():
        stop = asyncio.Event()
        notices = []
        def notify():
            notices.append(projects.get_project(project.id).path)
            stop.set()
        await asyncio.wait_for(run_discovery(stop, notify), timeout=30)
        assert notices == [str(tmp_path / 'new')]
    asyncio.run(exercise())


def test_registry_is_available_while_session_locations_update(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.state.store.project.discovery import discover_moved_projects
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    old.rename(tmp_path / 'new')
    acquired_results = []
    def relocate_sessions(*args, **kwargs):
        def acquire_registry():
            acquired = projects._reg_lock.acquire(timeout=1)
            if acquired:
                projects._reg_lock.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as executor:
            acquired_results.append(executor.submit(acquire_registry).result(timeout=30))
    monkeypatch.setattr(
        'openprogram.state.store.session.session_store.default_store',
        lambda: type('Store', (), {'relocate_project_sessions': relocate_sessions})())
    assert discover_moved_projects([tmp_path]) == [project.id]
    assert acquired_results == [True]



def test_startup_reconciles_once_then_waits_for_shutdown(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        'openprogram.state.store.project.discovery.reconcile_registered_projects',
        lambda: calls.append("reconcile") or [])
    async def exercise():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(run_discovery(stop, lambda: calls.append("notify")), timeout=30)
    asyncio.run(exercise())
    assert calls == ["reconcile"]
