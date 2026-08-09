"""/api/file-{search,read,resolve} — ``?root=`` may not escape the project.

The containment check in these routes compares ``target`` against the
root the *caller* supplied, so honouring an arbitrary ``root`` made it
vacuous: ``?root=/etc&path=passwd`` read /etc/passwd. ``_resolve_root``
now rejects roots outside the allowed set.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
