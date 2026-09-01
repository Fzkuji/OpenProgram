"""Single-owner authentication and request policy for the Web server.

This is the server half: the live :class:`OwnerAuthState` (owner token,
cookie derivation, single-process lock, state files on disk) and the
:class:`OwnerAuthMiddleware` that authenticates every HTTP, SSE, and
WebSocket request before route dispatch, including the ``/api/auth``
challenge and cookie-bootstrap endpoints it serves itself.

Origin and token vocabulary comes from :mod:`openprogram.backend_endpoint`,
which clients also use to reach this server; the dependency runs one way.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Iterable, IO
from urllib.parse import parse_qsl

from openprogram import _compat as fcntl
from openprogram.backend_endpoint import (
    OwnerAuthError,
    _base64url_no_pad,
    _decode_token,
    canonicalize_bind_host,
    canonicalize_origin,
    create_owner_challenge_proof,
    is_loopback_host,
    resolve_effective_origins,
)

_INLINE_SCRIPT_RE = re.compile(
    rb"<script(?P<attributes>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PUBLIC_FRONTEND_ROUTES = frozenset(
    {
        "",
        "/",
        "/chat",
        "/chats",
        "/desktop-transfer-acceptance",
        "/functions",
        "/history",
        "/mcp",
        "/memory",
        "/plugin",
        "/plugins",
        "/programs",
        "/projects",
        "/scheduler",
        "/settings",
        "/skills",
    }
)
_PUBLIC_STATIC_FILES = frozenset(
    {
        "/favicon.ico",
        "/icon.svg",
        "/manifest.webmanifest",
    }
)
_PUBLIC_STATIC_PREFIXES = (
    "/_next/",
    "/html/",
    "/icons/",
    "/images/",
    "/menu-overlay/",
    "/settings/",
)


def _write_private_text(
    path: Path, content: str, *, expected_revision: str | None = None
):
    if path.name == "token":
        from openprogram.auth.credentials import _private_atomic_write

        return _private_atomic_write(
            path,
            lambda handle: handle.write(content.encode("ascii")),
            root=path.parent.parent,
            expected_revision=expected_revision,
        )

    # Non-secret Web access metadata retains its existing lifecycle.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        from openprogram._compat import restrict_to_user

        restrict_to_user(path)
        if path.read_text(encoding="ascii") != content:
            path.unlink(missing_ok=True)
            raise OwnerAuthError("private Web state read-back failed")
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@dataclass
class OwnerAuthState:
    _token_bytes: bytes = field(repr=False)
    owner_principal_id: str
    bind_host: str
    port: int
    effective_origins: frozenset[str]
    token_path: Path | None = None
    access_snapshot_path: Path | None = None
    _lock_handle: IO[str] | None = field(default=None, repr=False)

    @classmethod
    def from_raw_token(
        cls,
        raw_token: bytes,
        *,
        owner_principal_id: str,
        bind_host: str,
        port: int,
        allowed_origins: Iterable[str],
    ) -> "OwnerAuthState":
        if len(raw_token) != 32:
            raise OwnerAuthError("Web token must be exactly 32 bytes")
        if not re.fullmatch(r"owner/install/[0-9a-f]{16}", owner_principal_id):
            raise OwnerAuthError("owner principal ID is invalid")
        normalized_bind_host = canonicalize_bind_host(bind_host)
        return cls(
            _token_bytes=bytes(raw_token),
            owner_principal_id=owner_principal_id,
            bind_host=normalized_bind_host,
            port=int(port),
            effective_origins=resolve_effective_origins(
                normalized_bind_host, int(port), allowed_origins
            ),
        )

    @classmethod
    def start(
        cls,
        *,
        state_dir: Path,
        bind_host: str,
        port: int,
        allowed_origins: Iterable[str],
        raw_token: bytes | None = None,
        owner_principal_id: str | None = None,
    ) -> "OwnerAuthState":
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / "web.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        from openprogram._compat import restrict_to_user

        try:
            restrict_to_user(lock_path)
        except Exception:
            os.close(fd)
            raise
        handle = os.fdopen(fd, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise OwnerAuthError("another Web process is already active") from exc

        try:
            if owner_principal_id is None:
                from openprogram.agent.authority import (
                    owner_principal_id as get_owner_principal_id,
                )

                owner_principal_id = get_owner_principal_id()
            state = cls.from_raw_token(
                raw_token if raw_token is not None else secrets.token_bytes(32),
                owner_principal_id=owner_principal_id,
                bind_host=bind_host,
                port=port,
                allowed_origins=allowed_origins,
            )
            state.token_path = root / "web" / "token"
            state.access_snapshot_path = root / "web" / "access.json"
            state._lock_handle = handle
            _write_private_text(state.token_path, state.token)
            _write_private_text(
                state.access_snapshot_path,
                json.dumps(
                    {
                        "version": 1,
                        "bind_host": state.bind_host,
                        "port": state.port,
                        "effective_origins": sorted(state.effective_origins),
                        "token_fingerprint": state.fingerprint,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            return state
        except Exception as exc:
            from openprogram.auth.credentials import PrivateAtomicWriteError

            if isinstance(exc, PrivateAtomicWriteError) and exc.committed:
                if "state" in locals() and state._lock_handle is handle:
                    state._lock_handle = None
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
                raise
            if "state" in locals() and state._lock_handle is handle:
                state.close()
            else:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            raise

    @property
    def token(self) -> str:
        return _base64url_no_pad(self._token_bytes)

    @property
    def fingerprint(self) -> str:
        return f"sha256:{hashlib.sha256(self._token_bytes).hexdigest()[:12]}"

    @property
    def cookie_name(self) -> str:
        installation_id = self.owner_principal_id.rsplit("/", 1)[-1]
        return f"openprogram_owner_{installation_id}"

    @property
    def cookie_value(self) -> str:
        return _base64url_no_pad(
            hmac.digest(
                self._token_bytes,
                b"openprogram-web-cookie-v1",
                "sha256",
            )
        )

    @property
    def authority(self) -> dict[str, str]:
        from openprogram.agent.authority import owner_authority

        return owner_authority(self.owner_principal_id)

    def verify_token(self, value: str) -> bool:
        raw = _decode_token(value)
        return raw is not None and hmac.compare_digest(raw, self._token_bytes)

    def close(self) -> None:
        if self._lock_handle is None:
            return
        if self.token_path is not None:
            try:
                if self.token_path.read_text(encoding="ascii") == self.token:
                    self.token_path.unlink()
            except OSError:
                pass
        if self.access_snapshot_path is not None:
            try:
                payload = json.loads(
                    self.access_snapshot_path.read_text(encoding="ascii")
                )
                if payload.get("token_fingerprint") == self.fingerprint:
                    self.access_snapshot_path.unlink()
            except (OSError, ValueError, AttributeError):
                pass
        handle, self._lock_handle = self._lock_handle, None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _headers(scope) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in scope.get("headers") or []:
        result.setdefault(key.decode("latin-1").lower(), []).append(
            value.decode("latin-1")
        )
    return result


def _one_header(headers: dict[str, list[str]], name: str) -> str | None:
    values = headers.get(name, [])
    if len(values) > 1 or (values and "," in values[0]):
        raise OwnerAuthError(f"invalid {name} header")
    return values[0] if values else None


def _peer_is_loopback(scope) -> bool:
    client = scope.get("client")
    return bool(client and is_loopback_host(str(client[0])))


def _request_origin(
    scope,
    headers: dict[str, list[str]],
    state: OwnerAuthState,
) -> tuple[str, str | None]:
    host = _one_header(headers, "host")
    if not host:
        raise OwnerAuthError("missing Host header")
    scheme = str(scope.get("scheme") or "http").lower()
    if _peer_is_loopback(scope):
        forwarded = _one_header(headers, "x-forwarded-proto")
    else:
        forwarded = None
    if forwarded is not None:
        if forwarded.lower() not in {"http", "https"}:
            raise OwnerAuthError("invalid forwarded scheme")
        scheme = forwarded.lower()
    browser_scheme = "https" if scheme in {"https", "wss"} else "http"
    request_origin = canonicalize_origin(f"{browser_scheme}://{host}")
    if request_origin not in state.effective_origins:
        raise OwnerAuthError("request origin is not allowed")

    origin = _one_header(headers, "origin")
    if origin is not None:
        if origin.strip().lower() == "null":
            raise OwnerAuthError("opaque Origin")
        canonical_origin = canonicalize_origin(origin)
        if canonical_origin != request_origin:
            # A browser always sends the Origin that matches the Host it
            # dialled, so a mismatch there is CSRF and stays fatal. Native
            # clients (the Node TUI) reach the loopback listener by IP but
            # present the canonical effective Origin, which is a different
            # spelling of the same server. Tolerate that only for a bearer
            # request: bearer is a stronger credential than Origin, and it
            # is never attached ambiently the way a cookie is, so no CSRF
            # reasoning depends on it. Cookie requests keep the exact match
            # enforced below.
            bearer_provided, bearer_token = _bearer_token(headers)
            if not (
                bearer_provided
                and canonical_origin in state.effective_origins
                and state.verify_token(bearer_token)
            ):
                raise OwnerAuthError("Origin does not match request origin")
    if (_one_header(headers, "sec-fetch-site") or "").lower() == "cross-site":
        raise OwnerAuthError("cross-site request")
    return request_origin, origin


def _cookie_value(headers: dict[str, list[str]], name: str) -> str | None:
    raw = _one_header(headers, "cookie")
    if raw is None:
        return None
    try:
        cookie = SimpleCookie(raw)
    except Exception:
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel is not None else None


def _bearer_token(headers: dict[str, list[str]]) -> tuple[bool, str]:
    value = _one_header(headers, "authorization")
    if value is None:
        return False, ""
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return True, ""
    return True, token


def _public_shell(scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") not in {"GET", "HEAD"}:
        return False
    path = str(scope.get("path") or "/")
    route = path.removesuffix(".html").removesuffix(".txt")
    return (
        route in _PUBLIC_FRONTEND_ROUTES
        or path in _PUBLIC_STATIC_FILES
        or path.startswith(_PUBLIC_STATIC_PREFIXES)
        or path == "/docs"
        or path.startswith("/docs/")
    )


def _json_body(error: str) -> bytes:
    return json.dumps({"error": error}, separators=(",", ":")).encode("utf-8")


async def _http_response(send, status: int, error: str) -> None:
    body = _json_body(error)
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="OpenProgram"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _websocket_response(send, status: int, error: str, scope) -> None:
    if "websocket.http.response" in (scope.get("extensions") or {}):
        body = _json_body(error)
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="OpenProgram"'))
        await send(
            {
                "type": "websocket.http.response.start",
                "status": status,
                "headers": headers,
            }
        )
        await send({"type": "websocket.http.response.body", "body": body})
    else:
        await send({"type": "websocket.close", "code": 1008})


def _replace_header(
    headers: list[tuple[bytes, bytes]], name: bytes, value: bytes
) -> None:
    lowered = name.lower()
    headers[:] = [(key, val) for key, val in headers if key.lower() != lowered]
    headers.append((name, value))


def _shell_content_security_policy(
    body: bytes = b"", *, frame_ancestors: str = "'none'"
) -> bytes:
    hashes = []
    for match in _INLINE_SCRIPT_RE.finditer(body):
        if re.search(rb"\bsrc\s*=", match.group("attributes"), re.IGNORECASE):
            continue
        digest = base64.b64encode(hashlib.sha256(match.group("body")).digest()).decode(
            "ascii"
        )
        hashes.append(f"'sha256-{digest}'")
    script_sources = " ".join(("'self'", *hashes))
    return (
        f"object-src 'none'; base-uri 'none'; frame-ancestors {frame_ancestors}; "
        f"script-src {script_sources}; connect-src 'self'"
    ).encode("ascii")


def _response_sender(send, *, no_store: bool, shell: bool, frameable: bool = False):
    pending_start = None
    html_body = bytearray()

    async def wrapped(message):
        nonlocal pending_start
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers") or [])
            if no_store:
                _replace_header(headers, b"cache-control", b"no-store")
            if shell:
                _replace_header(
                    headers,
                    b"x-frame-options",
                    b"SAMEORIGIN" if frameable else b"DENY",
                )
                _replace_header(headers, b"referrer-policy", b"no-referrer")
                _replace_header(headers, b"x-content-type-options", b"nosniff")
                content_type = next(
                    (
                        value.lower()
                        for name, value in headers
                        if name.lower() == b"content-type"
                    ),
                    b"",
                )
                if content_type.startswith(b"text/html"):
                    pending_start = {**message, "headers": headers}
                    return
                _replace_header(
                    headers,
                    b"content-security-policy",
                    _shell_content_security_policy(
                        frame_ancestors="'self'" if frameable else "'none'"
                    ),
                )
            message = {**message, "headers": headers}
        elif pending_start is not None and message.get("type") == "http.response.body":
            html_body.extend(message.get("body") or b"")
            if message.get("more_body"):
                return
            headers = list(pending_start.get("headers") or [])
            _replace_header(
                headers,
                b"content-security-policy",
                _shell_content_security_policy(
                    bytes(html_body),
                    frame_ancestors="'self'" if frameable else "'none'",
                ),
            )
            await send({**pending_start, "headers": headers})
            pending_start = None
            message = {**message, "body": bytes(html_body)}
        await send(message)

    return wrapped


class OwnerAuthMiddleware:
    """Authenticate HTTP, SSE, and WebSocket before route dispatch."""

    def __init__(self, app, auth_state: OwnerAuthState) -> None:
        self.app = app
        self.auth_state = auth_state

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        try:
            request_origin, origin = _request_origin(scope, headers, self.auth_state)
        except OwnerAuthError:
            if scope["type"] == "websocket":
                await _websocket_response(send, 403, "request_origin_rejected", scope)
            else:
                await _http_response(send, 403, "request_origin_rejected")
            return

        path = str(scope.get("path") or "")
        if scope["type"] == "http" and path == "/api/auth/challenge":
            await self._challenge(scope, send)
            return
        if scope["type"] == "http" and path == "/api/auth/bootstrap":
            await self._bootstrap(scope, receive, send, headers, request_origin, origin)
            return

        public = scope["type"] == "http" and (
            path == "/healthz" or _public_shell(scope)
        )
        if public:
            await self.app(
                scope,
                receive,
                _response_sender(
                    send,
                    no_store=path == "/healthz",
                    shell=path != "/healthz",
                    frameable=path.startswith("/docs/") and path.endswith(".raw.html"),
                ),
            )
            return

        try:
            bearer_provided, bearer_token = _bearer_token(headers)
        except OwnerAuthError:
            bearer_provided, bearer_token = True, ""
        try:
            cookie = _cookie_value(headers, self.auth_state.cookie_name)
        except OwnerAuthError:
            cookie = None
        cookie_authenticated = cookie is not None and hmac.compare_digest(
            cookie, self.auth_state.cookie_value
        )
        bearer_authenticated = bearer_provided and self.auth_state.verify_token(
            bearer_token
        )
        if (bearer_provided and not bearer_authenticated) or (
            not bearer_provided and not cookie_authenticated
        ):
            if scope["type"] == "websocket":
                await _websocket_response(send, 401, "authentication_required", scope)
            else:
                await _http_response(send, 401, "authentication_required")
            return

        using_cookie = not bearer_provided
        origin_required = scope["type"] == "websocket" or (
            scope.get("method") not in _SAFE_METHODS
        )
        if using_cookie and origin_required and origin is None:
            if scope["type"] == "websocket":
                await _websocket_response(send, 403, "request_origin_rejected", scope)
            else:
                await _http_response(send, 403, "request_origin_rejected")
            return

        scope.setdefault("state", {})["authority"] = dict(self.auth_state.authority)
        await self.app(
            scope,
            receive,
            _response_sender(send, no_store=scope["type"] == "http", shell=False),
        )

    async def _challenge(self, scope, send) -> None:
        failed = scope.get("method") != "GET"
        try:
            pairs = parse_qsl(
                (scope.get("query_string") or b"").decode("ascii"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=2,
            )
        except (UnicodeDecodeError, ValueError):
            pairs = []
            failed = True
        values: dict[str, str] = {}
        for key, value in pairs:
            if key in values:
                failed = True
            values[key] = value
        if set(values) not in ({"nonce"}, {"nonce", "revision"}):
            failed = True
        revision = values.get("revision", "")
        if revision:
            from openprogram.webui.routes.misc import _head_sha

            if not hmac.compare_digest(revision, _head_sha()):
                failed = True
        try:
            proof = create_owner_challenge_proof(
                token=self.auth_state.token,
                nonce=values.get("nonce", ""),
                revision=revision,
            )
        except OwnerAuthError:
            failed = True
            proof = ""
        if failed:
            await _http_response(send, 400, "invalid_challenge")
            return
        body = json.dumps(
            {"proof": proof},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _bootstrap(
        self,
        scope,
        receive,
        send,
        headers: dict[str, list[str]],
        request_origin: str,
        origin: str | None,
    ) -> None:
        failed = False
        if scope.get("method") != "POST" or origin is None:
            failed = True
        try:
            if _one_header(headers, "authorization") is not None:
                failed = True
            content_type = (_one_header(headers, "content-type") or "").lower()
            if content_type != "application/json":
                failed = True
        except OwnerAuthError:
            failed = True

        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                failed = True
                break
            chunk = message.get("body") or b""
            if len(body) + len(chunk) > 256:
                failed = True
            elif not failed:
                body.extend(chunk)
            if not message.get("more_body"):
                break

        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            payload = json.loads(bytes(body), object_pairs_hook=pairs)
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict) or set(payload) != {"token"}:
            failed = True
        token = payload.get("token") if isinstance(payload, dict) else ""
        if not self.auth_state.verify_token(token):
            failed = True
        if failed:
            await _http_response(send, 401, "authentication_required")
            return

        secure = request_origin.startswith("https://")
        cookie = (
            f"{self.auth_state.cookie_name}={self.auth_state.cookie_value}; Path=/; "
            "HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")
        ).encode("ascii")
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"set-cookie", cookie),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})


__all__ = [
    "OwnerAuthMiddleware",
    "OwnerAuthState",
]
