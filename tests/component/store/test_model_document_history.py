"""Project History combines authoritative model receipts and manual versions."""
import hashlib
import os
from types import SimpleNamespace

import pytest

from openprogram.store.document_history import DocumentHistory, DocumentHistoryError
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore, manifest
from openprogram.store.snapshot.checkpoint.paths import turn_manifest_path, turn_backup_dir
from openprogram.store.project.identity import capture_directory_identity


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def model_history(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    project = SimpleNamespace(id="p1", path=str(root), is_default=False,
                              session_ids=["s1"],
                              location_state="available", location_revision=0,
                              **capture_directory_identity(root))
    monkeypatch.setattr("openprogram.store.project.project_store.get_project",
                        lambda key: project if key == "p1" else None)
    sessions = SessionStore(root_path=state / "sessions")
    sessions.create_session("s1", agent_id="main", title="model")
    sessions.update_session("s1", project_id="p1", project_path=str(root))
    monkeypatch.setattr("openprogram.store.default_store", lambda: sessions)
    history = DocumentHistory(root=state / "history")
    yield history, sessions, root, project
    sessions.close()


def model_write(fixture, before=b"before\0", after=b"model\xff", turn="turn1", register=True):
    history, sessions, root, project = fixture
    target = root / "a.docx"
    target.write_bytes(before)
    journal = CheckpointStore(sessions._session_dir("s1"))
    journal.backup_before_edit(turn, str(target))
    path = turn_manifest_path(journal.session_dir, turn)
    value = manifest.load(path)
    entry = next(iter(value["files"].values()))
    entry["project_locator"] = {"project_id": "p1", "path": "a.docx",
        "recorded_root": str(root), "directory_identity": project.directory_identity,
        "location_revision": 0}
    manifest.save(path, value)
    if register:
        history.register_model_turn("s1", turn, session_store=sessions)
    target.write_bytes(after)
    journal.commit_after_edit(turn, str(target))
    if register:
        history.register_model_turn("s1", turn, session_store=sessions)
    return target, journal


def test_model_and_manual_versions_reopen_and_restore_only_selected_file(model_history, monkeypatch):
    history, sessions, root, _ = model_history
    target, _ = model_write(model_history)
    rows = history.list("p1", "a.docx")["entries"]
    assert rows, "Existing model file history must appear in project History"
    model = rows[0]
    assert model["actor"] == "model"
    history.publish("p1", "a.docx", b"manual", baseline_revision=digest(target.read_bytes()),
                    idempotency_key="manual", close=True)
    reopened = DocumentHistory(root=history.root)
    entries = reopened.list("p1", "a.docx")["entries"]
    assert [row["actor"] for row in entries] == ["user", "model"]
    assert reopened.content("p1", "a.docx", model["version_id"], "before") == b"before\0"
    assert reopened.content("p1", "a.docx", model["version_id"], "after") == b"model\xff"
    other = root / "other.txt"
    other.write_bytes(b"unchanged")
    def forbidden(*args, **kwargs):
        raise AssertionError("whole turn restore is forbidden")
    monkeypatch.setattr(CheckpointStore, "apply_history_operation", forbidden)
    result = reopened.restore("p1", "a.docx", model["version_id"], side="after",
                              baseline_revision=digest(b"manual"), idempotency_key="restore")
    assert result["ok"] and target.read_bytes() == b"model\xff"
    assert other.read_bytes() == b"unchanged"
    assert reopened.restore("p1", "a.docx", model["version_id"], side="after",
        baseline_revision=digest(b"manual"), idempotency_key="restore") == result


def test_archive_keeps_model_history_but_hard_delete_rejects_cached_rows(model_history):
    history, sessions, _, _ = model_history
    model_write(model_history)
    version = history.list("p1", "a.docx")["entries"][0]["version_id"]
    sessions.set_archived("s1", True)
    assert history.content("p1", "a.docx", version) == b"model\xff"
    sessions.delete_session("s1")
    assert history.list("p1", "a.docx")["entries"] == []
    with pytest.raises(DocumentHistoryError):
        history.content("p1", "a.docx", version)


def test_model_content_survives_project_move_and_offline_but_restore_checks_location(model_history):
    history, _, root, project = model_history
    model_write(model_history)
    version = history.list("p1", "a.docx")["entries"][0]["version_id"]
    moved = root.with_name("moved")
    root.rename(moved)
    root.mkdir()
    (root / "a.docx").write_bytes(b"unrelated")
    project.path = str(moved)
    project.location_revision = 1
    assert history.content("p1", "a.docx", version) == b"model\xff"
    assert history.restore("p1", "a.docx", version, side="before",
        baseline_revision=digest(b"model\xff"), idempotency_key="moved")["ok"]
    assert (root / "a.docx").read_bytes() == b"unrelated"
    moved.rename(moved.with_name("offline"))
    project.location_state = "missing"
    assert history.content("p1", "a.docx", version) == b"model\xff"
    with pytest.raises(DocumentHistoryError):
        history.restore("p1", "a.docx", version, side="after",
            baseline_revision=digest(b"before\0"), idempotency_key="offline")


def test_model_corrupt_blob_is_not_replaced_by_current_file(model_history):
    history, _, _, _ = model_history
    _, journal = model_write(model_history)
    version = history.list("p1", "a.docx")["entries"][0]["version_id"]
    entry = journal.list_file_history("turn1")[0]
    (turn_backup_dir(journal.session_dir, "turn1") / entry["after"]["blob_ref"]).write_bytes(b"corrupt")
    with pytest.raises(DocumentHistoryError, match="corrupt"):
        history.content("p1", "a.docx", version)


def test_merged_cursor_does_not_duplicate_rows_after_new_publication(model_history):
    history, _, _, _ = model_history
    target, _ = model_write(model_history)
    history.publish("p1", "a.docx", b"manual", baseline_revision=digest(target.read_bytes()),
                    idempotency_key="one", close=True)
    first = history.list("p1", "a.docx", limit=1)
    history.publish("p1", "a.docx", b"newer", baseline_revision=digest(b"manual"),
                    idempotency_key="two", close=True)
    second = history.list("p1", "a.docx", limit=1, cursor=first["next_cursor"])
    assert first["entries"][0]["actor"] == "user"
    assert second["entries"][0]["actor"] == "model"
    assert first["entries"][0]["version_id"] != second["entries"][0]["version_id"]


def test_deleted_model_rows_do_not_hide_older_manual_page(model_history):
    history,sessions,_,_=model_history
    target,_=model_write(model_history)
    history.publish("p1","a.docx",b"manual",baseline_revision=digest(target.read_bytes()),idempotency_key="manual",close=True)
    model_write(model_history,before=b"manual",after=b"last",turn="turn2")
    sessions.delete_session("s1")
    page=history.list("p1","a.docx",limit=1)
    assert len(page["entries"])==1
    assert page["entries"][0]["actor"]=="user"


def test_backfill_is_bounded_and_does_not_rescan_sessions_after_snapshot(model_history,monkeypatch):
    history,sessions,_,_=model_history
    for i in range(25):
        model_write(model_history,turn=f"turn{i:02d}",register=False)
    assert history.backfill_project("p1",limit=3)["state"]=="partial"
    def no_scan(**kwargs):
        raise AssertionError("registry must not be rescanned after the saved snapshot")
    monkeypatch.setattr(sessions,"list_sessions",no_scan)
    page=history.list("p1","a.docx",limit=100)
    assert page["model_index"]["state"]=="partial"
    assert len(page["entries"])==23
    page=history.list("p1","a.docx",limit=100)
    assert page["model_index"]["state"]=="complete"
    assert len(page["entries"])==25


def test_legacy_exact_root_maps_without_modifying_manifest(model_history):
    history,_,_,_=model_history
    _,journal=model_write(model_history,register=False)
    path=turn_manifest_path(journal.session_dir,"turn1")
    value=manifest.load(path)
    next(iter(value["files"].values())).pop("project_locator")
    manifest.save(path,value)
    original=path.read_bytes()
    page=history.list("p1","a.docx")
    assert page["model_index"]["state"]=="complete"
    assert len(page["entries"])==1
    assert history.content("p1","a.docx",page["entries"][0]["version_id"])==b"model\xff"
    assert path.read_bytes()==original


def test_pending_receipt_overrides_stale_committed_projection(model_history):
    history,_,_,_=model_history
    target,journal=model_write(model_history)
    assert history.list("p1","a.docx")["entries"][0]["status"]=="committed"
    journal.backup_before_edit("turn1",str(target))
    entry=history.list("p1","a.docx")["entries"][0]
    assert entry["status"]=="recovery_required"
    assert entry["after_revision"] is None


def test_projection_failure_does_not_remove_existing_turn_summary(model_history,monkeypatch):
    from openprogram.agent.dispatcher.finalize import persist_turn_file_summary
    model_write(model_history)
    def unavailable(*args,**kwargs):
        raise OSError("history index unavailable")
    monkeypatch.setattr(DocumentHistory,"register_model_turn",unavailable)
    summary=persist_turn_file_summary("s1","turn1")
    assert summary is not None and summary["file_count"]==1


def test_model_restore_rejects_stale_baseline_without_touching_current_file(model_history):
    history,_,_,_=model_history
    target,_=model_write(model_history)
    version=history.list("p1","a.docx")["entries"][0]["version_id"]
    target.write_bytes(b"external")
    with pytest.raises(DocumentHistoryError) as error:
        history.restore("p1","a.docx",version,side="before",baseline_revision=digest(b"model\xff"),idempotency_key="stale")
    assert error.value.code=="CONFLICT"
    assert target.read_bytes()==b"external"


def test_corrupt_backfill_state_reports_history_error(model_history):
    history,_,_,_=model_history
    model_write(model_history)
    history.backfill_project("p1",limit=1)
    with history._database() as db:
        db.execute("UPDATE model_backfills SET queue_json='broken',status='partial'")
        db.commit()
    with pytest.raises(DocumentHistoryError) as error:
        history.list("p1","a.docx")
    assert error.value.code=="HISTORY_CORRUPT"


def test_future_history_schema_is_not_silently_written(model_history):
    history,_,_,_=model_history
    with history._database() as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(DocumentHistoryError) as error:
        history.list("p1","a.docx")
    assert error.value.code=="HISTORY_CORRUPT"


@pytest.mark.skipif(os.name=='nt',reason='POSIX directory modes')
def test_first_read_creates_private_history_directory(model_history):
    history,_,_,_=model_history
    previous=os.umask(0o022)
    try:
        history.list("p1","a.docx")
    finally:
        os.umask(previous)
    assert history.root.stat().st_mode & 0o777 == 0o700


def test_missing_registered_session_marks_backfill_unavailable(model_history):
    history,_,_,project=model_history
    project.session_ids=["unavailable-session"]
    page=history.list("p1","a.docx")
    assert page["entries"]==[]
    assert page["model_index"]["state"]=="unavailable"
