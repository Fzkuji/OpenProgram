"""Project location states on the public WS/execute boundary."""
from __future__ import annotations

import asyncio
import json
import shutil

from openprogram.state.store.project import project_store as projects
from openprogram.state.store.session.session_store import SessionStore
from openprogram.interfaces.server.ws_actions import project as ws_project


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    store = SessionStore(state / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    return store


def test_list_projects_reports_missing_and_counts(tmp_path, monkeypatch):
    """A listing shows the location the registry records; the check behind
    the reply records the deleted folder and has every page list again."""
    from openprogram.interfaces.server import server

    store = _store(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder))
    store.update_session("s1", archived=True)
    shutil.rmtree(folder)
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: broadcasts.append(json.loads(msg)))
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("-inf"))
    monkeypatch.setattr(ws_project, "_reconcile_queued_for", float("-inf"))
    # Returning from ``asyncio.run`` waits for the check behind the reply.
    asyncio.run(ws_project.handle_list_projects(FakeWS(), {"session_id": ""}))
    assert [b["type"] for b in broadcasts] == ["projects_changed"]
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("inf"))
    ws = FakeWS()
    asyncio.run(ws_project.handle_list_projects(ws, {"session_id": ""}))
    payload = ws.sent[0]["data"]
    row = next(p for p in payload["projects"] if not p["is_default"] and p["path"].endswith("paper"))
    assert row["path_missing"] is True
    assert row["location_state"] in {"missing", "replaced"}
    assert row["session_count"] == 1
    assert row["unarchived_session_count"] == 0


def test_a_listing_touches_no_project_folder(tmp_path, monkeypatch):
    """Answering made a folder check and a marker read per project. Each of
    those calls hands the interpreter to whichever thread is computing, so
    126 projects took 4 to 6 seconds beside one busy thread. The answer
    reads the registry file and nothing under any project's folder, however
    many projects there are and whether their folders exist."""
    import builtins
    import os

    _store(tmp_path, monkeypatch)
    work = tmp_path / "work"

    made = []

    def register(count):
        for index in range(count):
            folder = work / f"p{len(made)}"
            made.append(folder)
            folder.mkdir(parents=True)
            project = projects.resolve_project(folder)
            if index % 2:
                shutil.rmtree(folder)
                projects.set_location_state(project.id, "missing")

    calls = []

    def recording(real):
        def call(path, *args, **kwargs):
            calls.append(os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else path)
            return real(path, *args, **kwargs)
        return call

    def listing_calls():
        calls.clear()
        with monkeypatch.context() as patch:
            for name in ("stat", "lstat", "scandir", "listdir", "open"):
                patch.setattr(os, name, recording(getattr(os, name)))
            patch.setattr(builtins, "open", recording(builtins.open))
            snapshot = ws_project._projects_snapshot(None)
        assert snapshot["status"] == "ready"
        return snapshot, list(calls)

    register(4)
    ws_project._projects_snapshot(None)  # the registry file is read once
    few, few_calls = listing_calls()
    register(20)
    ws_project._projects_snapshot(None)
    many, many_calls = listing_calls()

    assert len(many["projects"]) == len(few["projects"]) + 20
    assert not [path for path in many_calls if str(path).startswith(str(work))]
    assert len(many_calls) == len(few_calls)
    states = {row["path"]: row for row in many["projects"] if row["path"].startswith(str(work))}
    assert sorted({row["location_state"] for row in states.values()}) == ["available", "missing"]
    assert all(row["path_missing"] == (row["location_state"] == "missing") for row in states.values())


