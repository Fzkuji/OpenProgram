from __future__ import annotations

import hashlib
import json
import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
from openprogram.programs.applications.office_editor.install import OFFICE_DOWNLOAD_BYTES


def _pack(root: Path) -> None:
    assets = {
        "office-host.html": b"<!doctype html><script>window.officeHost = true</script>",
        "reset.html": b"<!doctype html><title>reset</title>",
        "document_editor_service_worker.js": b"importScripts('/sw.js');",
        "sw.js": b"self.addEventListener('fetch', () => {});",
        "plugins.json": b"{}",
        "themes.json": b"{}",
        "onlyoffice-runtime-assets.json": b"{\"version\":1}",
        "web-apps/apps/api/documents/api.js": b"window.DocsAPI = {};",
        "wasm/x2t/x2t.wasm": b"wasm",
        "fonts/example.woff2": b"font",
        "npm/public-api.js": b"export const editor = true;",
    }
    for relative, content in assets.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
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
    (root / "LICENSE").write_text("license", encoding="utf-8")


@pytest.fixture
def boundary_app(tmp_path: Path):
    from openprogram.programs.applications.office_editor.assets import OfficeAssetPack
    from openprogram.programs.applications.office_editor.service import OfficeEditorService

    root = tmp_path / "office"
    _pack(root)
    pack = OfficeAssetPack.from_root(root)
    service = OfficeEditorService(pack)
    app = FastAPI()
    app.state.office = service

    from openprogram.interfaces.server.routes.files.office_assets import register
    register(app, office=lambda: app.state.office)

    @app.get("/api/x")
    async def api():
        return {"ok": True}

    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
    )
    app.state.owner_auth = state
    return OwnerAuthMiddleware(app, auth_state=state, office=lambda: app.state.office), pack


def test_valid_office_host_assets_are_public_and_manifest_bound(boundary_app):
    app, _ = boundary_app
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/office-host.html",
            headers={"host": "host-abc.office.localhost:18100"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'self' http://127.0.0.1:18100" in response.headers["content-security-policy"]
    assert "set-cookie" not in response.headers


def test_missing_office_pack_is_unavailable(boundary_app):
    app, pack = boundary_app
    pack.available = False
    pack.unavailable_reason = "managed runtime unavailable"
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/api/documents/office-host",
            headers={
                "origin": "http://127.0.0.1:18100",
                "host": "127.0.0.1:18100",
                "authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            },
        )
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_office_host_rejects_unknown_paths_methods_and_main_api(boundary_app):
    app, _ = boundary_app
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        headers = {"host": "host-abc.office.localhost:18100"}
        assert client.get("/../api/x", headers=headers).status_code in {404, 503}
        assert client.get("/unknown.js", headers=headers).status_code == 404
        assert client.post("/office-host.html", headers=headers).status_code == 405
        assert client.get("/api/x", headers={**headers, "authorization": "Bearer invalid"}).status_code == 404
        assert client.get("/office-host.html", headers={"host": "evil.office.localhost:18100"}).status_code == 403


@pytest.mark.parametrize(("field", "value"), (("source", "wrong-source"), ("packageVersion", "9.9.9")))
def test_unverified_source_or_package_closes_assets_and_availability(boundary_app, field, value):
    app, pack = boundary_app
    manifest_path = pack.root / "openprogram-office-assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    invalid = __import__("openprogram.programs.applications.office_editor.assets", fromlist=["OfficeAssetPack"]).OfficeAssetPack.from_root(pack.root)
    app.app.state.office.pack = invalid
    auth = {
        "origin": "http://127.0.0.1:18100",
        "host": "127.0.0.1:18100",
        "authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
    }
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        availability = client.get("/api/documents/office-host", headers=auth)
        asset = client.get("/office-host.html", headers={"host": "host-abc.office.localhost:18100"})
    assert invalid.available is False
    assert availability.status_code == 200
    assert availability.json()["available"] is False
    assert asset.status_code == 503


@pytest.mark.parametrize("kind", ("host_build_id", "asset_count", "license_count", "manifest_bytes"))
def test_manifest_bounds_close_assets_and_availability(boundary_app, kind):
    app, pack = boundary_app
    manifest_path = pack.root / "openprogram-office-assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if kind == "host_build_id":
        manifest["hostBuildId"] = "x" * 129
    elif kind == "asset_count":
        manifest["assets"] = manifest["assets"] * 1200
    elif kind == "license_count":
        manifest["licenses"] = ["LICENSE"] * 257
    else:
        manifest["padding"] = "x" * (4 * 1024 * 1024)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    invalid = __import__("openprogram.programs.applications.office_editor.assets", fromlist=["OfficeAssetPack"]).OfficeAssetPack.from_root(pack.root)
    app.app.state.office.pack = invalid
    auth = {
        "origin": "http://127.0.0.1:18100",
        "host": "127.0.0.1:18100",
        "authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
    }
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        availability = client.get("/api/documents/office-host", headers=auth)
        asset = client.get("/office-host.html", headers={"host": "host-abc.office.localhost:18100"})
    assert invalid.available is False
    assert availability.json()["available"] is False
    assert asset.status_code == 503


def test_availability_rejects_remote_allowed_origin_but_accepts_local_main_origin(boundary_app):
    app, pack = boundary_app
    state = OwnerAuthState.from_raw_token(
        bytes(range(32)),
        owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=("https://remote.example",),
    )
    app.auth_state = state
    app.app.state.owner_auth = state
    token = "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
    with TestClient(app, base_url="https://remote.example", client=("127.0.0.1", 50000)) as remote:
        remote_response = remote.get(
            "/api/documents/office-host?session_id=abc",
            headers={"origin": "https://remote.example", "authorization": token},
        )
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as local:
        local_response = local.get(
            "/api/documents/office-host?session_id=abc",
            headers={"origin": "http://127.0.0.1:18100", "authorization": token},
        )
    assert remote_response.json() == {"available": False, "reason": "local_main_origin_required"}
    assert local_response.json()["available"] is True


def test_parent_module_is_authenticated_and_uses_verified_pack(boundary_app):
    from openprogram.programs.applications.office_editor.assets import OFFICE_PATCH_SHA256
    app, pack = boundary_app
    url = f"/api/documents/office-module/{OFFICE_PATCH_SHA256}.js"
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")}
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        assert client.get(url).status_code == 401
        response = client.get(url, headers=auth)
        assert response.status_code == 200
        assert response.content == b"export const editor = true;"
        assert "javascript" in response.headers["content-type"]
        (pack.root / "npm/public-api.js").write_bytes(b"tampered")
        assert client.get(url, headers=auth).status_code == 503
        assert client.get("/api/documents/office-module/unknown.js", headers=auth).status_code == 404


def test_missing_office_offers_install_without_downloading(boundary_app):
    app, pack = boundary_app
    pack.available = False
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")}
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        response = client.get('/api/documents/office-host?session_id=abc', headers=auth)
        assert response.json()['installable'] is True
        assert response.json()['downloadBytes'] == OFFICE_DOWNLOAD_BYTES
        assert client.post('/api/documents/office-install').status_code == 401


def test_explicit_install_retries_and_activates_host_without_restart(boundary_app, monkeypatch):
    from copy import copy
    import openprogram.programs.applications.office_editor.install as installer
    app, pack = boundary_app
    installed = copy(pack)
    pack.available = False
    calls = []
    def download():
        calls.append(True)
        if len(calls) == 1: raise OSError('offline')
        return installed
    monkeypatch.setattr(installer, 'download_office_pack', download)
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            "origin": "http://127.0.0.1:18100"}
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        assert client.get('/api/documents/office-host?session_id=abc', headers=auth).json()['installable']
        assert not calls
        assert client.post('/api/documents/office-install', headers=auth).status_code == 503
        assert not app.app.state.office.pack.available
        assert client.post('/api/documents/office-install', headers=auth).json()['installed']
        assert client.post('/api/documents/office-install', headers=auth).json()['installed']
        assert len(calls) == 2
        assert client.get('/api/documents/office-host?session_id=abc', headers=auth).json()['available']
        assert client.get('/office-host.html', headers={'host':'host-abc.office.localhost:18100'}).status_code == 200


