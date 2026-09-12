"""Authenticated document publication, durable versions, and recovery behavior."""
from __future__ import annotations

import hashlib
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openprogram.store.project import project_store
from openprogram.store.project.identity import capture_directory_identity


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def documents(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    project = types.SimpleNamespace(id="p1", path=str(root),
                                    location_state="available", is_default=False,
                                    **capture_directory_identity(root))
    monkeypatch.setattr(project_store, "get_project", lambda key: project if key == "p1" else None)
    from openprogram.webui.server import create_app
    app = create_app()
    client = TestClient(app, base_url="http://127.0.0.1:18100",
                        headers={"Authorization": f"Bearer {app.state.owner_auth.token}"},
                        raise_server_exceptions=False)
    yield client, root, state, project
    client.close()


def put(client, path, before, after, *, key=None, close=False):
    return client.put("/api/documents/content", params={"project_id": "p1", "path": path},
                      content=after, headers={"X-Baseline-Revision": digest(before),
                      "Idempotency-Key": key or str(uuid.uuid4()),
                      "X-History-Close": str(close).lower()})


def history(client, path):
    response = client.get("/api/documents/history", params={"project_id": "p1", "path": path})
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_publication_retry_retains_exact_bytes_and_single_version(documents):
    client, root, _, _ = documents
    old, new = b"old\x00binary", b"new\x00binary"
    (root / "a.docx").write_bytes(old)
    key = str(uuid.uuid4())
    first = put(client, "a.docx", old, new, key=key)
    assert first.status_code == 200, first.text
    assert (root / "a.docx").read_bytes() == new
    retry = put(client, "a.docx", old, new, key=key)
    assert retry.status_code == 200, retry.text
    assert len(history(client, "a.docx")) == 1
    entry = history(client, "a.docx")[0]
    for side, content in (("before", old), ("after", new)):
        response = client.get("/api/documents/history/content", params={
            "project_id": "p1", "path": "a.docx", "version": entry["version_id"], "side": side})
        assert response.status_code == 200
        assert response.content == content


def test_idempotency_collision_cannot_overwrite_file(documents):
    client, root, _, _ = documents
    target = root / "a.txt"
    target.write_bytes(b"old")
    key = str(uuid.uuid4())
    assert put(client, "a.txt", b"old", b"first", key=key).status_code == 200
    response = put(client, "a.txt", b"first", b"second", key=key)
    assert response.status_code == 409
    assert target.read_bytes() == b"first"


def test_closed_edit_group_does_not_absorb_next_publication(documents):
    client, root, _, _ = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"one", close=True).status_code == 200
    assert put(client, "a.txt", b"one", b"two").status_code == 200
    assert len(history(client, "a.txt")) == 2


def test_saved_history_is_readable_when_project_disk_is_missing(documents):
    client, root, _, project = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"new").status_code == 200
    version = history(client, "a.txt")[0]["version_id"]
    root.rename(root.with_name("detached"))
    project.location_state = "missing"
    assert len(history(client, "a.txt")) == 1
    response = client.get("/api/documents/history/content", params={
        "project_id": "p1", "path": "a.txt", "version": version, "side": "before"})
    assert response.status_code == 200
    assert response.content == b"old"
    assert put(client, "a.txt", b"new", b"later").status_code != 200


def test_unicode_content_and_missing_file_have_correct_http_status(documents):
    client, root, _, _ = documents
    (root / "中文📝.txt").write_bytes(b"actual")
    response = client.get("/api/documents/content", params={"project_id": "p1", "path": "中文📝.txt"})
    assert response.status_code == 200
    assert response.content == b"actual"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    missing = client.get("/api/documents/content", params={"project_id": "p1", "path": "missing.txt"})
    assert missing.status_code == 404


