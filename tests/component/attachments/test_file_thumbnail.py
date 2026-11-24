"""/api/file-thumbnail: an Office file's first page for the chat file card.

Quick Look is the first source on macOS; these tests turn it off to
check the rest of the contract on every platform: the picture saved in
the file, the root check, the cache, and a plain 404 when there is none.
"""
from __future__ import annotations

import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server.routes.files import file_thumbnail

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(root))
    monkeypatch.setattr(file_thumbnail, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(file_thumbnail, "_quick_look", lambda target: None)
    return root


@pytest.fixture
def client(project):
    app = FastAPI()
    file_thumbnail.register(app)
    return TestClient(app)


def _office(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_saved_thumbnail_is_served_and_cached(client, project, tmp_path):
    deck = _office(project / "deck.pptx", {"docProps/thumbnail.png": PNG, "ppt/presentation.xml": b"<p/>"})
    response = client.get("/api/file-thumbnail", params={"path": str(deck)})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.content == PNG
    assert [p.suffix for p in (tmp_path / "cache").iterdir()] == [".png"]


def test_opendocument_thumbnail(client, project):
    sheet = _office(project / "sheet.ods", {"Thumbnails/thumbnail.png": PNG})
    assert client.get("/api/file-thumbnail", params={"path": str(sheet)}).content == PNG


def test_file_without_picture_is_404(client, project):
    deck = _office(project / "made-by-script.pptx", {"ppt/presentation.xml": b"<p/>"})
    assert client.get("/api/file-thumbnail", params={"path": str(deck)}).status_code == 404


def test_not_an_office_file(client, project):
    note = project / "note.txt"
    note.write_text("hi")
    assert client.get("/api/file-thumbnail", params={"path": str(note)}).status_code == 415


def test_outside_the_roots_is_refused(client, tmp_path):
    outside = _office(tmp_path / "elsewhere.pptx", {"docProps/thumbnail.png": PNG})
    assert client.get("/api/file-thumbnail", params={"path": str(outside)}).status_code == 403
