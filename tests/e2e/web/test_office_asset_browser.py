from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

pytestmark = pytest.mark.browser


def _free_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock, int(sock.getsockname()[1])


def _make_pack(root: Path) -> None:
    assets = {
        "office-host.html": b'<iframe src="/child.html"></iframe>',
        "child.html": b"<h1>Nested document ready</h1>",
        "reset.html": b"",
        "sw.js": b"self.addEventListener('fetch', () => {});",
        "document_editor_service_worker.js": b"importScripts('/sw.js');",
        "plugins.json": b"{}",
        "themes.json": b"{}",
        "onlyoffice-runtime-assets.json": b"{}",
    }
    for relative, content in assets.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / "LICENSE").write_text("license", encoding="utf-8")
    manifest = {
        "version": 1,
        "packageVersion": "0.3.34",
        "hostBuildId": "office-host-test",
        "source": "d15d12b6945be4d8b0f3aa1806120e740d2950ee",
        "licenses": ["LICENSE"],
        "assets": [
            {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in assets.items()
        ],
    }
    (root / "openprogram-office-assets.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def office_browser(tmp_path):
    from openprogram.webui.office_assets import OfficeAssetPack
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState

    import uvicorn
    from playwright.sync_api import sync_playwright

    root = tmp_path / "office"
    _make_pack(root)
    pack = OfficeAssetPack.from_root(root)
    sock, port = _free_socket()
    host = f"http://host-abc.office.localhost:{port}"
    app = FastAPI()

    @app.get("/")
    async def main():
        return HTMLResponse(f'<iframe src="{host}/office-host.html"></iframe>')

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=(),
    )
    server = uvicorn.Server(uvicorn.Config(OwnerAuthMiddleware(app, auth_state=state, office_assets=pack), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page()
            errors = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/")
            yield page, host, errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        state.close()
        sock.close()


def test_office_host_allows_nested_same_origin_frame(office_browser):
    page, host, errors = office_browser
    page.wait_for_load_state("networkidle")
    assert page.locator(f'iframe[src="{host}/office-host.html"]').count() == 1
    assert page.frame(url=f"{host}/office-host.html") is not None
    child = next(frame for frame in page.frames if frame.url == f"{host}/child.html")
    assert child.locator("h1").inner_text() == "Nested document ready"
    assert errors == []
