"""Real local Office engines through DocumentWindow and authenticated routes.

Run with OPENPROGRAM_TEST_OFFICE_PACK pointing to an explicitly prepared pack.
An omitted external pack is reported as a skip, never Office acceptance.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def office_window_bundle(tmp_path_factory):
    output = tmp_path_factory.mktemp("office-window-build") / "window.js"
    subprocess.run(["node", "apps/web/tests/build-office-document-browser.mjs", str(output)], check=True)
    return output.read_bytes()


@pytest.fixture
def office_window(tmp_path, monkeypatch, office_window_bundle):
    import uvicorn
    from playwright.sync_api import sync_playwright
    from openprogram.office_assets import validate_prepared_office_pack
    from openprogram.store.project import project_store
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.webui.routes.documents import register as register_documents
    from openprogram.webui.routes.office_assets import register as register_office
    from openprogram.webui.routes.file_search import register as register_files

    configured = os.environ.get("OPENPROGRAM_TEST_OFFICE_PACK")
    if not configured:
        pytest.skip("Explicit OPENPROGRAM_TEST_OFFICE_PACK is required for native Office acceptance")
    pack = validate_prepared_office_pack(Path(configured))
    project_root = tmp_path / "project"
    project_root.mkdir()
    for ext in ("docx", "pptx", "xlsx", "odt", "odp", "ods"):
        shutil.copy2(Path(__file__).parent / "fixtures" / f"office-baseline.{ext}", project_root / f"baseline.{ext}")
    for ext in ("doc", "ppt", "xls"):
        shutil.copy2(Path(__file__).parent / "fixtures" / f"office-legacy.{ext}", project_root / f"legacy.{ext}")
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    project = types.SimpleNamespace(id="p", path=str(project_root), is_default=True)
    monkeypatch.setattr(project_store, "get_project", lambda key: project if key == "p" else None)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                         bind_host="127.0.0.1", port=port, allowed_origins=())
    app = FastAPI()
    app.state.owner_auth = state
    app.state.office_assets = pack
    # Reuse the real download route without entering the worker lifespan.
    from openprogram.webui.server import create_app
    raw_app = create_app(owner_auth=state, port=port)
    app.router.routes.extend(route for route in raw_app.routes if getattr(route, "path", None) == "/files/raw")
    register_documents(app)
    register_office(app)
    register_files(app)
    monkeypatch.setattr("openprogram.attachments.readable_roots", lambda session=None: [project_root])

    @app.get("/")
    async def index():
        return HTMLResponse('''<style>html,body,#root{height:100%;margin:0}.fixture-pane{height:100%;min-height:0}.window{flex:1;min-width:0;width:100%;height:100%;display:flex;flex-direction:column}.body{flex:1;min-height:0;overflow:auto}.toolbar{min-height:40px;display:flex;gap:8px}.history{max-height:150px;overflow:auto}</style><div id="root"></div><script src="/_next/static/office-window.js"></script>''')

    @app.get("/_next/static/office-window.js")
    async def bundle():
        return Response(office_window_bundle, media_type="text/javascript")

    server = uvicorn.Server(uvicorn.Config(OwnerAuthMiddleware(app, state, office_assets=pack), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            response = page.request.post(f"http://127.0.0.1:{port}/api/auth/bootstrap",
                                         data={"token": state.token},
                                         headers={"Origin": f"http://127.0.0.1:{port}"})
            assert response.status == 204
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                yield page, f"http://127.0.0.1:{port}", project_root, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        state.close()
        sock.close()


@pytest.mark.parametrize("extension", ["docx", "pptx", "xlsx", "odt", "odp", "ods"])
def test_real_office_window_preview_and_edit(office_window, extension):
    from playwright.sync_api import expect
    page, origin, _, errors = office_window
    page.goto(origin + f"/?file=baseline.{extension}")
    expect(page.get_by_role("button", name="Preview", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.locator('[data-office-editor]')).to_be_visible(timeout=45000)
    expect(page.locator('[data-office-editor] > iframe')).to_have_count(1)
    native = next(frame for frame in page.frames if "/web-apps/apps/" in frame.url and "/main/index.html" in frame.url)
    save_slot = native.locator('[data-layout-name="header-save"]')
    expect(save_slot).to_have_count(1)
    expect(save_slot).not_to_be_visible()
    original = page.locator('[data-office-editor] > iframe').element_handle()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-office-editor]:visible')).to_have_attribute("aria-busy", "false")
    expect(page.get_by_role("button", name="Edit", exact=True)).to_have_attribute("aria-pressed", "true")
    assert page.locator('[data-office-editor] > iframe').evaluate('(node,original) => node === original', original)
    page.get_by_role("button", name="Close file", exact=True).click()
    expect(page.get_by_text("File closed", exact=True)).to_be_visible(timeout=20000)
    assert errors == []


def test_sheet_close_persists_pending_cell_input(office_window):
    from zipfile import ZipFile
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    page.goto(origin + "/?file=baseline.xlsx")
    expect(page.locator('[data-office-editor]')).to_be_visible(timeout=45000)
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-office-editor]:visible')).to_have_attribute("aria-busy", "false")
    bounds = page.locator('[data-office-editor] > iframe').bounding_box()
    assert bounds
    page.mouse.click(bounds["x"] + 140, bounds["y"] + 135)
    page.keyboard.press("F2")
    page.keyboard.press("End")
    page.keyboard.type(" PENDING_CELL_CLOSE")
    page.get_by_role("button", name="Close file", exact=True).click()
    expect(page.get_by_text("File closed", exact=True)).to_be_visible(timeout=20000)
    with ZipFile(project / "baseline.xlsx") as archive:
        assert any(b"PENDING_CELL_CLOSE" in archive.read(name)
                   for name in archive.namelist() if name.endswith(".xml"))
    assert errors == []


def test_sheet_history_and_preview_preserve_native_undo(office_window):
    from zipfile import ZipFile
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    page.goto(origin + "/?file=baseline.xlsx")
    host = page.locator('[data-office-editor]').first
    expect(host).to_be_visible(timeout=45000)
    original = host.locator(":scope > iframe").element_handle()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-office-editor]:visible')).to_have_attribute("aria-busy", "false")
    bounds = host.bounding_box()
    page.mouse.click(bounds["x"] + 140, bounds["y"] + 135)
    page.keyboard.press("F2")
    page.keyboard.press("End")
    page.keyboard.type(" UNDO_AFTER_PREVIEW")
    page.keyboard.press("Enter")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("button", name="Preview", exact=True)).to_have_attribute("aria-pressed", "true")
    with ZipFile(project / "baseline.xlsx") as archive:
        assert any(b"UNDO_AFTER_PREVIEW" in archive.read(name)
                   for name in archive.namelist() if name.endswith(".xml"))
    page.get_by_role("button", name="Split files", exact=True).click()
    expect(page.locator('[data-file-pane-id="peer"] [data-office-editor]')).to_be_visible(timeout=45000)
    page.get_by_role("button", name="Swap panes", exact=True).click()
    assert host.locator(":scope > iframe").evaluate("(node,original)=>node===original", original)
    page.get_by_role("button", name="Split files", exact=True).click()
    page.get_by_role("button", name="Other tab", exact=True).click()
    expect(host).not_to_be_visible()
    page.get_by_role("button", name="Return to file", exact=True).click()
    expect(host).to_be_visible()
    assert host.locator(":scope > iframe").evaluate("(node,original)=>node===original", original)
    page.get_by_role("button", name="Open settings route", exact=True).click()
    assert "/settings?" in page.url
    expect(host).not_to_be_visible()
    page.get_by_role("button", name="Back to file route", exact=True).click()
    expect(host).to_be_visible()
    assert host.locator(":scope > iframe").evaluate("(node,original)=>node===original", original)
    page.get_by_role("button", name="History", exact=True).click()
    with page.expect_response(lambda response: "/api/documents/history/content?" in response.url) as response_info:
        page.get_by_role("button", name="Before", exact=True).first.click()
    assert response_info.value.status == 200
    expect(page.locator('[data-file-pane-id="file"] [data-office-editor]').nth(1)).to_be_visible(timeout=45000)
    assert host.locator(":scope > iframe").evaluate("(node,original)=>node===original", original)
    before_history_input = (project / "baseline.xlsx").read_bytes()
    writes = []
    def capture_write(request):
        if request.method in {"PUT", "PATCH", "DELETE"}:
            writes.append(request.url)
    page.on("request", capture_write)
    history_host = page.locator('[data-file-pane-id="file"] [data-office-editor]').nth(1)
    bounds = history_host.bounding_box()
    page.mouse.click(bounds["x"] + 140, bounds["y"] + 135)
    page.keyboard.press("F2")
    page.keyboard.type("NO_HISTORY_WRITE")
    page.keyboard.press("Enter")
    page.get_by_role("button", name="Back to current file", exact=True).click()
    page.remove_listener("request", capture_write)
    assert writes == []
    assert (project / "baseline.xlsx").read_bytes() == before_history_input
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-office-editor]:visible')).to_have_attribute("aria-busy", "false")
    bounds = host.bounding_box()
    page.mouse.click(bounds["x"] + 140, bounds["y"] + 135)
    native = next(frame for frame in page.frames if "/spreadsheeteditor/main/index.html" in frame.url)
    native.get_by_role("button", name=re.compile(r"^Undo ")).click()
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("button", name="Preview", exact=True)).to_have_attribute("aria-pressed", "true")
    with ZipFile(project / "baseline.xlsx") as archive:
        assert not any(b"UNDO_AFTER_PREVIEW" in archive.read(name)
                       for name in archive.namelist() if name.endswith(".xml"))
    assert errors == []


@pytest.mark.parametrize("extension,target", [("doc", "docx"), ("ppt", "pptx"), ("xls", "xlsx")])
def test_legacy_conversion_creates_new_file(office_window, extension, target):
    from zipfile import ZipFile
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    original = (project / f"legacy.{extension}").read_bytes()
    downloads = []
    page.on("download", lambda item: downloads.append(item))
    page.goto(origin + f"/?file=legacy.{extension}")
    button = page.get_by_role("button", name=f"Convert to {target.upper()}", exact=True)
    expect(button).to_be_enabled(timeout=15000)
    page.once("dialog", lambda dialog: dialog.accept(f"converted.{target}"))
    with page.expect_response(lambda response: response.request.method == "PUT" and "/api/documents/content?" in response.url, timeout=45000) as published:
        button.click()
    assert published.value.status == 200
    expect(button).to_be_enabled(timeout=10000)
    assert (project / f"legacy.{extension}").read_bytes() == original
    with ZipFile(project / f"converted.{target}") as archive:
        assert archive.testzip() is None
        assert "[Content_Types].xml" in archive.namelist()
    assert downloads == []
    history = page.request.get(origin + f"/api/documents/history?project_id=p&path=converted.{target}")
    assert history.status == 200 and len(history.json()["entries"]) == 1
    page.goto(origin + f"/?file=converted.{target}")
    expect(page.locator('[data-office-editor]')).to_be_visible(timeout=45000)
    expect(page.get_by_role("button", name="Edit", exact=True)).to_be_enabled()
    assert errors == []


def test_legacy_conversion_does_not_overwrite_existing_target(office_window):
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    destination = project / "existing.docx"
    destination.write_bytes((project / "baseline.docx").read_bytes())
    before = destination.read_bytes()
    source = (project / "legacy.doc").read_bytes()
    page.goto(origin + "/?file=legacy.doc")
    button = page.get_by_role("button", name="Convert to DOCX", exact=True)
    expect(button).to_be_enabled(timeout=15000)
    page.once("dialog", lambda dialog: dialog.accept("existing.docx"))
    button.click()
    expect(page.get_by_role("alert")).to_contain_text("destination already exists", timeout=45000)
    assert destination.read_bytes() == before
    assert (project / "legacy.doc").read_bytes() == source
    assert errors == []


def test_word_edits_autosave_without_preview_or_close(office_window):
    from zipfile import ZipFile
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    page.goto(origin + "/?file=baseline.docx")
    host = page.locator('[data-office-editor]')
    expect(host).to_be_visible(timeout=45000)
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-office-editor]:visible')).to_have_attribute("aria-busy", "false")
    bounds = host.bounding_box()
    page.mouse.click(bounds["x"] + 300, bounds["y"] + 215)
    page.keyboard.press("ControlOrMeta+End")
    with page.expect_response(lambda response: response.request.method == "PUT" and "/api/documents/content?" in response.url, timeout=15000) as published:
        page.keyboard.type(" WORD_AUTOSAVE")
    assert published.value.status == 200
    with ZipFile(project / "baseline.docx") as archive:
        assert b"WORD_AUTOSAVE" in archive.read("word/document.xml")
    assert errors == []


def test_office_attachment_is_readonly_and_reads_original_bytes(office_window):
    from urllib.parse import urlencode
    from playwright.sync_api import expect
    page, origin, project, errors = office_window
    source = project / "baseline.docx"
    before = source.read_bytes()
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method in {"PUT", "PATCH", "DELETE"} else None)
    page.goto(origin + "/?" + urlencode({"file": str(source), "attachment": "1"}))
    expect(page.locator('[data-office-editor]')).to_be_visible(timeout=45000)
    expect(page.get_by_role("button", name="Edit", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="History", exact=True)).to_have_count(0)
    bounds = page.locator('[data-office-editor]').bounding_box()
    page.mouse.click(bounds["x"] + 300, bounds["y"] + 215)
    page.keyboard.type("NO_ATTACHMENT_WRITE")
    page.get_by_role("button", name="Preview", exact=True).click()
    assert source.read_bytes() == before
    assert writes == [] and errors == []


@pytest.mark.parametrize("oversize", [False, True], ids=["corrupt", "oversize"])
def test_invalid_office_retains_original_download(office_window, oversize):
    import hashlib
    from playwright.sync_api import expect
    page, origin, project, _ = office_window
    source = project / "invalid.docx"
    with source.open("wb") as stream:
        stream.write(b"not an Office document")
        if oversize:
            stream.truncate(64 * 1024 * 1024 + 1)
    page.goto(origin + "/?file=invalid.docx")
    expect(page.get_by_role("alert").first).to_be_visible(timeout=45000)
    expect(page.locator('[data-office-editor] iframe')).to_have_count(0, timeout=15000)
    with page.expect_download() as downloaded:
        page.get_by_role("link", name="Download disk file", exact=True).click()
    actual = downloaded.value.path()
    assert actual is not None
    with open(actual, "rb") as stream, source.open("rb") as original:
        assert hashlib.file_digest(stream, "sha256").digest() == hashlib.file_digest(original, "sha256").digest()