def test_untrusted_and_missing_revision_writes_do_not_mutate(documents):
    client, root, _, _ = documents
    (root / "a.txt").write_bytes(b"old")
    response = client.put("/api/documents/content", params={"project_id": "p1", "path": "a.txt"},
                          content=b"new", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 400
    denied = client.put("/api/documents/content", params={"project_id": "p1", "path": "a.txt"},
                        content=b"new", headers={"Authorization": "Bearer invalid"})
    assert denied.status_code in (401, 403)
    assert (root / "a.txt").read_bytes() == b"old"


def test_publication_preserves_private_target_mode(documents):
    import os
    if os.name == "nt":
        pytest.skip("POSIX permission mode")
    client, root, _, _ = documents
    target = root / "private.txt"
    target.write_bytes(b"old")
    target.chmod(0o600)
    response = put(client, "private.txt", b"old", b"new")
    assert response.status_code == 200, response.text
    assert target.stat().st_mode & 0o777 == 0o600


def test_restore_old_version_is_new_record_and_retry_survives_new_service(documents):
    client, root, state, _ = documents
    from openprogram.store.document_history import DocumentHistory
    target = root / "a.txt"
    target.write_bytes(b"zero")
    assert put(client, "a.txt", b"zero", b"one", close=True).status_code == 200
    first = history(client, "a.txt")[0]["version_id"]
    assert put(client, "a.txt", b"one", b"two").status_code == 200
    payload = {"project_id": "p1", "path": "a.txt", "version": first, "side": "before",
               "baseline_revision": digest(b"two"), "idempotency_key": str(uuid.uuid4())}
    response = client.post("/api/documents/history/restore", json=payload)
    assert response.status_code == 200, response.text
    assert target.read_bytes() == b"zero"
    assert len(history(client, "a.txt")) == 3
    retry = DocumentHistory(state / "project-file-history").restore(
        "p1", "a.txt", first, side="before", baseline_revision=payload["baseline_revision"],
        idempotency_key=payload["idempotency_key"])
    assert retry["ok"]
    assert len(history(client, "a.txt")) == 3
    latest = history(client, "a.txt")[0]["version_id"]
    before = client.get("/api/documents/history/content", params={
        "project_id": "p1", "path": "a.txt", "version": latest, "side": "before"})
    assert before.content == b"two"


def test_history_group_has_five_minute_upper_bound_and_external_edits_start_new_group(documents, monkeypatch):
    client, root, _, _ = documents
    import openprogram.store.document_history as module
    now = [10_000.0]
    monkeypatch.setattr(module, "time", types.SimpleNamespace(time=lambda: now[0]))
    target = root / "a.txt"
    target.write_bytes(b"zero")
    for timestamp, before, after in ((10000, b"zero", b"one"), (10290, b"one", b"two"), (10301, b"two", b"three")):
        now[0] = timestamp
        assert put(client, "a.txt", before, after).status_code == 200
    assert len(history(client, "a.txt")) == 2
    target.write_bytes(b"outside")
    now[0] = 10302
    assert put(client, "a.txt", b"outside", b"four").status_code == 200
    assert len(history(client, "a.txt")) == 3


def test_prepare_failure_does_not_modify_target(documents, monkeypatch):
    client, root, _, _ = documents
    from openprogram.store.snapshot.checkpoint.store import CheckpointStore
    target = root / "a.txt"
    target.write_bytes(b"old")
    def fail(*args, **kwargs):
        raise OSError("injected snapshot failure")
    monkeypatch.setattr(CheckpointStore, "_capture_regular", fail)
    response = put(client, "a.txt", b"old", b"new")
    assert response.status_code == 503
    assert target.read_bytes() == b"old"
    assert history(client, "a.txt")[0]["status"] == "recovery_required"


def test_commit_failure_retains_before_and_exposes_unknown_history(documents, monkeypatch):
    client, root, _, _ = documents
    from openprogram.store.snapshot.checkpoint import manifest
    original = manifest.save
    failed = [False]
    def fail_once(path, value):
        if value.get("status") == "committed" and not failed[0]:
            failed[0] = True
            raise OSError("injected receipt commit failure")
        return original(path, value)
    monkeypatch.setattr(manifest, "save", fail_once)
    (root / "a.txt").write_bytes(b"old")
    response = put(client, "a.txt", b"old", b"new")
    assert response.status_code == 503
    entry = history(client, "a.txt")[0]
    assert entry["status"] == "recovery_required"
    assert entry["after_revision"] is None
    before = client.get("/api/documents/history/content", params={
        "project_id": "p1", "path": "a.txt", "version": entry["version_id"], "side": "before"})
    assert before.content == b"old"


def test_concurrent_clients_cannot_both_overwrite_the_same_baseline(documents):
    from concurrent.futures import ThreadPoolExecutor
    client, root, _, _ = documents
    (root / "a.txt").write_bytes(b"old")
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda n: put(client, "a.txt", b"old", str(n).encode()), range(8)))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in (200, 409) for response in responses)
    assert len(history(client, "a.txt")) == 1


