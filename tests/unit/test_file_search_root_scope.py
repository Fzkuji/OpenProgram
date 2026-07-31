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
