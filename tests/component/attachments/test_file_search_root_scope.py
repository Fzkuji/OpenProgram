"""/api/file-{search,read,resolve} — ``?root=`` may not escape the project.

The containment check in these routes compares ``target`` against the
root the *caller* supplied, so honouring an arbitrary ``root`` made it
vacuous: ``?root=/etc&path=passwd`` read /etc/passwd. ``_resolve_root``
now rejects roots outside the allowed set.
"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.state import attachments
from openprogram.state.store.session.files.session_log import SessionLog
from openprogram.state.store.session.files.migration import migrate_session
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fake project root with one file, made the default root."""
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "hello.txt").write_text("hi there", encoding="utf-8")
    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(root))
    return root


@pytest.fixture
def client(project):
    from openprogram.interfaces.server.routes.files import file_search
    app = FastAPI()
    file_search.register(app)
    return TestClient(app)


def test_read_without_root_uses_project_root(client):
    r = client.get("/api/file-read", params={"path": "sub/hello.txt"})
    assert r.status_code == 200
    assert r.json()["content"] == "hi there"


def test_read_with_explicit_root_inside_project_works(client, project):
    """The composer legitimately passes a workdir under the root."""
    r = client.get("/api/file-read", params={
        "path": "hello.txt", "root": str(project / "sub"),
    })
    assert r.status_code == 200
    assert r.json()["content"] == "hi there"


def test_read_rejects_arbitrary_root(client):
    r = client.get("/api/file-read", params={"root": "/etc", "path": "passwd"})
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_resolve_rejects_arbitrary_root(client):
    r = client.get("/api/file-resolve", params={"root": "/etc", "path": "passwd"})
    assert r.status_code == 400


def test_search_rejects_arbitrary_root(client):
    r = client.get("/api/file-search", params={"root": "/etc", "q": "passwd"})
    assert r.status_code == 400


def test_read_still_rejects_dotdot_escape(client):
    r = client.get("/api/file-read", params={"path": "../../../../etc/passwd"})
    assert r.status_code == 400
    assert "escapes root" in r.json()["detail"]


# --- /api/file-raw + absolute-path /api/file-read -------------------------
#
# The chat's attachment viewer asks for bytes by ABSOLUTE path, because
# attachments live in the session workdir or a channel's inbound
# directory, not under a project id. The containment check moves to
# ``attachments.readable_roots()``; everything else about the response
# mirrors ``/files/raw``.