def test_a_listing_inside_the_interval_is_checked_at_its_end(monkeypatch):
    """A folder deleted just after a check would stay "available" until
    somebody listed again; the listing inside the interval queues one run."""
    import time

    monkeypatch.setattr(ws_project, "_reconcile_running", False)
    monkeypatch.setattr(ws_project, "_reconcile_queued_for", float("-inf"))
    monkeypatch.setattr(ws_project, "_reconcile_started_at", time.monotonic())
    wait = ws_project._claim_reconcile()
    assert 0 < wait <= ws_project.RECONCILE_INTERVAL_S
    assert ws_project._claim_reconcile() is None  # one queued run, not one each

    started = []

    class Loop:
        def run_in_executor(self, executor, work):
            started.append(work)

    ws_project._start_queued_reconcile(Loop())
    assert started == [ws_project._reconcile_projects]
    assert ws_project._reconcile_running is True
    assert ws_project._claim_reconcile() is None


def test_relocate_rejects_another_projects_folder(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    pa = projects.resolve_project(a)
    pb = projects.resolve_project(b)
    ws = FakeWS()
    asyncio.run(ws_project.handle_relocate_project(ws, {
        "project_id": pa.id, "path": str(b), "replace_identity": True,
    }))
    data = next(f["data"] for f in ws.sent if f["type"] == "project_relocated")
    assert data["ok"] is False
    assert "already the project" in (data["error"] or "")
    assert projects.get_project(pb.id).path.endswith("b")


def test_relocate_folds_a_missing_project_into_the_folders_project(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    gone = tmp_path / "gone"; gone.mkdir()
    here = tmp_path / "here"; here.mkdir()
    stale = projects.resolve_project(gone)
    owner = projects.resolve_project(here)
    shutil.rmtree(gone)
    ws = FakeWS()
    asyncio.run(ws_project.handle_relocate_project(ws, {
        "project_id": stale.id, "path": str(here), "replace_identity": True,
    }))
    data = next(f["data"] for f in ws.sent if f["type"] == "project_relocated")
    assert data["ok"] is True
    assert data["project_id"] == owner.id
    assert projects.get_project(stale.id) is None


def test_execute_refuses_bound_missing_project(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.session_settings.workdir import bound_project_execution_blocked

    store = _store(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", project_path=str(folder))
    shutil.rmtree(folder)
    assert bound_project_execution_blocked("s1") == "missing"


def test_list_projects_answers_from_the_registry_and_reconciles_after(tmp_path, monkeypatch):
    """A listing never searches for moved folders or waits on a migration
    before it answers: once it scanned the parents of every missing project
    on each call and held the connection for 20 seconds."""
    import threading
    import time
    from openprogram.interfaces.server import server
    from openprogram.state.store.project import discovery

    _store(tmp_path, monkeypatch)
    parent = tmp_path / "work"
    big = parent / "big"
    for i in range(300):
        (big / f"d{i}").mkdir(parents=True)
    projects.resolve_project(big)
    gone = parent / "gone"; gone.mkdir()
    moved = projects.resolve_project(gone)
    shutil.rmtree(gone)

    release = threading.Event()
    searched = []

    def slow_search(project, **kwargs):
        searched.append(project.id)
        release.wait(5)
        return None

    monkeypatch.setattr(discovery, "find_project_folder", slow_search)
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("-inf"))
    monkeypatch.setattr(ws_project, "_reconcile_queued_for", float("-inf"))
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: broadcasts.append(msg))

    async def exercise():
        ws = FakeWS()
        started = time.monotonic()
        await ws_project.handle_list_projects(ws, {"session_id": ""})
        answered = time.monotonic() - started
        # The reply went out while the background search is still held.
        assert not release.is_set()
        release.set()
        return ws.sent, answered

    sent, answered = asyncio.run(exercise())
    assert answered < 1.0
    # The reply carries the recorded location; nothing looked at the folder.
    row = next(p for p in sent[0]["data"]["projects"] if p["id"] == moved.id)
    assert row["location_state"] == "available"
    # The reconciliation still ran, after the reply: it found the folder
    # gone, recorded that and had every page list again.
    assert searched == [moved.id]
    assert projects.get_project(moved.id).location_state == "missing"
    assert [json.loads(b)["type"] for b in broadcasts] == ["projects_changed"]


def test_a_migration_settled_after_the_listing_tells_clients_to_list_again(tmp_path, monkeypatch):
    """The listing shows a deferred migration as migrating and retries it
    after the reply. When the retry settles, clients are told to list again;
    nothing else would refresh the project out of "migrating"."""
    from openprogram.interfaces.server import server
    from openprogram.state.store.project import location

    _store(tmp_path, monkeypatch)
    folder = tmp_path / "paper"; folder.mkdir()
    project = projects.resolve_project(folder)
    projects.set_location_state(project.id, "migrating")

    def settle():
        projects.set_location_state(project.id, "available")
        return []  # nothing moved

    monkeypatch.setattr(location, "reconcile_registered_projects", settle)
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: broadcasts.append(json.loads(msg)))
    # The listing must not start its own background run; this test runs it.
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("inf"))

    ws = FakeWS()
    asyncio.run(ws_project.handle_list_projects(ws, {"session_id": ""}))
    row = next(p for p in ws.sent[0]["data"]["projects"] if p["id"] == project.id)
    assert row["location_state"] == "migrating"

    monkeypatch.setattr(ws_project, "_reconcile_running", False)
    ws_project._reconcile_projects()
    assert [b["type"] for b in broadcasts] == ["projects_changed"]


