"""/api/file-{search,read,resolve} — ``?root=`` may not escape the project.

The containment check in these routes compares ``target`` against the
root the *caller* supplied, so honouring an arbitrary ``root`` made it
vacuous: ``?root=/etc&path=passwd`` read /etc/passwd. ``_resolve_root``
now rejects roots outside the allowed set.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram import attachments
from openprogram.store.session.git_session import GitSession
from openprogram.store.session.migration import migrate_session
from openprogram.store.session.session_store import SessionStore


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
    from openprogram.webui.routes import file_search
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
    GitSession(source)._ensure_init()
    (source / "meta.json").write_text('{"id":"s1"}', encoding="utf-8")
    marker = attachments.format_marker(
        "report.pdf", old_attachment, len(b"legacy"), mime="application/pdf",
    )
    (source / "history" / "0001-u-u1.json").write_text(
        json.dumps({"id": "u1", "role": "user", "content": marker}),
        encoding="utf-8",
    )
    old_attachment.write_bytes(b"legacy")

    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(project))
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.store.session.migration._live_jobs", lambda _sid: False,
    )
    store = SessionStore(state / "sessions")
    result = migrate_session(store, {
        "session_id": "s1",
        "project_id": "p1",
        "source": str(source),
        "source_unavailable": False,
    })
    assert result == "done"
    assert not source.exists()
    dest = state / "sessions" / "projects" / "p1" / "s1"
    current_attachment = dest / "workdir" / "attachments" / "report.pdf"
    assert current_attachment.read_bytes() == b"legacy"
    assert str(old_attachment) in (dest / "history" / "0001-u-u1.json").read_text()

    app = FastAPI()
    from openprogram.webui.routes import file_search
    file_search.register(app)
    client = TestClient(app)
    response = client.get("/api/file-raw", params={
        "path": str(old_attachment), "session_id": "s1",
    })
    assert response.status_code == 200
    assert response.content == b"legacy"

    wrong_session = client.get("/api/file-raw", params={
        "path": str(old_attachment), "session_id": "other",
    })
    assert wrong_session.status_code == 403

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