def test_raw_serves_a_file_inside_an_allowed_root(client, project):
    img = project / "sub" / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nBODY")
    r = client.get("/api/file-raw", params={"path": str(img)})
    assert r.status_code == 200
    assert r.content == b"\x89PNG\r\n\x1a\nBODY"
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_raw_refuses_a_path_outside_every_root(client, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    assert client.get("/api/file-raw",
                      params={"path": str(outside)}).status_code == 403


def test_file_raw_streams_video_with_its_type_and_ranges(client, project):
    # A reply plays sent video in place: <video> needs the real type, and
    # seeking needs Range requests.
    clip = project / "sub" / "cut.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"0123456789")
    r = client.get("/api/file-raw", params={"path": str(clip)})
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.headers["x-content-type-options"] == "nosniff"
    part = client.get("/api/file-raw", params={"path": str(clip)},
                      headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"


def test_attachment_routes_keep_allowed_missing_file_404(client, project):
    missing = project / "sub" / "missing.png"
    assert client.get("/api/file-raw", params={"path": str(missing)}).status_code == 404
    assert client.get("/api/file-read", params={"path": str(missing)}).status_code == 404


def test_existing_legacy_path_falls_back_without_canonical(project, monkeypatch):
    legacy = project / ".openprogram" / "sessions" / "s1" / "workdir" / "attachments" / "old.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("source", encoding="utf-8")
    monkeypatch.setattr(attachments, "_session_repo_candidates", lambda _sid: [])
    assert attachments.resolve_session_attachment(
        legacy, "s1", [project], allow_missing=False,
    ) == legacy.resolve()


def test_raw_refuses_a_symlink_pointing_out_of_the_root(client, project, tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    link = project / "sub" / "innocent.png"
    link.symlink_to(secret)
    assert client.get("/api/file-raw",
                      params={"path": str(link)}).status_code == 403


def test_read_accepts_an_absolute_path_inside_a_root(client, project):
    f = project / "sub" / "hello.txt"
    r = client.get("/api/file-read", params={"path": str(f)})
    assert r.status_code == 200
    assert r.json()["content"] == "hi there"


def test_read_refuses_an_absolute_path_outside_every_root(client, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    assert client.get("/api/file-read",
                      params={"path": str(outside)}).status_code == 403


def test_read_reports_binary_so_the_viewer_shows_a_download_card(client, project):
    blob = project / "sub" / "x.bin"
    blob.write_bytes(b"\x00\x01\x02binary")
    assert client.get("/api/file-read",
                      params={"path": str(blob)}).json()["binary"] is True


def test_raw_rebases_legacy_session_attachment_after_real_migration(
    tmp_path, monkeypatch,
):
    """Historical markers remain immutable while the raw boundary follows
    the copied session attachment into its canonical repo.

    This uses the production migration entry point. The source is removed by
    migration, so a route that only checks its old absolute path returns 404.
    """
    project = tmp_path / "project"
    state = tmp_path / "state"
    source = project / ".openprogram" / "sessions" / "s1"
    old_attachment = source / "workdir" / "attachments" / "report.pdf"
    old_attachment.parent.mkdir(parents=True)
    log = SessionLog(source)
    log.write_meta({"id": "s1"})
    marker = attachments.format_marker(
        "report.pdf", old_attachment, len(b"legacy"), mime="application/pdf",
    )
    log.write_history(1, "user", "u1", {"id": "u1", "role": "user", "content": marker})
    old_attachment.write_bytes(b"legacy")

    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(project))
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.state.store.session.files.migration._live_jobs", lambda _sid: False,
    )
    store = SessionStore(state / "projects")
    result = migrate_session(store, {
        "session_id": "s1",
        "project_id": "p1",
        "source": str(source),
        "source_unavailable": False,
    })
    assert result == "done"
    assert not source.exists()
    dest = state / "projects" / "p1" / "chats" / "s1"
    current_attachment = dest / "workdir" / "attachments" / "report.pdf"
    assert current_attachment.read_bytes() == b"legacy"
    assert str(old_attachment) in (dest / "main.jsonl").read_text()

    # A stale source copy may still exist after migration. The public route
    # must prefer the canonical session attachment rather than serving it.
    old_attachment.parent.mkdir(parents=True, exist_ok=True)
    old_attachment.write_bytes(b"stale-source")

    app = FastAPI()
    from openprogram.interfaces.server.routes.files import file_search
    file_search.register(app)
    client = TestClient(app)
    response = client.get("/api/file-raw", params={
        "path": str(old_attachment), "session_id": "s1",
    })
    assert response.status_code == 200
    assert response.content == b"legacy"

    import asyncio
    from openprogram.runtime.execution.run_control import (
        reset_current_session_id,
        set_current_session_id,
    )
    from openprogram.programs.tools.files.read import read
    from openprogram.host import sandbox
    token = set_current_session_id("s1")
    try:
        read_result = asyncio.run(read.execute(
            "migration-read", {"file_path": str(old_attachment)}, None, None,
        ))
        read_text = "\n".join(
            block.text for block in (read_result.content or [])
            if getattr(block, "text", None) is not None
        )
        assert "legacy" in read_text
        monkeypatch.setattr(sandbox, "validate_read_path", lambda _path: "blocked")
        denied_result = asyncio.run(read.execute(
            "migration-read-denied", {"file_path": str(old_attachment)}, None, None,
        ))
        denied_text = "\n".join(
            block.text for block in (denied_result.content or [])
            if getattr(block, "text", None) is not None
        )
        assert denied_text.startswith("[blocked by sandbox]") and "blocked" in denied_text
    finally:
        reset_current_session_id(token)

    wrong_session = client.get("/api/file-raw", params={
        "path": str(old_attachment), "session_id": "other",
    })
    assert wrong_session.status_code == 403
    wrong_session_traversal = client.get("/api/file-raw", params={
        "path": str(old_attachment.parent / ".." / "attachments" / "report.pdf"),
        "session_id": "other",
    })
    assert wrong_session_traversal.status_code == 403

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private")
    link = current_attachment.parent / "link.pdf"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    escaped = client.get("/api/file-raw", params={
        "path": str(source / "workdir" / "attachments" / "link.pdf"),
        "session_id": "s1",
    })
    assert escaped.status_code == 403

    root_outside = tmp_path / "outside-attachments"
    root_outside.mkdir()
    (root_outside / "report.pdf").write_bytes(b"private-root")
    shutil.rmtree(current_attachment.parent)
    try:
        current_attachment.parent.symlink_to(root_outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    escaped_root = client.get("/api/file-raw", params={
        "path": str(old_attachment), "session_id": "s1",
    })
    assert escaped_root.status_code == 403


# --- @ mentions resolve in the chat's working directory --------------------

def test_search_and_resolve_use_the_chats_working_directory(client, tmp_path, monkeypatch):
    """A chat's ``@`` mentions name files where its agent works, not files
    of the OpenProgram checkout the worker runs from."""
    chat_dir = tmp_path / "work"
    (chat_dir / "src").mkdir(parents=True)
    (chat_dir / "src" / "main.py").write_text("print(1)", encoding="utf-8")
    from openprogram.runtime.conversation.session_settings import workdir
    monkeypatch.setattr(
        workdir, "runtime_location_for",
        lambda sid, use_context=True: {"workdir": str(chat_dir) if sid == "s1" else None},
    )
    monkeypatch.setattr(workdir, "session_workdir_for", lambda _sid: None)

    found = client.get("/api/file-search", params={"q": "main", "session_id": "s1"}).json()
    assert found["root"] == str(chat_dir.resolve())
    assert [m["path"] for m in found["matches"]] == ["src/main.py"]

    resolved = client.get("/api/file-resolve", params={"path": "src/main.py", "session_id": "s1"})
    assert resolved.status_code == 200
    assert resolved.json()["path"] == str((chat_dir / "src" / "main.py").resolve())
    escaped = client.get("/api/file-resolve", params={"path": "../outside.txt", "session_id": "s1"})
    assert escaped.status_code == 400


def test_a_chat_not_created_yet_searches_its_picked_project(client, tmp_path, monkeypatch):
    picked = tmp_path / "picked"
    picked.mkdir()
    (picked / "notes.md").write_text("n", encoding="utf-8")
    from types import SimpleNamespace
    from openprogram.state.store.project import project_store
    monkeypatch.setattr(
        project_store, "get_project",
        lambda pid: SimpleNamespace(path=str(picked)) if pid == "p1" else None,
    )
    found = client.get("/api/file-search", params={
        "q": "notes", "session_id": "local_draft", "project_id": "p1",
    }).json()
    assert found["root"] == str(picked.resolve())
    assert [m["path"] for m in found["matches"]] == ["notes.md"]


def test_no_chat_keeps_the_default_root(client, project):
    found = client.get("/api/file-search", params={"q": "hello"}).json()
    assert found["root"] == str(project.resolve())


# --- /api/file-paths: which paths in a reply name an openable file ---------

def test_file_paths_answers_each_path_in_one_request(client, project, tmp_path, monkeypatch):
    """A reply's paths are checked in one request: absolute and
    chat-relative paths come back as absolute files; a folder, a missing
    file and a file outside every root all come back null."""
    deck = project / "sub" / "plan.pptx"
    deck.write_bytes(b"PK")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    from openprogram.runtime.conversation.session_settings import workdir
    monkeypatch.setattr(
        workdir, "runtime_location_for",
        lambda sid, use_context=True: {"workdir": str(project) if sid == "s1" else None},
    )
    monkeypatch.setattr(workdir, "session_workdir_for", lambda _sid: None)
    asked = [str(deck), "sub/hello.txt", "sub", "sub/missing.pdf", str(outside), "../secret.txt"]
    r = client.get("/api/file-paths", params={"path": asked, "session_id": "s1"})
    assert r.status_code == 200
    assert r.json()["files"] == {
        str(deck): str(deck.resolve()),
        "sub/hello.txt": str((project / "sub" / "hello.txt").resolve()),
        "sub": None,
        "sub/missing.pdf": None,
        str(outside): None,
        "../secret.txt": None,
    }


def test_file_paths_without_a_chat_checks_absolute_paths_only(client, project):
    r = client.get("/api/file-paths", params={"path": [str(project / "sub" / "hello.txt"), "sub/hello.txt"]})
    assert r.json()["files"] == {
        str(project / "sub" / "hello.txt"): str((project / "sub" / "hello.txt").resolve()),
        "sub/hello.txt": None,
    }


def test_read_rejects_paths_with_a_nul_byte(client):
    """A NUL byte made resolve() raise ValueError — a 500, not a 4xx."""
    r = client.get("/api/file-read", params={"path": "sub\x00hello.txt"})
    assert r.status_code == 400
    assert r.json()["detail"] == "path escapes root"


def test_resolve_rejects_paths_with_a_nul_byte(client):
    r = client.get("/api/file-resolve", params={"path": "sub\x00hello.txt"})
    assert r.status_code == 400
    assert r.json()["detail"] == "path escapes root"
