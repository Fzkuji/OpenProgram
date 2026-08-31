"""webui/frontend.py — static-export serving + build gate (single-port)."""
from __future__ import annotations

import gzip

import pytest

from openprogram.webui import frontend


@pytest.fixture()
def out_tree(tmp_path, monkeypatch):
    """A fake apps/web/out/ export plus monkeypatched locators."""
    wd = tmp_path / "web"
    out = wd / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "_next" / "static" / "chunks" / "app.js").write_text("js")
    (out / "_next" / "static" / "app.css").write_text("css")
    (out / "icons").mkdir()
    (out / "icons" / "mark.svg").write_text("<svg></svg>")
    (out / "icons" / "icon-512.png").write_bytes(b"png")
    (out / "fonts").mkdir()
    (out / "fonts" / "inter.woff2").write_bytes(b"woff2")
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
    from fastapi.responses import Response, StreamingResponse
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"pong": True}

    @app.get("/api/events")
    async def events():
        return StreamingResponse(
            iter(["data: " + ("event" * 200) + "\n\n"]),
            media_type="text/event-stream",
        )

    @app.get("/api/precompressed")
    async def precompressed():
        return Response(
            gzip.compress(b"already encoded" * 100),
            media_type="text/plain",
            headers={"Content-Encoding": "gzip"},
        )

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


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("/_next/static/chunks/app.js", b"const payload = 'compressible';\n" * 400),
        ("/_next/static/app.css", b".row { color: var(--text); }\n" * 400),
        ("/icons/mark.svg", b'<svg><path d="M0 0h10v10"/></svg>' * 400),
    ],
)
def test_large_static_text_negotiates_gzip(client, out_tree, path, source):
    _wd, out = out_tree
    (out / path.lstrip("/")).write_bytes(source)

    compressed = client.get(
        path,
        headers={"Accept-Encoding": "gzip"},
    )
    assert compressed.status_code == 200
    assert compressed.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in compressed.headers["vary"]
    assert compressed.content == source

    identity = client.get(
        path,
        headers={"Accept-Encoding": "identity"},
    )
    assert "content-encoding" not in identity.headers
    assert identity.content == source


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("/icons/icon-512.png", b"\x89PNG\r\n\x1a\n" + b"compressed-png" * 400),
        ("/fonts/inter.woff2", b"wOF2" + b"compressed-font" * 400),
    ],
)
def test_precompressed_static_formats_are_not_gzipped(client, out_tree, path, source):
    _wd, out = out_tree
    (out / path.lstrip("/")).write_bytes(source)

    response = client.get(path, headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == source


def test_small_static_text_is_not_compressed(client):
    r = client.get(
        "/_next/static/chunks/app.js",
        headers={"Accept-Encoding": "gzip"},
    )
    assert "content-encoding" not in r.headers


def test_gzip_excludes_sse_and_preserves_existing_encoding(client):
    events = client.get("/api/events", headers={"Accept-Encoding": "gzip"})
    assert events.status_code == 200
    assert "content-encoding" not in events.headers

    encoded = client.get(
        "/api/precompressed",
        headers={"Accept-Encoding": "gzip"},
    )
    assert encoded.status_code == 200
    assert encoded.headers["content-encoding"] == "gzip"
    assert encoded.content == b"already encoded" * 100


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
    assert calls == ["npm install", "npm workspace build"]


def test_build_gate_errors_without_out_and_npm(out_tree, monkeypatch):
    wd, out = out_tree
    import shutil as _sh
    _sh.rmtree(out)
    (wd / "app").mkdir(exist_ok=True)
    monkeypatch.setattr(frontend.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="npm is not in PATH"):
        frontend.ensure_frontend_built()


def test_installed_package_prefers_bundled_frontend(tmp_path, monkeypatch):
    bundled = (
        tmp_path
        / "site-packages"
        / "openprogram_server"
        / "_webui"
        / "_frontend"
    )
    bundled.mkdir(parents=True)
    (bundled / "index.html").write_text("bundled", encoding="utf-8")
    checkout = tmp_path / "checkout" / "web" / "out"
    checkout.mkdir(parents=True)
    (checkout / "index.html").write_text("checkout", encoding="utf-8")

    monkeypatch.setattr(frontend, "packaged_out_dir", lambda: bundled)
    monkeypatch.setattr(frontend, "repo_out_dir", lambda: checkout)
    monkeypatch.setattr(frontend, "web_dir", lambda: checkout.parent)

    assert frontend.out_dir() == bundled


def test_unknown_api_path_is_404_not_spa(client):
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")