def test_every_project_change_reaches_every_open_page(tmp_path, monkeypatch):
    """A project made, renamed, moved or hidden from one connection (another
    window, an agent) shows in pages already open: each change is broadcast
    as ``projects_changed``, which every page answers by listing again."""
    from openprogram.interfaces.server import server
    _store(tmp_path, monkeypatch)
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: broadcasts.append(json.loads(msg)))
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("inf"))
    folder = tmp_path / "paper"; folder.mkdir()
    moved = tmp_path / "paper-moved"; moved.mkdir()

    ws = FakeWS()
    asyncio.run(ws_project.handle_create_project(ws, {"path": str(folder)}))
    project_id = ws.sent[0]["data"]["project"]["id"]
    asyncio.run(ws_project.handle_update_project(ws, {"project_id": project_id, "patch": {"name": "Paper"}}))
    asyncio.run(ws_project.handle_relocate_project(ws, {"project_id": project_id, "path": str(moved)}))
    asyncio.run(ws_project.handle_project_operation(ws, {"action": "remove_project", "project_id": project_id}))
    assert broadcasts == [{"type": "projects_changed", "data": {"project_id": project_id}}] * 4


class GoneWS:
    """A sender that hung up right after asking: every send fails."""

    async def send_text(self, text):
        raise ConnectionError("socket closed")


def test_a_sender_that_hangs_up_still_tells_every_other_page(tmp_path, monkeypatch):
    """The CLI, a script or an agent may close its socket as soon as it has
    asked; the change is stored, so the other open pages still hear of it."""
    from openprogram.interfaces.server import server
    _store(tmp_path, monkeypatch)
    broadcasts = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: broadcasts.append(json.loads(msg)))
    monkeypatch.setattr(ws_project, "_reconcile_started_at", float("inf"))
    folder = tmp_path / "paper"; folder.mkdir()
    moved = tmp_path / "paper-moved"; moved.mkdir()

    def run(handler, cmd):
        try:
            asyncio.run(handler(GoneWS(), cmd))
        except ConnectionError:
            pass

    run(ws_project.handle_create_project, {"path": str(folder)})
    project_id = projects.resolve_project(str(folder)).id
    run(ws_project.handle_update_project, {"project_id": project_id, "patch": {"name": "Paper"}})
    run(ws_project.handle_relocate_project, {"project_id": project_id, "path": str(moved)})
    run(ws_project.handle_project_operation, {"action": "remove_project", "project_id": project_id})
    assert broadcasts == [{"type": "projects_changed", "data": {"project_id": project_id}}] * 4
