"""Single-owner authentication and request policy for the Web surface."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Iterable, IO
from urllib.parse import parse_qsl, urlsplit

from openprogram import _compat as fcntl


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_INLINE_SCRIPT_RE = re.compile(
    rb"<script(?P<attributes>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PUBLIC_FRONTEND_ROUTES = frozenset({
    "",
    "/",
    "/chat",
    "/chats",
    "/desktop-transfer-acceptance",
    "/functions",
    "/mcp",
    "/memory",
    "/plugin",
    "/plugins",
    "/programs",
    "/projects",
    "/settings",
    "/skills",
})
_PUBLIC_STATIC_FILES = frozenset({
    "/favicon.ico",
    "/icon.svg",
    "/manifest.webmanifest",
})
_PUBLIC_STATIC_PREFIXES = (
    "/_next/",
    "/html/",
    "/icons/",
    "/images/",
    "/menu-overlay/",
    "/settings/",
)
_PLAINTEXT_HTTP_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class OwnerAuthError(RuntimeError):
    pass


def _base64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_token(value: str) -> bytes | None:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        return None
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None
    return raw if len(raw) == 32 else None


def is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().lower().strip("[]")
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _http_ip_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address in network for network in _PLAINTEXT_HTTP_NETWORKS)


def canonicalize_bind_host(value: str) -> str:
    """Validate and canonicalize a Uvicorn bind hostname or IP literal."""
    raw = str(value or "").strip()
    if (
        not raw
        or "\\" in raw
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in raw)
    ):
        raise OwnerAuthError("invalid Web bind host")
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        if any(character in raw for character in ":/[]@?#"):
            raise OwnerAuthError("Web bind host must not include a port or URL syntax")
        try:
            hostname = raw.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise OwnerAuthError("invalid Web bind host") from exc
        if (
            len(hostname) > 253
            or any(
                not _DNS_LABEL_RE.fullmatch(label)
                for label in hostname.split(".")
            )
        ):
            raise OwnerAuthError("invalid Web bind host")
        return hostname


def canonicalize_origin(value: str) -> str:
    """Validate and canonicalize one configured or request origin."""
    raw = str(value or "").strip()
    if (
        not raw
        or raw == "null"
        or "*" in raw
        or "\\" in raw
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw)
    ):
        raise OwnerAuthError("invalid origin")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise OwnerAuthError("invalid origin") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise OwnerAuthError("origin scheme must be http or https")
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise OwnerAuthError("origin must contain only scheme and authority")
    host = parsed.hostname
    if not host or "%" in host:
        raise OwnerAuthError("invalid origin host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            canonical_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise OwnerAuthError("invalid origin host") from exc
        labels = canonical_host.split(".")
        if (
            not canonical_host
            or len(canonical_host) > 253
            or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
        ):
            raise OwnerAuthError("invalid origin host")
        if scheme == "http" and canonical_host != "localhost":
            raise OwnerAuthError("HTTP DNS origins are not allowed")
    else:
        if address.is_unspecified or address.is_multicast:
            raise OwnerAuthError("unspecified or multicast origin")
        if scheme == "http" and not _http_ip_allowed(address):
            raise OwnerAuthError("HTTP origin is outside the local/overlay allowlist")
        canonical_host = f"[{address.compressed}]" if address.version == 6 else str(address)

    if port is None or (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    ):
        return f"{scheme}://{canonical_host}"
    return f"{scheme}://{canonical_host}:{port}"


def resolve_effective_origins(
    bind_host: str,
    port: int,
    allowed_origins: Iterable[str],
) -> frozenset[str]:
    configured = {canonicalize_origin(value) for value in allowed_origins}
    if is_loopback_host(bind_host):
        configured.add(canonicalize_origin(f"http://localhost:{port}"))
        literal = str(bind_host).strip().lower()
        if literal != "localhost":
            bracketed = f"[{literal.strip('[]')}]" if ":" in literal else literal
            configured.add(canonicalize_origin(f"http://{bracketed}:{port}"))
    elif not configured:
        raise OwnerAuthError("a non-loopback bind requires an allowed origin")
    return frozenset(configured)


def build_owner_auth_url(
    base_url: str,
    *,
    token: str,
    effective_origins: Iterable[str],
) -> str:
    """Build a fragment-bootstrap URL for one validated effective Origin."""
    origin = canonicalize_origin(base_url)
    if str(base_url).strip() != origin:
        raise OwnerAuthError("base URL must be a canonical Origin")
    if origin not in frozenset(effective_origins):
        raise OwnerAuthError("base URL is not an effective origin")
    if _decode_token(token) is None:
        raise OwnerAuthError("active Web token is invalid")
    return f"{origin}/#token={token}"


def create_owner_challenge_proof(
    *,
    token: str,
    nonce: str,
    revision: str = "",
) -> str:
    """Authenticate a client nonce without transmitting the owner token."""
    token_bytes = _decode_token(token)
    nonce_bytes = _decode_token(nonce)
    if token_bytes is None or nonce_bytes is None:
        raise OwnerAuthError("invalid owner challenge input")
    if revision and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise OwnerAuthError("invalid revision")
    message = (
        b"openprogram-web-challenge-v1\0"
        + nonce_bytes
        + b"\0"
        + revision.encode("ascii")
    )
    return _base64url_no_pad(hmac.digest(token_bytes, message, "sha256"))


def _write_private_text(path: Path, content: str) -> None:
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


@dataclass(frozen=True)
class ActiveWebAccess:
    """Non-secret network policy snapshot for the active Web process."""

    bind_host: str
    port: int
    effective_origins: frozenset[str]
    token_fingerprint: str


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
                from openprogram.agent.authority import owner_principal_id as get_owner_principal_id

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
        except Exception:
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


def read_web_token(state_dir: Path | None = None) -> str:
    if state_dir is None:
        from openprogram.paths import get_state_dir

        state_dir = Path(get_state_dir())
    path = Path(state_dir) / "web" / "token"
    try:
        token = path.read_text(encoding="ascii")
    except OSError as exc:
        raise OwnerAuthError("no active Web token") from exc
    if _decode_token(token) is None:
        raise OwnerAuthError("active Web token is invalid")
    return token


def read_active_web_access(state_dir: Path | None = None) -> ActiveWebAccess:
    """Read and validate the active process's frozen network policy."""
    if state_dir is None:
        from openprogram.paths import get_state_dir

        state_dir = Path(get_state_dir())
    path = Path(state_dir) / "web" / "access.json"
    try:
        payload = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError) as exc:
        raise OwnerAuthError("no active Web access snapshot") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "bind_host",
        "port",
        "effective_origins",
        "token_fingerprint",
    }:
        raise OwnerAuthError("active Web access snapshot is invalid")
    origins = payload.get("effective_origins")
    port = payload.get("port")
    if (
        payload.get("version") != 1
        or not isinstance(payload.get("bind_host"), str)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(origins, list)
        or not origins
        or not all(
            isinstance(origin, str) and canonicalize_origin(origin) == origin
            for origin in origins
        )
        or not isinstance(payload.get("token_fingerprint"), str)
    ):
        raise OwnerAuthError("active Web access snapshot is invalid")
    token = read_web_token(state_dir)
    raw_token = _decode_token(token)
    assert raw_token is not None
    expected_fingerprint = (
        f"sha256:{hashlib.sha256(raw_token).hexdigest()[:12]}"
    )
    if not hmac.compare_digest(
        payload["token_fingerprint"], expected_fingerprint
    ):
        raise OwnerAuthError("active Web state files do not match")
    return ActiveWebAccess(
        bind_host=payload["bind_host"],
        port=port,
        effective_origins=frozenset(origins),
        token_fingerprint=expected_fingerprint,
    )


