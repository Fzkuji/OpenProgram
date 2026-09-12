from __future__ import annotations

import hashlib
import json
import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState


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
    from openprogram.webui import office_assets

    root = tmp_path / "office"
    _pack(root)
    pack = office_assets.OfficeAssetPack.from_root(root)
    app = FastAPI()

    from openprogram.webui.routes.office_assets import register
    register(app)

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
    app.state.office_assets = pack
    return OwnerAuthMiddleware(app, auth_state=state, office_assets=pack), pack


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
    invalid = __import__("openprogram.webui.office_assets", fromlist=["OfficeAssetPack"]).OfficeAssetPack.from_root(pack.root)
    app.office_assets = invalid
    app.app.state.office_assets = invalid
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
    invalid = __import__("openprogram.webui.office_assets", fromlist=["OfficeAssetPack"]).OfficeAssetPack.from_root(pack.root)
    app.office_assets = invalid
    app.app.state.office_assets = invalid
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
    from openprogram.office_assets import OFFICE_PATCH_SHA256
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
