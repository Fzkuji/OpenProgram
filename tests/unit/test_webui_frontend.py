"""webui/frontend.py — static-export serving + build gate (single-port)."""
from __future__ import annotations

import pytest

from openprogram.webui import frontend


@pytest.fixture()
def out_tree(tmp_path, monkeypatch):
    """A fake web/out/ export plus monkeypatched locators."""
    wd = tmp_path / "web"
    out = wd / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "_next" / "static" / "chunks" / "app.js").write_text("js")
    (out / "index.html").write_text("<html>index</html>")
    (out / "chat.html").write_text("<html>chat</html>")
    (out / "skills.html").write_text("<html>skills</html>")
    (out / "settings").mkdir()
    (out / "settings" / "providers.html").write_text("<html>providers</html>")
    monkeypatch.setattr(frontend, "web_dir", lambda: wd)
    monkeypatch.setattr(frontend, "out_dir", lambda: out)
    return wd, out


@pytest.fixture()
def client(out_tree):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"pong": True}

    frontend.mount_frontend(app)
    return TestClient(app)


def test_api_routes_win_over_catch_all(client):
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": True}


def test_html_served_with_no_cache(client):
    r = client.get("/chat")
    assert r.status_code == 200
    assert "chat" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_next_static_immutable_cache(client):
    r = client.get("/_next/static/chunks/app.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_spa_fallback_nearest_ancestor_page(client):
    # /skills/<name> has no exported file — falls back to skills.html.
    assert "skills" in client.get("/skills/pdf").text
    # nested ancestor: /settings/providers/<id> → settings/providers.html
    assert "providers" in client.get("/settings/providers/openai").text


def test_spa_fallback_unknown_path_serves_chat_shell(client):
    r = client.get("/s/abc123")
    assert r.status_code == 200
    assert "chat" in r.text


def test_path_traversal_falls_back(client):
    r = client.get("/..%2f..%2fetc%2fpasswd")
    assert r.status_code == 200
    assert "passwd" not in r.text


def test_build_gate_skips_when_fresh(out_tree, monkeypatch):
    calls = []
    monkeypatch.setattr(frontend, "_run", lambda *a, **k: calls.append(a))
    frontend.ensure_frontend_built()  # out/index.html newer than sources
    assert calls == []


def test_build_gate_rebuilds_when_stale(out_tree, monkeypatch):
    wd, out = out_tree
    app_dir = wd / "app"
    app_dir.mkdir()
    src = app_dir / "page.tsx"
    src.write_text("x")
    import os
    future = (out / "index.html").stat().st_mtime + 100
    os.utime(src, (future, future))

    calls = []

    def fake_run(cmd, cwd, what):
        calls.append(what)

    monkeypatch.setattr(frontend, "_run", fake_run)
    monkeypatch.setattr(frontend.shutil, "which", lambda _: "/usr/bin/npm")
    frontend.ensure_frontend_built()
    assert "next build" in calls


def test_build_gate_errors_without_out_and_npm(out_tree, monkeypatch):
    wd, out = out_tree
    import shutil as _sh
    _sh.rmtree(out)
    (wd / "app").mkdir(exist_ok=True)
    monkeypatch.setattr(frontend.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="npm is not in PATH"):
        frontend.ensure_frontend_built()