def test_install_for_a_version_no_release_serves_says_which_update_brings_office(boundary_app, monkeypatch):
    import openprogram.programs.applications.office_editor.install as installer
    app, pack = boundary_app
    pack.available = False
    def download():
        raise installer.OfficeArchiveUnpublished('1.2.0')
    monkeypatch.setattr(installer, 'download_office_pack', download)
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            "origin": "http://127.0.0.1:18100"}
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        response = client.post('/api/documents/office-install', headers=auth)
    assert response.status_code == 503
    body = response.json()
    assert body['code'] == 'office_release_missing'
    assert body['version'] == '1.2.0' and body['updateTo'] == '1.3.0'
    assert 'Update OpenProgram to 1.3.0 or newer' in body['error']


def test_concurrent_install_requests_download_once(boundary_app, monkeypatch):
    import asyncio
    from copy import copy
    import httpx
    import openprogram.programs.applications.office_editor.install as installer
    app, pack = boundary_app
    installed = copy(pack)
    pack.available = False
    calls = []
    def download():
        calls.append(True)
        return installed
    monkeypatch.setattr(installer, 'download_office_pack', download)
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            "origin": "http://127.0.0.1:18100"}
    async def requests():
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 50000))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:18100', headers=auth) as client:
            responses = await asyncio.gather(*(client.post('/api/documents/office-install') for _ in range(3)))
        assert all(response.json().get('installed') for response in responses)
    asyncio.run(requests())
    assert len(calls) == 1


def test_install_rejects_authenticated_remote_origin(boundary_app, monkeypatch):
    import openprogram.programs.applications.office_editor.install as installer
    app, _ = boundary_app
    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id='owner/install/0123456789abcdef',
        bind_host='127.0.0.1', port=18100, allowed_origins=('https://remote.example',))
    app.auth_state = state
    app.app.state.owner_auth = state
    calls = []
    monkeypatch.setattr(installer, 'download_office_pack', lambda: calls.append(True))
    token = 'Bearer ' + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    try:
        with TestClient(app, base_url='https://remote.example', client=('127.0.0.1', 50000)) as client:
            response = client.post('/api/documents/office-install', headers={'origin':'https://remote.example','authorization':token})
        assert response.status_code == 403
        assert response.json()['reason'] == 'local_main_origin_required'
        assert not calls
    finally:
        state.close()


def test_disabled_office_editor_leaves_download_only(boundary_app):
    app, _ = boundary_app
    app.app.state.office = None
    auth = {"authorization": "Bearer " + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            "origin": "http://127.0.0.1:18100"}
    from openprogram.programs.applications.office_editor.assets import OFFICE_PATCH_SHA256
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/documents/office-host?session_id=abc", headers=auth).json() == {
            "available": False, "reason": "office_editor_disabled"}
        install = client.post("/api/documents/office-install", headers=auth)
        assert install.status_code == 404 and install.json()["code"] == "office_editor_disabled"
        assert client.get(f"/api/documents/office-module/{OFFICE_PATCH_SHA256}.js", headers=auth).status_code == 404
        # No isolated Office origin exists: its Host is refused like any other.
        assert client.get("/office-host.html", headers={"host": "host-abc.office.localhost:18100"}).status_code in {400, 403}
        assert client.get("/api/x", headers=auth).json() == {"ok": True}
