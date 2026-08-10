"""Reaching the active Web backend from a client process.

Everything here runs in a process that *connects to* the Web server — the
MCP CLI, the TUI, the ``op web`` commands, the port probes. It reads the
state files the running server left behind, verifies the listener with a
nonce/HMAC challenge, and hands back a :class:`BackendEndpoint`.

The origin and token vocabulary lives here too, because both directions
need it: a client validates what it reads off disk with the same rules the
server used when it wrote it. The dependency runs one way — this module
never imports :mod:`openprogram.webui`, while the server imports from here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
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


@dataclass(frozen=True)
class ActiveWebAccess:
    """Non-secret network policy snapshot for the active Web process."""

    bind_host: str
    port: int
    effective_origins: frozenset[str]
    token_fingerprint: str


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

    Every URL field derives from one canonical Origin, so the Host a
    client dials and the Origin it declares are always the same spelling
    of the same server. Storing them independently once let the two drift
    ("localhost" Origin against a "127.0.0.1" Host), which the owner-auth
    middleware correctly refused as a cross-origin request.
    """

    origin: str
    token: str = field(repr=False)

    @property
    def _parts(self) -> tuple[str, str, int]:
        parsed = urlsplit(self.origin)
        return parsed.scheme, parsed.netloc, int(parsed.port or 0)

    @property
    def scheme(self) -> str:
        return self._parts[0]

    @property
    def host(self) -> str:
        """Authority (host[:port]) — exactly what goes in the Host header."""
        return self._parts[1]

    @property
    def port(self) -> int:
        return self._parts[2]

    @property
    def base_url(self) -> str:
        return self.origin

    @property
    def websocket_url(self) -> str:
        scheme, netloc, _ = self._parts
        return f"{'wss' if scheme == 'https' else 'ws'}://{netloc}/ws"

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
        # Bracketed, like every other IPv6 literal here: the result goes
        # straight into a URL authority, and bare "::1:18100" parses as a
        # malformed host rather than host + port.
        return "[::1]" if address.version == 6 else "127.0.0.1"
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
    origin = select_request_origin(active_access)
    if not backend_accepts_owner_challenge(active_access.port, origin=origin):
        raise OwnerAuthError("active Web port is not owned by this profile")
    token = read_web_token(state_dir)
    # The snapshot and the token are two files; a restart between the two
    # reads would pair an old policy with a new credential. Re-read the
    # snapshot and require it to be the same state we just challenged, so
    # a rotation during this window fails instead of half-applying.
    if read_active_web_access(state_dir) != active_access:
        raise OwnerAuthError("active Web state changed while resolving")
    return BackendEndpoint(origin=origin, token=token)


__all__ = [
    "ActiveWebAccess",
    "BackendEndpoint",
    "OwnerAuthError",
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