def test_restore_uses_relocated_project_and_does_not_touch_old_occupant(documents):
    client, root, _, project = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"new", close=True).status_code == 200
    version = history(client, "a.txt")[0]["version_id"]
    moved = root.with_name("relocated")
    root.rename(moved)
    root.mkdir()
    (root / "a.txt").write_bytes(b"unrelated")
    project.path = str(moved)
    response = client.post("/api/documents/history/restore", json={
        "project_id": "p1", "path": "a.txt", "version": version, "side": "before",
        "baseline_revision": digest(b"new"), "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    assert (moved / "a.txt").read_bytes() == b"old"
    assert (root / "a.txt").read_bytes() == b"unrelated"


def test_corrupt_version_cannot_be_restored(documents):
    client, root, state, _ = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"new", close=True).status_code == 200
    version = history(client, "a.txt")[0]["version_id"]
    # Damage the retained before bytes, as a disk corruption would do.
    blobs = [path for path in (state / "project-file-history").rglob("*")
             if path.is_file() and path.read_bytes() == b"old"]
    assert blobs
    for blob in blobs:
        blob.write_bytes(b"corrupted")
    response = client.post("/api/documents/history/restore", json={
        "project_id": "p1", "path": "a.txt", "version": version, "side": "before",
        "baseline_revision": digest(b"new"), "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 503
    assert (root / "a.txt").read_bytes() == b"new"


def test_history_pagination_keeps_all_closed_versions(documents):
    client, root, _, _ = documents
    previous = b"zero"
    (root / "a.txt").write_bytes(previous)
    for n in range(5):
        current = str(n).encode()
        assert put(client, "a.txt", previous, current, close=True).status_code == 200
        previous = current
    versions, cursor = [], 0
    while cursor is not None:
        response = client.get("/api/documents/history", params={
            "project_id": "p1", "path": "a.txt", "limit": 2, "cursor": cursor})
        assert response.status_code == 200
        versions.extend(entry["version_id"] for entry in response.json()["entries"])
        cursor = response.json()["next_cursor"]
    assert len(versions) == len(set(versions)) == 5


def test_outside_path_unknown_project_and_oversize_body_are_rejected(documents):
    client, root, _, _ = documents
    outside = root.parent / "outside.txt"
    outside.write_bytes(b"private")
    headers = {"X-Baseline-Revision": digest(b"private"), "Idempotency-Key": str(uuid.uuid4())}
    for path in ("../outside.txt", str(outside)):
        response = client.put("/api/documents/content", params={"project_id": "p1", "path": path},
                              content=b"overwrite", headers=headers)
        assert response.status_code == 400
    assert outside.read_bytes() == b"private"
    unknown = client.put("/api/documents/content", params={"project_id": "unknown", "path": "a.txt"},
                         content=b"new", headers=headers)
    assert unknown.status_code == 404
    oversized = client.put("/api/documents/content", params={"project_id": "p1", "path": "a.txt"},
                           content=b"new", headers={**headers, "Content-Length": str(64 * 1024 * 1024 + 1)})
    assert oversized.status_code == 413
    assert not (root / "a.txt").exists()


def test_failed_autosave_keeps_previous_confirmed_version_visible(documents, monkeypatch):
    client, root, _, _ = documents
    from openprogram.store.snapshot.checkpoint.store import CheckpointStore
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"confirmed").status_code == 200
    confirmed = history(client, "a.txt")[0]["version_id"]
    def fail(*args, **kwargs):
        raise OSError("snapshot unavailable")
    monkeypatch.setattr(CheckpointStore, "_capture_regular", fail)
    assert put(client, "a.txt", b"confirmed", b"unconfirmed").status_code == 503
    entries = history(client, "a.txt")
    assert len(entries) == 2
    assert entries[0]["status"] == "recovery_required"
    assert entries[1]["version_id"] == confirmed
    assert entries[1]["status"] == "committed"
    response = client.get("/api/documents/history/content", params={
        "project_id": "p1", "path": "a.txt", "version": confirmed, "side": "after"})
    assert response.status_code == 200
    assert response.content == b"confirmed"


def test_malformed_receipt_remains_visible_as_unconfirmed_history(documents):
    import json
    client, root, state, _ = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"new", close=True).status_code == 200
    intents = list((state / "project-file-history").rglob("intent.json"))
    assert len(intents) == 1
    value = json.loads(intents[0].read_text())
    value["before"] = "corrupt descriptor"
    intents[0].write_text(json.dumps(value))
    response = client.get("/api/documents/history", params={"project_id": "p1", "path": "a.txt"})
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["status"] == "recovery_required"
    assert response.json()["entries"][0]["before_revision"] is None


def test_empty_history_blob_reference_reports_corruption(documents):
    import json
    client, root, state, _ = documents
    (root / "a.txt").write_bytes(b"old")
    assert put(client, "a.txt", b"old", b"new", close=True).status_code == 200
    version = history(client, "a.txt")[0]["version_id"]
    intent = next((state / "project-file-history").rglob("intent.json"))
    value = json.loads(intent.read_text())
    for side in ("before", "after"):
        value[side]["blob_ref"] = ""
    intent.write_text(json.dumps(value))
    for side in ("before", "after"):
        response = client.get("/api/documents/history/content", params={
            "project_id": "p1", "path": "a.txt", "version": version, "side": side})
        assert response.status_code == 503, response.text
        assert response.json()["error"] in ("HISTORY_CORRUPT", "RECOVERY_REQUIRED")
    assert history(client, "a.txt")[0]["status"] == "recovery_required"


def test_authenticated_model_history_content_and_opaque_cursor(documents, monkeypatch):
    from openprogram.store.document_history import DocumentHistory
    from openprogram.store.session.session_store import SessionStore
    from openprogram.store.snapshot.checkpoint import CheckpointStore
    client, root, state, project = documents
    sessions = SessionStore(root_path=state / "sessions")
    try:
        sessions.create_session("model-http", agent_id="main")
        monkeypatch.setattr("openprogram.store.default_store", lambda: sessions)
        target = root / "model.docx"
        target.write_bytes(b"before\0")
        journal = CheckpointStore(sessions._session_dir("model-http"))
        journal.backup_before_edit("turn", str(target), project_locator={
            "project_id":"p1", "path":"model.docx", "recorded_root":str(root),
            "directory_identity":project.directory_identity, "location_revision":0})
        target.write_bytes(b"model\xff")
        journal.commit_after_edit("turn",str(target))
        DocumentHistory().register_model_turn("model-http","turn",session_store=sessions)
        assert put(client,"model.docx",b"model\xff",b"manual",close=True).status_code==200
        page=client.get("/api/documents/history",params={"project_id":"p1","path":"model.docx","limit":1}).json()
        more=client.get("/api/documents/history",params={"project_id":"p1","path":"model.docx","limit":1,"cursor":page["next_cursor"]})
        assert more.status_code==200,more.text
        entry=more.json()["entries"][0]
        assert entry["actor"]=="model"
        params={"project_id":"p1","path":"model.docx","version":entry["version_id"]}
        assert client.get("/api/documents/history/content",params=params).content==b"model\xff"
        assert client.get("/api/documents/history/content",params=params,headers={"Authorization":"Bearer bad"}).status_code in (401,403)
    finally:
        sessions.close()