@dataclass(frozen=True)
class BackendEndpoint:
    """Everything an internal client needs to reach the active Web server.

    Produced only by :func:`resolve_backend_endpoint`, which verifies the
    listener with the nonce/HMAC challenge *before* the owner token is read,
    so the credential is never handed to a stranger holding the port.
    """

    base_url: str
    websocket_url: str
    origin: str
    host: str
    scheme: str
    port: int
    token: str = field(repr=False)

    @property
    def authorization_header(self) -> str:
        return f"Bearer {self.token}"


def select_connect_host(bind_host: str) -> str:
    """URL-ready host an internal client dials for a given bind host.

    A wildcard bind is dialled on loopback; an IPv6 literal comes back
    bracketed so it can be pasted straight into a URL authority.
    """
    value = str(bind_host).strip().strip("[]")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if address.is_unspecified:
        return "::1" if address.version == 6 else "127.0.0.1"
    return f"[{address.compressed}]" if address.version == 6 else str(address)


def select_request_origin(active_access: ActiveWebAccess) -> str:
    """Pick the effective Origin an internal client should present."""
    preferred = (
        f"http://localhost:{active_access.port}",
        f"http://{select_connect_host(active_access.bind_host)}:{active_access.port}",
    )
    for origin in preferred:
        if origin in active_access.effective_origins:
            return origin
    return sorted(active_access.effective_origins)[0]


