"""/api/archive-list, /api/archive-member and /api/file-reveal.

A zip a reply sends shows its contents in the chat card and opens as a
tree whose members preview in place; the card's "Reveal in Finder"
selects the file on the worker machine. All three use the attachment
roots, like /api/file-raw.
"""
from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server.routes.files import archives


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(root))
    return root


@pytest.fixture
def client(project):
    from openprogram.interfaces.server.routes.files import file_search
    app = FastAPI()
    file_search.register(app)
    archives.register(app)
    return TestClient(app)


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_zip_listing_adds_implied_folders_and_counts_files(client, project):
    pack = _zip(project / "pack.zip", {"papers/a.pdf": b"%PDF", "papers/deep/b.md": b"# b", "readme.md": b"hi"})
    body = client.get("/api/archive-list", params={"path": str(pack)}).json()
    assert body["format"] == "zip" and body["total"] == 3 and not body["truncated"]
    rows = {entry["path"]: entry for entry in body["entries"]}
    assert rows["papers"]["dir"] and rows["papers/deep"]["dir"]
    assert rows["readme.md"] == {"path": "readme.md", "size": 2, "dir": False}


def test_tar_gz_listing_and_member(client, project):
    pack = project / "pack.tar.gz"
    with tarfile.open(pack, "w:gz") as archive:
        data = b"hello"
        info = tarfile.TarInfo("docs/note.txt"); info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    body = client.get("/api/archive-list", params={"path": str(pack)}).json()
    assert body["format"] == "tar" and [e["path"] for e in body["entries"]] == ["docs", "docs/note.txt"]
    member = client.get("/api/archive-member", params={"path": str(pack), "member": "docs/note.txt"})
    assert member.status_code == 200 and member.content == b"hello"


def test_member_bytes_carry_untrusted_headers(client, project):
    pack = _zip(project / "pack.zip", {"page.html": b"<script>x</script>", "pic.png": b"\x89PNG"})
    page = client.get("/api/archive-member", params={"path": str(pack), "member": "page.html"})
    assert page.headers["content-type"] == "application/octet-stream"
    assert page.headers["content-security-policy"] == "sandbox"
    assert client.get("/api/archive-member", params={"path": str(pack), "member": "pic.png"}).headers["content-type"] == "image/png"


def test_member_names_are_looked_up_never_joined(client, project, tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    pack = _zip(project / "pack.zip", {"a.txt": b"a"})
    assert client.get("/api/archive-member", params={"path": str(pack), "member": "../../secret.txt"}).status_code == 404


def test_oversized_member_is_refused(client, project, monkeypatch):
    monkeypatch.setattr(archives, "MEMBER_MAX_BYTES", 4)
    pack = _zip(project / "pack.zip", {"big.txt": b"0123456789"})
    assert client.get("/api/archive-member", params={"path": str(pack), "member": "big.txt"}).status_code == 413


def test_listing_is_capped(client, project, monkeypatch):
    monkeypatch.setattr(archives, "LIST_MAX_ENTRIES", 2)
    pack = _zip(project / "pack.zip", {f"f{i}.txt": b"x" for i in range(5)})
    body = client.get("/api/archive-list", params={"path": str(pack)}).json()
    assert len(body["entries"]) == 2 and body["truncated"] and body["total"] == 5


def test_archive_routes_refuse_paths_outside_the_roots(client, tmp_path):
    outside = _zip(tmp_path / "outside.zip", {"a.txt": b"a"})
    assert client.get("/api/archive-list", params={"path": str(outside)}).status_code == 403


def test_non_archive_and_broken_archive(client, project):
    (project / "notes.txt").write_text("x")
    (project / "broken.zip").write_bytes(b"not a zip")
    assert client.get("/api/archive-list", params={"path": str(project / "notes.txt")}).status_code == 415
    assert client.get("/api/archive-list", params={"path": str(project / "broken.zip")}).status_code == 422


def test_reveal_selects_an_allowed_file_only(client, project, tmp_path, monkeypatch):
    from openprogram.interfaces.server.ws_actions.files import mutations
    shown = []
    monkeypatch.setattr(mutations, "reveal_in_file_manager", lambda target: shown.append(target) or {"ok": True})
    inside = project / "report.pdf"; inside.write_bytes(b"%PDF")
    outside = tmp_path / "secret.txt"; outside.write_text("nope")
    assert client.post("/api/file-reveal", params={"path": str(inside)}).status_code == 200
    assert client.post("/api/file-reveal", params={"path": str(outside)}).status_code == 403
    assert shown == [str(inside.resolve())]


def test_file_page_serves_html_in_a_script_sandbox_only(client, project):
    page = project / "page.html"; page.write_text("<script>1</script>")
    r = client.get("/api/file-page", params={"path": str(page)})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    (project / "notes.txt").write_text("x")
    assert client.get("/api/file-page", params={"path": str(project / "notes.txt")}).status_code == 415
