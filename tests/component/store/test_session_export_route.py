"""GET /api/sessions/{id}/export — download a session as Markdown / HTML.

The rendering itself is covered by ``test_session_export.py``; here we
only pin the HTTP contract: format negotiation, download headers, and
the 400/404 rejections.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeDB:
    def get_session(self, session_id):
        return {"title": "Fake session"} if session_id == "s1" else None

    def get_branch(self, session_id, head_id=None):
        return [
            {"id": "n1", "role": "user", "content": "hello", "timestamp": 0},
            {"id": "n2", "role": "assistant", "content": "hi", "timestamp": 0},
        ]

    def get_messages(self, session_id, limit=None):
        return self.get_branch(session_id)


@pytest.fixture
def client(monkeypatch):
    import openprogram.agent.session_db as _sdb
    monkeypatch.setattr(_sdb, "default_db", lambda: _FakeDB())

    app = FastAPI()
    from openprogram.webui.routes import export as _export
    _export.register(app)
    return TestClient(app)


def test_markdown_is_the_default_format(client):
    r = client.get("/api/sessions/s1/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="s1.md"'
    assert "# Fake session" in r.text
    assert "hello" in r.text


def test_html_format_returns_a_full_document(client):
    r = client.get("/api/sessions/s1/export", params={"format": "html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == 'attachment; filename="s1.html"'
    assert r.text.startswith("<!DOCTYPE html>")
    assert "prefers-color-scheme" in r.text


def test_unknown_format_is_rejected(client):
    r = client.get("/api/sessions/s1/export", params={"format": "pdf"})
    assert r.status_code == 400


def test_missing_session_is_404(client):
    assert client.get("/api/sessions/nope/export").status_code == 404


def test_path_traversal_in_session_id_is_rejected(client):
    r = client.get("/api/sessions/..%2F..%2Fetc/export")
    assert r.status_code in (400, 404)
