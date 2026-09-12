"""Fixed, manifest-bound resources for the isolated local Office host."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

from openprogram.backend_endpoint import is_loopback_host
from openprogram.updater.detect import managed_runtime_root

_MANIFEST = "openprogram-office-assets.json"
_HOST_RE = re.compile(r"^host-([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.office\.localhost$")
_BOOTSTRAP = frozenset({
    "office-host.html", "reset.html", "document_editor_service_worker.js", "sw.js",
    "plugins.json", "themes.json", "onlyoffice-runtime-assets.json",
})


@dataclass
class OfficeAssetPack:
    root: Path
    manifest: dict
    manifest_bytes: bytes
    runtime_manifest_bytes: bytes
    assets: dict[str, tuple[int, str, int, int]]
    available: bool = True
    unavailable_reason: str | None = None

    @classmethod
    def unavailable_pack(cls, root: Path, reason: str) -> "OfficeAssetPack":
        return cls(root, {}, b"", b"", {}, False, reason)

    @classmethod
    def from_root(cls, root: Path) -> "OfficeAssetPack":
        root = Path(root).absolute()
        try:
            manifest_path = root / _MANIFEST
            raw = manifest_path.read_bytes()
            runtime_raw = (root / "onlyoffice-runtime-assets.json").read_bytes()
            manifest = json.loads(raw)
            if not isinstance(manifest, dict) or manifest.get("version") != 1:
                raise ValueError("invalid Office asset manifest version")
            for key in ("packageVersion", "hostBuildId", "source", "assets", "licenses"):
                if not manifest.get(key):
                    raise ValueError("invalid Office asset manifest")
            if not isinstance(manifest["assets"], list) or not isinstance(manifest["licenses"], list):
                raise ValueError("invalid Office asset manifest collections")
            assets: dict[str, tuple[int, str, int, int]] = {}
            for item in manifest["assets"]:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("invalid Office asset entry")
                rel = item["path"]
                path = _safe_relative(rel)
                if path in assets or int(item.get("bytes", -1)) < 0 or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", ""))):
                    raise ValueError("invalid or duplicate Office asset entry")
                target = _contained_file(root, path)
                if target is None:
                    raise ValueError("Office asset unavailable")
                content_size = target.stat().st_size
                if content_size != int(item["bytes"]):
                    raise ValueError("Office asset size mismatch")
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != item["sha256"]:
                    raise ValueError("Office asset digest mismatch")
                stat = target.stat()
                assets[path] = (content_size, digest, stat.st_mtime_ns, stat.st_ino)
            if not _BOOTSTRAP.issubset(assets):
                raise ValueError("Office asset manifest omits bootstrap resource")
            for license_path in manifest["licenses"]:
                if not isinstance(license_path, str) or _contained_file(root, _safe_relative(license_path)) is None:
                    raise ValueError("invalid Office asset license")
            return cls(root, manifest, raw, runtime_raw, assets)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return cls.unavailable_pack(root, "invalid Office asset pack")


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid Office asset path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("invalid Office asset path")
    return value


def _contained_file(root: Path, relative: str) -> Path | None:
    try:
        target = (root / relative).resolve(strict=True)
        if not target.is_file() or target.is_symlink() or not target.is_relative_to(root.resolve()):
            return None
        current = root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                return None
        return target
    except (OSError, ValueError):
        return None


def load_installed_office_pack() -> OfficeAssetPack:
    runtime = managed_runtime_root()
    if runtime is None:
        return OfficeAssetPack.unavailable_pack(Path(""), "managed runtime unavailable")
    return OfficeAssetPack.from_root(runtime / "assets" / "office")


def _host_session(scope, port: int) -> str | None:
    if not is_loopback_host(str((scope.get("client") or ("",))[0])):
        return None
    if not _HOST_RE.fullmatch(_host_without_port(scope)):
        return None
    host = _one_host(scope)
    if host is None or host[1] != str(port):
        return None
    return _HOST_RE.fullmatch(host[0]).group(1)  # type: ignore[union-attr]


def _one_host(scope) -> tuple[str, str] | None:
    values = [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == b"host"]
    if len(values) != 1 or ":" not in values[0]:
        return None
    hostname, port = values[0].rsplit(":", 1)
    return hostname, port


def _host_without_port(scope) -> str:
    value = _one_host(scope)
    return value[0] if value else ""


def is_office_host(scope, port: int) -> bool:
    return _host_session(scope, port) is not None


async def serve_asset(scope, receive, send, pack: OfficeAssetPack, port: int, frame_ancestors: str) -> None:
    if scope.get("type") != "http" or scope.get("method") not in {"GET", "HEAD"}:
        await _send_error(send, 405, "office_asset_method_rejected")
        return
    if not pack.available:
        await _send_error(send, 503, "office_assets_unavailable")
        return
    relative = str(scope.get("path") or "").lstrip("/")
    try:
        relative = _safe_relative(relative)
    except ValueError:
        await _send_error(send, 404, "office_asset_not_found")
        return
    if relative not in pack.assets:
        if relative == _MANIFEST:
            return_response = Response(pack.manifest_bytes, media_type="application/json", headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
            await return_response(scope, receive, send)
            return
        await _send_error(send, 404, "office_asset_not_found")
        return
    target = _contained_file(pack.root, relative)
    if target is None:
        await _send_error(send, 503, "office_asset_unavailable")
        return
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"default-src 'none'; base-uri 'none'; object-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' blob:; worker-src 'self' blob:; frame-src 'self'; connect-src 'self' blob:; frame-ancestors {frame_ancestors}",
    }
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        stat = os.fstat(fd)
        expected = pack.assets[relative]
        if stat.st_size != expected[0] or stat.st_mtime_ns != expected[2] or stat.st_ino != expected[3]:
            os.close(fd)
            await _send_error(send, 503, "office_asset_changed")
            return
    except OSError:
        await _send_error(send, 503, "office_asset_unavailable")
        return
    headers["Content-Length"] = str(stat.st_size)
    headers["Content-Type"] = _media_type(target)

    async def body():
        try:
            while True:
                chunk = await __import__("asyncio").to_thread(os.read, fd, 1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            os.close(fd)

    await StreamingResponse(body(), headers=headers)(scope, receive, send)


def _media_type(path: Path) -> str:
    if path.suffix.lower() == ".wasm":
        return "application/wasm"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def _send_error(send, status: int, error: str) -> None:
    body = json.dumps({"error": error}, separators=(",", ":")).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


def office_host_availability(request, pack: OfficeAssetPack) -> dict:
    client = request.client.host if request.client else ""
    if not is_loopback_host(client):
        return {"available": False, "reason": "local_client_required"}
    if not pack.available:
        return {"available": False, "reason": pack.unavailable_reason or "unavailable"}
    session = request.query_params.get("session_id", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", session):
        return {"available": False, "reason": "invalid_session_id"}
    return {
        "available": True,
        "hostUrl": f"http://host-{session}.office.localhost:{request.app.state.owner_auth.port}/office-host.html",
        "packageVersion": pack.manifest["packageVersion"],
        "hostBuildId": pack.manifest["hostBuildId"],
        "assetManifestDigest": hashlib.sha256(pack.runtime_manifest_bytes).hexdigest(),
    }


__all__ = ["OfficeAssetPack", "is_office_host", "load_installed_office_pack", "office_host_availability", "serve_asset"]