def resolve_backend_endpoint(state_dir: Path | None = None) -> BackendEndpoint:
    """Resolve the active backend endpoint, challenge-verified, with token.

    Order matters: the snapshot and the challenge come first, the token is
    read only once the listener has proven it holds the same token.
    """
    from openprogram._ports import backend_accepts_owner_challenge

    active_access = read_active_web_access(state_dir)
    if not backend_accepts_owner_challenge(active_access.port):
        raise OwnerAuthError("active Web port is not owned by this profile")
    origin = select_request_origin(active_access)
    parsed = urlsplit(origin)
    host = parsed.netloc
    scheme = parsed.scheme
    url_host = select_connect_host(active_access.bind_host)
    return BackendEndpoint(
        base_url=f"{scheme}://{url_host}:{active_access.port}",
        websocket_url=(
            f"{'wss' if scheme == 'https' else 'ws'}://"
            f"{url_host}:{active_access.port}/ws"
        ),
        origin=origin,
        host=host,
        scheme=scheme,
        port=active_access.port,
        token=read_web_token(state_dir),
    )


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
        if canonicalize_origin(origin) != request_origin:
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
        await send({
            "type": "websocket.http.response.start",
            "status": status,
            "headers": headers,
        })
        await send({"type": "websocket.http.response.body", "body": body})
    else:
        await send({"type": "websocket.close", "code": 1008})


def _replace_header(headers: list[tuple[bytes, bytes]], name: bytes, value: bytes) -> None:
    lowered = name.lower()
    headers[:] = [(key, val) for key, val in headers if key.lower() != lowered]
    headers.append((name, value))


def _shell_content_security_policy(body: bytes = b"") -> bytes:
    hashes = []
    for match in _INLINE_SCRIPT_RE.finditer(body):
        if re.search(rb"\bsrc\s*=", match.group("attributes"), re.IGNORECASE):
            continue
        digest = base64.b64encode(
            hashlib.sha256(match.group("body")).digest()
        ).decode("ascii")
        hashes.append(f"'sha256-{digest}'")
    script_sources = " ".join(("'self'", *hashes))
    return (
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        f"script-src {script_sources}; connect-src 'self'"
    ).encode("ascii")


def _response_sender(send, *, no_store: bool, shell: bool):
    pending_start = None
    html_body = bytearray()

    async def wrapped(message):
        nonlocal pending_start
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers") or [])
            if no_store:
                _replace_header(headers, b"cache-control", b"no-store")
            if shell:
                _replace_header(headers, b"x-frame-options", b"DENY")
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
                    _shell_content_security_policy(),
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
                _shell_content_security_policy(bytes(html_body)),
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
            request_origin, origin = _request_origin(
                scope, headers, self.auth_state
            )
        except OwnerAuthError:
            if scope["type"] == "websocket":
                await _websocket_response(
                    send, 403, "request_origin_rejected", scope
                )
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

        public = (
            scope["type"] == "http"
            and (path == "/healthz" or _public_shell(scope))
        )
        if public:
            await self.app(
                scope,
                receive,
                _response_sender(send, no_store=path == "/healthz", shell=path != "/healthz"),
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
        bearer_authenticated = (
            bearer_provided and self.auth_state.verify_token(bearer_token)
        )
        if (bearer_provided and not bearer_authenticated) or (
            not bearer_provided and not cookie_authenticated
        ):
            if scope["type"] == "websocket":
                await _websocket_response(
                    send, 401, "authentication_required", scope
                )
            else:
                await _http_response(send, 401, "authentication_required")
            return

        using_cookie = not bearer_provided
        origin_required = scope["type"] == "websocket" or (
            scope.get("method") not in _SAFE_METHODS
        )
        if using_cookie and origin_required and origin is None:
            if scope["type"] == "websocket":
                await _websocket_response(
                    send, 403, "request_origin_rejected", scope
                )
            else:
                await _http_response(send, 403, "request_origin_rejected")
            return

        scope.setdefault("state", {})["authority"] = dict(
            self.auth_state.authority
        )
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
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        })
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
            "HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else "")
        ).encode("ascii")
        await send({
            "type": "http.response.start",
            "status": 204,
            "headers": [
                (b"set-cookie", cookie),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": b""})


__all__ = [
    "ActiveWebAccess",
    "BackendEndpoint",
    "OwnerAuthMiddleware",
    "OwnerAuthError",
    "OwnerAuthState",
    "build_owner_auth_url",
    "canonicalize_bind_host",
    "canonicalize_origin",
    "create_owner_challenge_proof",
    "is_loopback_host",
    "read_active_web_access",
    "read_web_token",
    "resolve_backend_endpoint",
    "resolve_effective_origins",
    "select_connect_host",
    "select_request_origin",
]
