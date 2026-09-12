"""Fixed, manifest-bound resources for the isolated local Office host."""
from __future__ import annotations

import hashlib
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi.responses import Response, StreamingResponse

from openprogram.backend_endpoint import OwnerAuthError, canonicalize_origin, is_loopback_host
from openprogram.updater.detect import managed_runtime_root

from openprogram.office_assets import (
    OfficeAssetPack, _MANIFEST,
    _safe_relative, _contained_file, prepared_office_cache,
    validate_prepared_office_pack,
)


_HOST_RE = re.compile(r"^host-([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.office\.localhost$")
_INLINE_SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)


def load_installed_office_pack() -> OfficeAssetPack:
    runtime = managed_runtime_root()
    root = runtime / "assets" / "office" if runtime else prepared_office_cache()
    try:
        return validate_prepared_office_pack(root)
    except (OSError, ValueError):
        return OfficeAssetPack.unavailable_pack(root, "verified Office resources unavailable")


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
        "Content-Security-Policy": _asset_csp(target, frame_ancestors),
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


def _asset_csp(path: Path, frame_ancestors: str) -> str:
    script_hashes: list[str] = []
    unsafe_hashes = False
    if path.suffix.lower() in {".html", ".htm"}:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        for body in _INLINE_SCRIPT_RE.findall(text):
            if body.strip():
                value = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
                script_hashes.append(f"'sha256-{value}'")
        for _, body in _INLINE_HANDLER_RE.findall(text):
            value = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            script_hashes.append(f"'sha256-{value}'")
            unsafe_hashes = True
    hash_suffix = (" " + " ".join(dict.fromkeys(script_hashes))) if script_hashes else ""
    handler_suffix = " 'unsafe-hashes'" if unsafe_hashes else ""
    # The pinned native editor uses runtime-compiled templates. This applies
    # only to its static entry documents on the isolated Office origin.
    eval_suffix = " 'unsafe-eval'" if path.name == "index.html" and path.parent.name == "main" else ""
    return (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        f"script-src 'self' 'wasm-unsafe-eval'{eval_suffix}{handler_suffix}{hash_suffix}; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' blob:; worker-src 'self' blob:; frame-src 'self'; "
        "connect-src 'self' blob:; "
        f"frame-ancestors {frame_ancestors}"
    )


async def _send_error(send, status: int, error: str) -> None:
    body = json.dumps({"error": error}, separators=(",", ":")).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


def office_host_availability(request, pack: OfficeAssetPack) -> dict:
    client = request.client.host if request.client else ""
    if not is_loopback_host(client):
        return {"available": False, "reason": "local_client_required"}
    main_hostname = request.url.hostname or ""
    if not is_loopback_host(main_hostname) or request.url.port != request.app.state.owner_auth.port:
        return {"available": False, "reason": "local_main_origin_required"}
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed_origin = urlsplit(canonicalize_origin(origin))
        except (OwnerAuthError, ValueError):
            return {"available": False, "reason": "local_main_origin_required"}
        if not is_loopback_host(parsed_origin.hostname or "") or parsed_origin.port != request.app.state.owner_auth.port:
            return {"available": False, "reason": "local_main_origin_required"}
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
