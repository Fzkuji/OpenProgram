"""Consumer declarations and peer-constrained Runtime HTTP clients."""

from __future__ import annotations

import ipaddress
import os
import re
import ssl
import tempfile
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from http import HTTPStatus
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Any, Iterator
from urllib.parse import urljoin

import httpcore
import httpx

from .url_policy import (
    OwnerURLException,
    Resolver,
    URLDecision,
    URLPolicyError,
    URLTrustClass,
    evaluate_url,
    normalize_origin,
    normalize_url,
    resolve_all,
)


_MAX_RESPONSE_HEADERS = 100
_MAX_ENCODED_HEADER_BYTES = 65_536
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 30.0
_WRITE_TIMEOUT = 30.0
_POOL_TIMEOUT = 5.0
_OVERALL_TIMEOUT = 120.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SUPPORTED_ENCODINGS = frozenset({"identity", "gzip", "deflate"})
_ASCII_DECIMAL = re.compile(r"[0-9]+", re.ASCII)
AUDIT_EVENT_CAPACITY = 256


class SDKDisposition(str, Enum):
    INJECTED_TRANSPORT = "injected_transport"
    EXACT_ORIGIN = "exact_origin"
    POLICY_PROXY = "policy_proxy"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ConsumerSpec:
    consumer: str
    trust_class: URLTrustClass
    allowed_schemes: frozenset[str]
    allowed_methods: frozenset[str]
    allowed_ports: frozenset[int] | None
    fixed_origins: frozenset[str]
    redirect_policy: str
    max_redirects: int
    max_decoded_body_bytes: int
    accepted_mime_prefixes: tuple[str, ...]
    credential_origin_policy: str
    credential_headers: frozenset[str]
    allow_owner_exceptions: bool
    sdk_disposition: SDKDisposition | None = None


_HTTP_SCHEMES = frozenset({"http", "https"})
_HTTPS_SCHEME = frozenset({"https"})
_PUBLIC_PORTS = frozenset({80, 443})
_READ_METHODS = frozenset({"GET", "HEAD"})
_API_METHODS = frozenset({"GET", "HEAD", "POST"})
_ANY_MIME = ("application/", "audio/", "image/", "text/", "video/")
_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key"}
)
_NO_FIXED_ORIGINS: frozenset[str] = frozenset()
_AUDITED_FIXED_ORIGINS = MappingProxyType(
    {
        "tool.web_search.fixed_api": frozenset(
            {
                "https://api.exa.ai",
                "https://api.firecrawl.dev",
                "https://api.minimax.io",
                "https://api.minimaxi.com",
                "https://api.moonshot.ai",
                "https://api.moonshot.cn",
                "https://api.perplexity.ai",
                "https://api.search.brave.com",
                "https://api.tavily.com",
                "https://chat-api.you.com",
                "https://export.arxiv.org",
                "https://google.serper.dev",
                "https://kagi.com",
                "https://ollama.com",
                "https://s.jina.ai",
                "https://www.googleapis.com",
            }
        ),
        "tool.image_api.fixed": frozenset(
            {
                "https://api.anthropic.com",
                "https://api.openai.com",
                "https://generativelanguage.googleapis.com",
                "https://queue.fal.run",
            }
        ),
        "channel.telegram.api": frozenset({"https://api.telegram.org"}),
        "channel.discord.api": frozenset({"https://discord.com"}),
        "channel.discord.gateway_sdk": frozenset({"https://discord.com"}),
        "channel.slack.api": frozenset({"https://slack.com"}),
        "channel.slack.gateway_sdk": frozenset({"https://slack.com"}),
        "channel.slack.attachment": frozenset(
            {"https://files.slack.com", "https://slack.com"}
        ),
        "channel.slack.generated_asset.upload": frozenset({"https://files.slack.com"}),
        "channel.telegram.attachment": frozenset({"https://api.telegram.org"}),
        "channel.feishu.api": frozenset(
            {"https://open.feishu.cn", "https://open.larksuite.com"}
        ),
        "skills.github.catalog": frozenset(
            {
                "https://clawhub.ai",
                "https://codeload.github.com",
                "https://github.com",
            }
        ),
        "plugins.autoupdate": frozenset(
            {"https://pypi.org", "https://registry.npmjs.org"}
        ),
        "updater.github": frozenset({"https://api.github.com"}),
        "provider.fixed_api": frozenset(
            {
                "https://ai-gateway.vercel.sh",
                "https://api.anthropic.com",
                "https://api.cerebras.ai",
                "https://api.deepseek.com",
                "https://api.github.com",
                "https://api.githubcopilot.com",
                "https://api.groq.com",
                "https://api.individual.githubcopilot.com",
                "https://api.kimi.com",
                "https://api.minimax.io",
                "https://api.minimaxi.com",
                "https://api.mistral.ai",
                "https://api.openai.com",
                "https://api.x.ai",
                "https://cli-chat-proxy.grok.com",
                "https://api.z.ai",
                "https://bedrock-runtime.us-east-1.amazonaws.com",
                "https://chatgpt.com",
                "https://cloudcode-pa.googleapis.com",
                "https://generativelanguage.googleapis.com",
                "https://opencode.ai",
                "https://openrouter.ai",
                "https://router.huggingface.co",
                "https://token-plan.cn-beijing.maas.aliyuncs.com",
            }
        ),
        "provider.amazon_bedrock.sdk": frozenset(
            {
                "https://bedrock.us-east-1.amazonaws.com",
                "https://bedrock-runtime.us-east-1.amazonaws.com",
            }
        ),
        "provider.oauth.fixed": frozenset(
            {
                "https://accounts.google.com",
                "https://api.github.com",
                "https://auth.openai.com",
                "https://auth.x.ai",
                "https://accounts.x.ai",
                "https://claude.ai",
                "https://console.anthropic.com",
                "https://github.com",
                "https://oauth2.googleapis.com",
            }
        ),
        "tts.fixed_api": frozenset(
            {"https://api.elevenlabs.io", "https://api.openai.com"}
        ),
        "webui.model_listing.fixed": frozenset(
            {
                "https://api.anthropic.com",
                "https://generativelanguage.googleapis.com",
                "https://models.dev",
            }
        ),
    }
)


def _download(consumer: str, trust_class: URLTrustClass) -> ConsumerSpec:
    configured = trust_class == URLTrustClass.CONFIGURED_SERVICE
    fixed = trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE
    return ConsumerSpec(
        consumer=consumer,
        trust_class=trust_class,
        allowed_schemes=_HTTPS_SCHEME if fixed else _HTTP_SCHEMES,
        allowed_methods=_READ_METHODS,
        allowed_ports=None if configured else _PUBLIC_PORTS,
        fixed_origins=(
            _AUDITED_FIXED_ORIGINS[consumer] if fixed else _NO_FIXED_ORIGINS
        ),
        redirect_policy="same_origin" if configured else "public",
        max_redirects=5,
        max_decoded_body_bytes=32 * 1024 * 1024,
        accepted_mime_prefixes=_ANY_MIME,
        credential_origin_policy="same_origin" if configured else "none",
        credential_headers=_CREDENTIAL_HEADERS,
        allow_owner_exceptions=configured,
    )


def _api(
    consumer: str,
    trust_class: URLTrustClass,
    *,
    sdk_disposition: SDKDisposition | None = None,
) -> ConsumerSpec:
    configured = trust_class == URLTrustClass.CONFIGURED_SERVICE
    fixed = trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE
    return ConsumerSpec(
        consumer=consumer,
        trust_class=trust_class,
        allowed_schemes=_HTTPS_SCHEME if fixed else _HTTP_SCHEMES,
        allowed_methods=_API_METHODS,
        allowed_ports=None if configured else _PUBLIC_PORTS,
        fixed_origins=(
            _AUDITED_FIXED_ORIGINS[consumer] if fixed else _NO_FIXED_ORIGINS
        ),
        redirect_policy="same_origin",
        max_redirects=5,
        max_decoded_body_bytes=16 * 1024 * 1024,
        accepted_mime_prefixes=("application/", "text/"),
        credential_origin_policy="same_origin",
        credential_headers=_CREDENTIAL_HEADERS,
        allow_owner_exceptions=configured,
        sdk_disposition=sdk_disposition,
    )


def _callback(consumer: str) -> ConsumerSpec:
    return ConsumerSpec(
        consumer=consumer,
        trust_class=URLTrustClass.LOOPBACK_CALLBACK,
        allowed_schemes=frozenset({"http"}),
        allowed_methods=_READ_METHODS,
        allowed_ports=None,
        fixed_origins=_NO_FIXED_ORIGINS,
        redirect_policy="deny",
        max_redirects=1,
        max_decoded_body_bytes=1024 * 1024,
        accepted_mime_prefixes=("application/", "text/"),
        credential_origin_policy="none",
        credential_headers=_CREDENTIAL_HEADERS,
        allow_owner_exceptions=False,
    )


_SPECS = (
    replace(
        _download("tool.web_fetch", URLTrustClass.UNTRUSTED_PUBLIC),
        max_decoded_body_bytes=5 * 1024 * 1024 + 1,
    ),
    _api("tool.web_search.fixed_api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("tool.web_search.configured_api", URLTrustClass.CONFIGURED_SERVICE),
    _api("tool.image_api.fixed", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("tool.image_api.configured", URLTrustClass.CONFIGURED_SERVICE),
    replace(
        _download("tool.image_result.download", URLTrustClass.UNTRUSTED_PUBLIC),
        accepted_mime_prefixes=("image/", "application/octet-stream"),
    ),
    replace(
        _download("channel.attachment.download", URLTrustClass.UNTRUSTED_PUBLIC),
        max_decoded_body_bytes=20 * 1024 * 1024,
    ),
    _api(
        "channel.telegram.api",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.EXACT_ORIGIN,
    ),
    replace(
        _api(
            "channel.discord.api",
            URLTrustClass.FIXED_PUBLIC_SERVICE,
            sdk_disposition=SDKDisposition.EXACT_ORIGIN,
        ),
        allowed_methods=_API_METHODS | {"PATCH"},
    ),
    _api(
        "channel.discord.gateway_sdk",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.DISABLED,
    ),
    _api(
        "channel.slack.api",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.EXACT_ORIGIN,
    ),
    _api(
        "channel.slack.gateway_sdk",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.DISABLED,
    ),
    replace(
        _download("channel.slack.attachment", URLTrustClass.FIXED_PUBLIC_SERVICE),
        redirect_policy="same_origin",
        max_decoded_body_bytes=20 * 1024 * 1024,
        credential_origin_policy="same_origin",
    ),
    replace(
        _api(
            "channel.slack.generated_asset.upload",
            URLTrustClass.FIXED_PUBLIC_SERVICE,
        ),
        allowed_methods=frozenset({"POST"}),
        credential_origin_policy="none",
    ),
    replace(
        _download("channel.telegram.attachment", URLTrustClass.FIXED_PUBLIC_SERVICE),
        redirect_policy="same_origin",
        max_decoded_body_bytes=20 * 1024 * 1024,
    ),
    _api(
        "channel.wechat.api",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.EXACT_ORIGIN,
    ),
    _api(
        "channel.feishu.api",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.EXACT_ORIGIN,
    ),
    _api(
        "channel.matrix.configured",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.EXACT_ORIGIN,
    ),
    _download("channel.generated_asset.download", URLTrustClass.UNTRUSTED_PUBLIC),
    _download("skills.github.catalog", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("skills.configured.catalog", URLTrustClass.CONFIGURED_SERVICE),
    _download("plugins.marketplace", URLTrustClass.CONFIGURED_SERVICE),
    _download("plugins.autoupdate", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("updater.github", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("provider.fixed_api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("provider.configured_api", URLTrustClass.CONFIGURED_SERVICE),
    _api("provider.oauth.fixed", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api(
        "provider.google.sdk",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.INJECTED_TRANSPORT,
    ),
    _api(
        "provider.openai.sdk",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.INJECTED_TRANSPORT,
    ),
    _api(
        "provider.anthropic.sdk",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.INJECTED_TRANSPORT,
    ),
    _api(
        "provider.amazon_bedrock.sdk",
        URLTrustClass.FIXED_PUBLIC_SERVICE,
        sdk_disposition=SDKDisposition.DISABLED,
    ),
    _api(
        "mcp.configured.http",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.INJECTED_TRANSPORT,
    ),
    _api(
        "mcp.configured.sse",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.INJECTED_TRANSPORT,
    ),
    _callback("mcp.loopback.callback"),
    replace(
        _api(
            "tts.fixed_api",
            URLTrustClass.FIXED_PUBLIC_SERVICE,
            sdk_disposition=SDKDisposition.EXACT_ORIGIN,
        ),
        accepted_mime_prefixes=("audio/", "application/octet-stream"),
    ),
    replace(
        _api(
            "tts.configured_api",
            URLTrustClass.CONFIGURED_SERVICE,
            sdk_disposition=SDKDisposition.EXACT_ORIGIN,
        ),
        accepted_mime_prefixes=("audio/", "application/octet-stream"),
    ),
    _api(
        "tts.edge_sdk",
        URLTrustClass.CONFIGURED_SERVICE,
        sdk_disposition=SDKDisposition.DISABLED,
    ),
    _api("webui.mcp.catalog", URLTrustClass.CONFIGURED_SERVICE),
    _api("webui.model_listing.fixed", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("webui.model_listing.configured", URLTrustClass.CONFIGURED_SERVICE),
    _api("runtime.local_probe", URLTrustClass.CONFIGURED_SERVICE),
)

if len({spec.consumer for spec in _SPECS}) != len(_SPECS):
    raise RuntimeError("duplicate safe HTTP consumer key")
if any(
    bool(spec.fixed_origins) != (spec.trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE)
    for spec in _SPECS
):
    raise RuntimeError("fixed service must declare audited origins")

CONSUMER_REGISTRY = MappingProxyType({spec.consumer: spec for spec in _SPECS})


def require_active_sdk_transport(consumer: str, origin: str) -> None:
    """Fail closed before starting an SDK whose network path is unmanaged."""
    spec = CONSUMER_REGISTRY[consumer]
    if spec.sdk_disposition is SDKDisposition.DISABLED:
        raise URLPolicyError("UNMANAGED_TRANSPORT", normalize_origin(origin))


_HTTPCORE_EXCEPTIONS: dict[type[Exception], type[httpx.HTTPError]] = {
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.ProxyError: httpx.ProxyError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
}


@contextmanager
def _map_httpcore_exceptions() -> Iterator[None]:
    try:
        yield
    except URLPolicyError:
        raise
    except Exception as exc:
        mapped_type = None
        for core_type, httpx_type in _HTTPCORE_EXCEPTIONS.items():
            if isinstance(exc, core_type) and (
                mapped_type is None or issubclass(httpx_type, mapped_type)
            ):
                mapped_type = httpx_type
        if mapped_type is None:
            raise
        raise mapped_type(str(exc)) from exc


@dataclass(frozen=True)
class PolicyProxyConfig:
    url: str
    enforces_target_policy: bool


@dataclass(frozen=True)
class AuditEvent:
    consumer: str
    reason: str
    safe_origin: str
    delegated_to_policy_proxy: bool
    timestamp: str


@dataclass(frozen=True)
class OutboundSecurityConfig:
    resolver: Resolver = resolve_all
    owner_exceptions: tuple[OwnerURLException, ...] = ()
    ca_bundle: str | None = None
    retries: int = 0
    local_address: str | None = None
    socket_options: tuple[tuple[int, int, int], ...] = ()
    policy_proxy_identity: str | None = None
    policy_proxy: PolicyProxyConfig | None = None

    def __post_init__(self) -> None:
        if self.ca_bundle is not None and not isinstance(self.ca_bundle, str):
            raise TypeError("ca_bundle must be a CA bundle path")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.local_address not in (None, "0.0.0.0"):
            raise ValueError("local_address must be the IPv4 wildcard or None")
        if (
            self.policy_proxy is not None
            and not self.policy_proxy.enforces_target_policy
        ):
            try:
                safe_origin = normalize_origin(self.policy_proxy.url)
            except URLPolicyError as exc:
                safe_origin = exc.safe_url
            raise URLPolicyError("POLICY_PROXY_ENFORCEMENT_REQUIRED", safe_origin)


class SafeHTTPStatusError(RuntimeError):
    """HTTP status failure without request paths, queries, or peer text."""

    def __init__(self, status_code: int, origin: str):
        self.status_code = status_code
        self.origin = origin
        try:
            reason = HTTPStatus(status_code).phrase
        except ValueError:
            reason = ""
        super().__init__(
            f"HTTP {status_code}{f' {reason}' if reason else ''} for {origin}"
        )


def _canonical_peer(stream: Any, decision: URLDecision):
    server_addr = stream.get_extra_info("server_addr")
    try:
        value = server_addr[0]
        peer = ipaddress.ip_address(value)
    except (TypeError, ValueError, IndexError) as exc:
        raise URLPolicyError("PEER_ADDRESS_MISMATCH", decision.origin) from exc
    if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
        peer = peer.ipv4_mapped
    if peer not in decision.resolved_ips:
        raise URLPolicyError("PEER_ADDRESS_MISMATCH", decision.origin)
    return peer


class _DecisionNetworkStream(httpcore.NetworkStream):
    def __init__(self, stream: httpcore.NetworkStream, decision: URLDecision):
        self._stream = stream
        self._decision = decision
        try:
            _canonical_peer(stream, decision)
        except URLPolicyError:
            stream.close()
            raise

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        stream = self._stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=timeout
        )
        return _DecisionNetworkStream(stream, self._decision)

    def get_extra_info(self, info: str):
        value = self._stream.get_extra_info(info)
        if info == "server_addr":
            _canonical_peer(self._stream, self._decision)
        return value


class _AsyncDecisionNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream: httpcore.AsyncNetworkStream, decision: URLDecision):
        self._stream = stream
        self._decision = decision
        _canonical_peer(stream, decision)

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer, timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await self._stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=timeout
        )
        try:
            return _AsyncDecisionNetworkStream(stream, self._decision)
        except URLPolicyError:
            await stream.aclose()
            raise

    def get_extra_info(self, info: str):
        value = self._stream.get_extra_info(info)
        if info == "server_addr":
            _canonical_peer(self._stream, self._decision)
        return value


class DecisionNetworkBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        decision: URLDecision,
        *,
        underlying: httpcore.NetworkBackend | None = None,
    ):
        self._decision = decision
        self._underlying = underlying or httpcore.SyncBackend()
        self._next_address = 0
        self._lock = threading.Lock()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        if host != self._decision.hostname or port != self._decision.port:
            raise URLPolicyError("DECISION_TARGET_MISMATCH", self._decision.origin)
        with self._lock:
            address = self._decision.resolved_ips[
                self._next_address % len(self._decision.resolved_ips)
            ]
            self._next_address += 1
        stream = self._underlying.connect_tcp(
            str(address),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return _DecisionNetworkStream(stream, self._decision)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise URLPolicyError("UNIX_SOCKET_FORBIDDEN", self._decision.origin)

    def sleep(self, seconds: float) -> None:
        self._underlying.sleep(seconds)


class AsyncDecisionNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        decision: URLDecision,
        *,
        underlying: httpcore.AsyncNetworkBackend | None = None,
    ):
        self._decision = decision
        self._underlying = underlying or httpcore.AnyIOBackend()
        self._next_address = 0

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._decision.hostname or port != self._decision.port:
            raise URLPolicyError("DECISION_TARGET_MISMATCH", self._decision.origin)
        address = self._decision.resolved_ips[
            self._next_address % len(self._decision.resolved_ips)
        ]
        self._next_address += 1
        stream = await self._underlying.connect_tcp(
            str(address),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        try:
            return _AsyncDecisionNetworkStream(stream, self._decision)
        except URLPolicyError:
            await stream.aclose()
            raise

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise URLPolicyError("UNIX_SOCKET_FORBIDDEN", self._decision.origin)

    async def sleep(self, seconds: float) -> None:
        await self._underlying.sleep(seconds)


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream, close_pool=None):
        self._stream = stream
        self._close_pool = close_pool
        self._closed = False

    def __iter__(self):
        try:
            with _map_httpcore_exceptions():
                yield from self._stream
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with _map_httpcore_exceptions():
                self._stream.close()
        finally:
            if self._close_pool is not None:
                self._close_pool()


class _AsyncResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream, close_pool=None):
        self._stream = stream
        self._close_pool = close_pool
        self._closed = False

    async def __aiter__(self):
        try:
            with _map_httpcore_exceptions():
                async for chunk in self._stream:
                    yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with _map_httpcore_exceptions():
                await self._stream.aclose()
        finally:
            if self._close_pool is not None:
                await self._close_pool()


def _validate_response_headers(
    headers: list[tuple[bytes, bytes]], spec: ConsumerSpec, safe_origin: str
) -> None:
    if len(headers) > _MAX_RESPONSE_HEADERS:
        raise URLPolicyError("TOO_MANY_HEADERS", safe_origin)
    encoded_size = sum(len(name) + len(value) + 4 for name, value in headers)
    if encoded_size > _MAX_ENCODED_HEADER_BYTES:
        raise URLPolicyError("HEADERS_TOO_LARGE", safe_origin)
    values = httpx.Headers(headers)
    encodings = {
        item.strip().lower()
        for item in values.get_list("content-encoding", split_commas=True)
        if item.strip()
    } or {"identity"}
    if not encodings <= _SUPPORTED_ENCODINGS:
        raise URLPolicyError("CONTENT_ENCODING_FORBIDDEN", safe_origin)
    content_type = values.get("content-type")
    if content_type is not None:
        mime = content_type.split(";", 1)[0].strip().lower()
        if not any(mime.startswith(prefix) for prefix in spec.accepted_mime_prefixes):
            raise URLPolicyError("MIME_TYPE_FORBIDDEN", safe_origin)
    content_lengths = values.get_list("content-length", split_commas=True)
    if len(content_lengths) > 1:
        raise URLPolicyError("CONTENT_LENGTH_INVALID", safe_origin)
    if content_lengths:
        content_length = content_lengths[0].strip(" \t")
        if _ASCII_DECIMAL.fullmatch(content_length) is None:
            raise URLPolicyError("CONTENT_LENGTH_INVALID", safe_origin)
        significant = content_length.lstrip("0") or "0"
        maximum = str(spec.max_decoded_body_bytes)
        if len(significant) > len(maximum) or (
            len(significant) == len(maximum) and significant > maximum
        ):
            raise URLPolicyError("BODY_TOO_LARGE", safe_origin)


class _LimitedResponse(httpx.Response):
    def __init__(
        self,
        *args,
        max_decoded_body_bytes: int,
        deadline: float,
        safe_origin: str,
        on_error,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._max_decoded_body_bytes = max_decoded_body_bytes
        self._safe_origin = safe_origin
        self._deadline = deadline
        self._on_limit_error = on_error

    def _check(self, size: int) -> None:
        reason = None
        if monotonic() > self._deadline:
            reason = "OVERALL_TIMEOUT"
        elif size > self._max_decoded_body_bytes:
            reason = "BODY_TOO_LARGE"
        if reason is not None:
            error = URLPolicyError(reason, self._safe_origin)
            self._on_limit_error(error)
            raise error

    def iter_bytes(self, chunk_size: int | None = None):
        if hasattr(self, "_content"):
            yield from super().iter_bytes(chunk_size)
            return
        size = 0
        try:
            for chunk in super().iter_bytes(chunk_size):
                size += len(chunk)
                self._check(size)
                yield chunk
        except BaseException:
            self.close()
            raise

    def iter_raw(self, chunk_size: int | None = None):
        size = 0
        try:
            for chunk in super().iter_raw(chunk_size):
                size += len(chunk)
                self._check(size)
                yield chunk
        except BaseException:
            self.close()
            raise

    async def aiter_bytes(self, chunk_size: int | None = None):
        if hasattr(self, "_content"):
            async for chunk in super().aiter_bytes(chunk_size):
                yield chunk
            return
        size = 0
        try:
            async for chunk in super().aiter_bytes(chunk_size):
                size += len(chunk)
                self._check(size)
                yield chunk
        except BaseException:
            await self.aclose()
            raise

    async def aiter_raw(self, chunk_size: int | None = None):
        size = 0
        try:
            async for chunk in super().aiter_raw(chunk_size):
                size += len(chunk)
                self._check(size)
                yield chunk
        except BaseException:
            await self.aclose()
            raise


class _ManagedTransportBase:
    def __init__(
        self,
        consumer: str,
        *,
        configured_origin: str | None,
        callback_origin: str | None,
        security: OutboundSecurityConfig | None,
    ):
        if consumer not in CONSUMER_REGISTRY:
            raise KeyError(consumer)
        self._consumer = consumer
        self._configured_origin = configured_origin
        self._callback_origin = callback_origin
        self._security = security or OutboundSecurityConfig()
        self._audit_events: deque[AuditEvent] = deque(maxlen=AUDIT_EVENT_CAPACITY)
        verify = self._security.ca_bundle
        if verify is not None:
            verify = ssl.create_default_context(cafile=verify)
        else:
            verify = True
        self._ssl_context = httpx.create_ssl_context(verify=verify, trust_env=False)

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit_events)

    def _record(self, reason: str, safe_origin: str) -> None:
        event = AuditEvent(
            consumer=self._consumer,
            reason=reason,
            safe_origin=safe_origin,
            delegated_to_policy_proxy=self._security.policy_proxy is not None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._audit_events.append(event)
        if reason not in {"ALLOWED", "PROXY_DELEGATED"}:
            from .runtime_http_audit import record_runtime_http_denial

            record_runtime_http_denial(
                consumer=event.consumer,
                reason=event.reason,
                url=event.safe_origin,
                delegated_to_policy_proxy=event.delegated_to_policy_proxy,
            )

    def _evaluate(self, method: str, url: str) -> URLDecision:
        spec = CONSUMER_REGISTRY[self._consumer]
        exceptions = (
            tuple(
                exception
                for exception in self._security.owner_exceptions
                if exception.consumer == self._consumer
            )
            if spec.allow_owner_exceptions
            else ()
        )
        try:
            decision = evaluate_url(
                self._consumer,
                method,
                url,
                trust_class=spec.trust_class,
                allowed_schemes=spec.allowed_schemes,
                allowed_methods=spec.allowed_methods,
                allowed_ports=spec.allowed_ports,
                fixed_origins=spec.fixed_origins,
                configured_origin=self._configured_origin,
                callback_origin=self._callback_origin,
                exceptions=exceptions,
                resolver=self._security.resolver,
            )
        except URLPolicyError as exc:
            self._record(exc.reason, exc.safe_url)
            raise
        self._record("ALLOWED", decision.origin)
        return decision

    def _evaluate_proxy(self) -> URLDecision:
        proxy = self._security.policy_proxy
        if proxy is None:
            raise RuntimeError("policy proxy is not configured")
        exceptions = tuple(
            exception
            for exception in self._security.owner_exceptions
            if exception.consumer == "runtime.local_probe"
        )
        try:
            decision = evaluate_url(
                "runtime.local_probe",
                "GET",
                proxy.url,
                trust_class=URLTrustClass.CONFIGURED_SERVICE,
                allowed_schemes=_HTTP_SCHEMES,
                allowed_methods=_READ_METHODS,
                allowed_ports=None,
                configured_origin=proxy.url,
                exceptions=exceptions,
                resolver=self._security.resolver,
            )
        except URLPolicyError as exc:
            self._record(exc.reason, exc.safe_url)
            raise
        self._record("PROXY_DELEGATED", decision.origin)
        return decision

    @staticmethod
    def _request_metadata(request: httpx.Request, decision: URLDecision):
        headers = [
            (name, value)
            for name, value in request.headers.raw
            if name.lower() not in {b"host", b"proxy-authorization", b"accept-encoding"}
        ]
        headers.append((b"Host", decision.origin.split("://", 1)[1].encode("ascii")))
        headers.append((b"Accept-Encoding", b"gzip, deflate"))
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = decision.hostname
        return headers, extensions


class ManagedHTTPTransport(_ManagedTransportBase, httpx.BaseTransport):
    def __init__(
        self,
        consumer: str,
        *,
        configured_origin: str | None = None,
        callback_origin: str | None = None,
        security: OutboundSecurityConfig | None = None,
    ):
        super().__init__(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        )
        self._active_pools: set[httpcore.ConnectionPool] = set()
        self._pools_lock = threading.Lock()

    def _pool(self, decision: URLDecision) -> httpcore.ConnectionPool:
        if self._security.policy_proxy is None:
            pool = httpcore.ConnectionPool(
                ssl_context=self._ssl_context,
                retries=self._security.retries,
                network_backend=DecisionNetworkBackend(decision),
                local_address=self._security.local_address,
                socket_options=self._security.socket_options or None,
            )
        else:
            proxy = self._evaluate_proxy()
            pool = httpcore.HTTPProxy(
                proxy_url=proxy.normalized_url,
                ssl_context=self._ssl_context,
                proxy_ssl_context=(
                    self._ssl_context if proxy.origin.startswith("https://") else None
                ),
                retries=self._security.retries,
                network_backend=DecisionNetworkBackend(proxy),
                local_address=self._security.local_address,
                socket_options=self._security.socket_options or None,
            )
        with self._pools_lock:
            self._active_pools.add(pool)
        return pool

    def _close_pool(self, pool: httpcore.ConnectionPool) -> None:
        with self._pools_lock:
            if pool not in self._active_pools:
                return
            self._active_pools.remove(pool)
        pool.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        decision = self._evaluate(request.method, str(request.url))
        headers, extensions = self._request_metadata(request, decision)
        core_request = httpcore.Request(
            method=request.method,
            url=decision.normalized_url,
            headers=headers,
            content=request.stream,
            extensions=extensions,
        )
        pool = self._pool(decision)
        try:
            with _map_httpcore_exceptions():
                response = pool.handle_request(core_request)
        except BaseException:
            self._close_pool(pool)
            raise
        extensions = dict(response.extensions)
        extensions["url_decision"] = decision
        spec = CONSUMER_REGISTRY[self._consumer]
        try:
            _validate_response_headers(response.headers, spec, decision.origin)
            deadline = request.extensions.get("safe_overall_deadline")
            if deadline is None:
                deadline = monotonic() + _OVERALL_TIMEOUT
            return _LimitedResponse(
                status_code=response.status,
                headers=response.headers,
                stream=_ResponseStream(response.stream, lambda: self._close_pool(pool)),
                extensions=extensions,
                max_decoded_body_bytes=spec.max_decoded_body_bytes,
                deadline=deadline,
                safe_origin=decision.origin,
                on_error=lambda error: self._record(error.reason, error.safe_url),
            )
        except BaseException as exc:
            if isinstance(exc, URLPolicyError):
                self._record(exc.reason, exc.safe_url)
            with _map_httpcore_exceptions():
                response.stream.close()
            self._close_pool(pool)
            raise

    def close(self) -> None:
        with self._pools_lock:
            pools = tuple(self._active_pools)
            self._active_pools.clear()
        for pool in pools:
            pool.close()


class AsyncManagedHTTPTransport(_ManagedTransportBase, httpx.AsyncBaseTransport):
    def __init__(
        self,
        consumer: str,
        *,
        configured_origin: str | None = None,
        callback_origin: str | None = None,
        security: OutboundSecurityConfig | None = None,
    ):
        super().__init__(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        )
        self._active_pools: set[httpcore.AsyncConnectionPool] = set()

    def _pool(self, decision: URLDecision) -> httpcore.AsyncConnectionPool:
        if self._security.policy_proxy is None:
            pool = httpcore.AsyncConnectionPool(
                ssl_context=self._ssl_context,
                retries=self._security.retries,
                network_backend=AsyncDecisionNetworkBackend(decision),
                local_address=self._security.local_address,
                socket_options=self._security.socket_options or None,
            )
        else:
            proxy = self._evaluate_proxy()
            pool = httpcore.AsyncHTTPProxy(
                proxy_url=proxy.normalized_url,
                ssl_context=self._ssl_context,
                proxy_ssl_context=(
                    self._ssl_context if proxy.origin.startswith("https://") else None
                ),
                retries=self._security.retries,
                network_backend=AsyncDecisionNetworkBackend(proxy),
                local_address=self._security.local_address,
                socket_options=self._security.socket_options or None,
            )
        self._active_pools.add(pool)
        return pool

    async def _close_pool(self, pool: httpcore.AsyncConnectionPool) -> None:
        if pool not in self._active_pools:
            return
        self._active_pools.remove(pool)
        await pool.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        decision = self._evaluate(request.method, str(request.url))
        headers, extensions = self._request_metadata(request, decision)
        core_request = httpcore.Request(
            method=request.method,
            url=decision.normalized_url,
            headers=headers,
            content=request.stream,
            extensions=extensions,
        )
        pool = self._pool(decision)
        try:
            with _map_httpcore_exceptions():
                response = await pool.handle_async_request(core_request)
        except BaseException:
            await self._close_pool(pool)
            raise
        extensions = dict(response.extensions)
        extensions["url_decision"] = decision
        spec = CONSUMER_REGISTRY[self._consumer]
        try:
            _validate_response_headers(response.headers, spec, decision.origin)
            deadline = request.extensions.get("safe_overall_deadline")
            if deadline is None:
                deadline = monotonic() + _OVERALL_TIMEOUT
            return _LimitedResponse(
                status_code=response.status,
                headers=response.headers,
                stream=_AsyncResponseStream(
                    response.stream, lambda: self._close_pool(pool)
                ),
                extensions=extensions,
                max_decoded_body_bytes=spec.max_decoded_body_bytes,
                deadline=deadline,
                safe_origin=decision.origin,
                on_error=lambda error: self._record(error.reason, error.safe_url),
            )
        except BaseException as exc:
            if isinstance(exc, URLPolicyError):
                self._record(exc.reason, exc.safe_url)
            with _map_httpcore_exceptions():
                await response.stream.aclose()
            await self._close_pool(pool)
            raise

    async def aclose(self) -> None:
        pools = tuple(self._active_pools)
        self._active_pools.clear()
        for pool in pools:
            await pool.aclose()


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        _READ_TIMEOUT,
        connect=_CONNECT_TIMEOUT,
        write=_WRITE_TIMEOUT,
        pool=_POOL_TIMEOUT,
    )


def _redirect_request(
    client: httpx.Client | httpx.AsyncClient,
    request: httpx.Request,
    response: httpx.Response,
    target_url: str,
) -> httpx.Request:
    method = request.method
    content = None
    headers = httpx.Headers(request.headers)
    if response.status_code == 303 or (
        response.status_code in {301, 302} and method != "HEAD"
    ):
        method = "GET"
        for name in ("content-length", "content-type", "transfer-encoding"):
            headers.pop(name, None)
    elif response.status_code in {307, 308}:
        try:
            content = request.content
        except (httpx.RequestNotRead, httpx.StreamConsumed) as exc:
            raise URLPolicyError(
                "NON_REWINDABLE_BODY", normalize_origin(str(request.url))
            ) from exc
    extensions = dict(request.extensions)
    return client.build_request(
        method,
        target_url,
        content=content,
        headers=headers,
        extensions=extensions,
    )


def _redirect_target(request_url: str, location: str):
    try:
        target_url = urljoin(request_url, location)
    except ValueError as exc:
        raise URLPolicyError("INVALID_URL", "<invalid-url>") from exc
    return normalize_url(target_url)


def _sanitize_credentials(request: httpx.Request, spec: ConsumerSpec) -> None:
    request.headers.pop("proxy-authorization", None)
    if spec.credential_origin_policy == "none":
        for name in spec.credential_headers:
            request.headers.pop(name, None)


class SafeClient(httpx.Client):
    def __init__(
        self,
        transport: ManagedHTTPTransport,
        *,
        timeout: httpx.Timeout | float | None = None,
        overall_timeout: float = _OVERALL_TIMEOUT,
    ):
        if type(transport) is not ManagedHTTPTransport:
            raise TypeError("SafeClient requires ManagedHTTPTransport")
        super().__init__(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout if timeout is not None else _timeout(),
        )
        self._overall_timeout = overall_timeout

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return self._transport.audit_events

    def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = None,
        follow_redirects: Any = None,
    ) -> httpx.Response:
        spec = CONSUMER_REGISTRY[self._transport._consumer]
        deadline = request.extensions.setdefault(
            "safe_overall_deadline", monotonic() + self._overall_timeout
        )
        try:
            seen = {normalize_url(str(request.url)).normalized_url}
        except URLPolicyError as exc:
            self._transport._record(exc.reason, exc.safe_url)
            raise
        history: list[httpx.Response] = []
        current = request
        while True:
            if monotonic() > deadline:
                error = URLPolicyError(
                    "OVERALL_TIMEOUT", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            _sanitize_credentials(current, spec)
            try:
                response = super().send(
                    current,
                    stream=True,
                    auth=None if spec.credential_origin_policy == "none" else auth,
                    follow_redirects=False,
                )
            except URLPolicyError:
                raise
            if (
                response.status_code not in _REDIRECT_STATUSES
                or "location" not in response.headers
            ):
                response.history = history
                if not stream:
                    response.read()
                return response
            # A caller may restrict redirects, never relax the consumer policy.
            if spec.redirect_policy == "deny" or follow_redirects is False:
                response.close()
                error = URLPolicyError(
                    "REDIRECT_FORBIDDEN", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            if len(history) >= spec.max_redirects:
                response.close()
                error = URLPolicyError(
                    "TOO_MANY_REDIRECTS", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            try:
                target = _redirect_target(
                    str(current.url), response.headers["location"]
                )
            except URLPolicyError as exc:
                response.close()
                self._transport._record(exc.reason, exc.safe_url)
                raise
            current_origin = normalize_origin(str(current.url))
            if current.url.scheme == "https" and target.scheme == "http":
                response.close()
                error = URLPolicyError("HTTPS_DOWNGRADE", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            if (
                spec.redirect_policy == "same_origin"
                and target.origin != current_origin
            ):
                response.close()
                error = URLPolicyError("REDIRECT_ORIGIN_FORBIDDEN", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            if target.normalized_url in seen:
                response.close()
                error = URLPolicyError("REDIRECT_LOOP", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            seen.add(target.normalized_url)
            try:
                next_request = _redirect_request(
                    self, current, response, target.normalized_url
                )
            except URLPolicyError as exc:
                response.close()
                self._transport._record(exc.reason, exc.safe_url)
                raise
            history.append(response)
            current = next_request
            current.extensions["safe_overall_deadline"] = deadline
            response.close()

    def download(self, url: str, destination: str | os.PathLike[str]) -> Path:
        target = Path(destination)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        try:
            try:
                output = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with output:
                with self.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            return target
        finally:
            Path(temporary).unlink(missing_ok=True)


class SafeAsyncClient(httpx.AsyncClient):
    def __init__(
        self,
        transport: AsyncManagedHTTPTransport,
        *,
        timeout: httpx.Timeout | float | None = None,
        overall_timeout: float = _OVERALL_TIMEOUT,
    ):
        if type(transport) is not AsyncManagedHTTPTransport:
            raise TypeError("SafeAsyncClient requires AsyncManagedHTTPTransport")
        super().__init__(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout if timeout is not None else _timeout(),
        )
        self._overall_timeout = overall_timeout

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return self._transport.audit_events

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = None,
        follow_redirects: Any = None,
    ) -> httpx.Response:
        spec = CONSUMER_REGISTRY[self._transport._consumer]
        deadline = request.extensions.setdefault(
            "safe_overall_deadline", monotonic() + self._overall_timeout
        )
        try:
            seen = {normalize_url(str(request.url)).normalized_url}
        except URLPolicyError as exc:
            self._transport._record(exc.reason, exc.safe_url)
            raise
        history: list[httpx.Response] = []
        current = request
        while True:
            if monotonic() > deadline:
                error = URLPolicyError(
                    "OVERALL_TIMEOUT", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            _sanitize_credentials(current, spec)
            response = await super().send(
                current,
                stream=True,
                auth=None if spec.credential_origin_policy == "none" else auth,
                follow_redirects=False,
            )
            if (
                response.status_code not in _REDIRECT_STATUSES
                or "location" not in response.headers
            ):
                response.history = history
                if not stream:
                    await response.aread()
                return response
            # Keep the same stricter per-request boundary as the sync client.
            if spec.redirect_policy == "deny" or follow_redirects is False:
                await response.aclose()
                error = URLPolicyError(
                    "REDIRECT_FORBIDDEN", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            if len(history) >= spec.max_redirects:
                await response.aclose()
                error = URLPolicyError(
                    "TOO_MANY_REDIRECTS", normalize_origin(str(current.url))
                )
                self._transport._record(error.reason, error.safe_url)
                raise error
            try:
                target = _redirect_target(
                    str(current.url), response.headers["location"]
                )
            except URLPolicyError as exc:
                await response.aclose()
                self._transport._record(exc.reason, exc.safe_url)
                raise
            current_origin = normalize_origin(str(current.url))
            if current.url.scheme == "https" and target.scheme == "http":
                await response.aclose()
                error = URLPolicyError("HTTPS_DOWNGRADE", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            if (
                spec.redirect_policy == "same_origin"
                and target.origin != current_origin
            ):
                await response.aclose()
                error = URLPolicyError("REDIRECT_ORIGIN_FORBIDDEN", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            if target.normalized_url in seen:
                await response.aclose()
                error = URLPolicyError("REDIRECT_LOOP", target.origin)
                self._transport._record(error.reason, error.safe_url)
                raise error
            seen.add(target.normalized_url)
            try:
                next_request = _redirect_request(
                    self, current, response, target.normalized_url
                )
            except URLPolicyError as exc:
                await response.aclose()
                self._transport._record(exc.reason, exc.safe_url)
                raise
            history.append(response)
            current = next_request
            current.extensions["safe_overall_deadline"] = deadline
            await response.aclose()

    async def download(self, url: str, destination: str | os.PathLike[str]) -> Path:
        target = Path(destination)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        try:
            try:
                output = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with output:
                async with self.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            return target
        finally:
            Path(temporary).unlink(missing_ok=True)


def safe_client(
    consumer: str,
    *,
    configured_origin: str | None = None,
    callback_origin: str | None = None,
    security: OutboundSecurityConfig | None = None,
    timeout: httpx.Timeout | float | None = None,
    overall_timeout: float = _OVERALL_TIMEOUT,
) -> SafeClient:
    if security is None:
        from openprogram.config_schema import load_outbound_security_config

        security = load_outbound_security_config(consumer)
    return SafeClient(
        ManagedHTTPTransport(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        ),
        timeout=timeout,
        overall_timeout=overall_timeout,
    )


def safe_async_client(
    consumer: str,
    *,
    configured_origin: str | None = None,
    callback_origin: str | None = None,
    security: OutboundSecurityConfig | None = None,
    timeout: httpx.Timeout | float | None = None,
    overall_timeout: float = _OVERALL_TIMEOUT,
) -> SafeAsyncClient:
    if security is None:
        from openprogram.config_schema import load_outbound_security_config

        security = load_outbound_security_config(consumer)
    return SafeAsyncClient(
        AsyncManagedHTTPTransport(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        ),
        timeout=timeout,
        overall_timeout=overall_timeout,
    )


def _configured_security(
    consumer: str,
    origin: str,
    owner_exception: OwnerURLException | None,
    *,
    local_address: str | None = None,
    socket_options: tuple[tuple[int, int, int], ...] = (),
) -> OutboundSecurityConfig:
    from openprogram.config_schema import load_outbound_security_config

    security = load_outbound_security_config(consumer)
    exceptions = security.owner_exceptions
    if owner_exception is not None:
        if type(owner_exception) is not OwnerURLException:
            raise TypeError("owner_exception must be an OwnerURLException")
        if (
            owner_exception.consumer != consumer
            or owner_exception.origin is None
            or owner_exception.network is not None
        ):
            raise URLPolicyError("OWNER_EXCEPTION_MISMATCH", origin)
        try:
            authorized_origin = normalize_origin(owner_exception.origin)
        except URLPolicyError:
            raise URLPolicyError("OWNER_EXCEPTION_MISMATCH", origin) from None
        if authorized_origin != origin:
            raise URLPolicyError("OWNER_EXCEPTION_MISMATCH", origin)
        if owner_exception not in exceptions:
            exceptions += (owner_exception,)
    return replace(
        security,
        owner_exceptions=exceptions,
        local_address=local_address,
        socket_options=socket_options,
    )


def configured_safe_client(
    consumer: str,
    configured_url: str,
    *,
    owner_exception: OwnerURLException | None = None,
    timeout: httpx.Timeout | float | None = None,
    overall_timeout: float = _OVERALL_TIMEOUT,
    local_address: str | None = None,
    socket_options: tuple[tuple[int, int, int], ...] = (),
) -> SafeClient:
    """Freeze an exact origin; private access needs explicit owner authorization."""
    origin = normalize_origin(configured_url)
    return safe_client(
        consumer,
        configured_origin=origin,
        security=_configured_security(
            consumer,
            origin,
            owner_exception,
            local_address=local_address,
            socket_options=socket_options,
        ),
        timeout=timeout,
        overall_timeout=overall_timeout,
    )


def configured_safe_async_client(
    consumer: str,
    configured_url: str,
    *,
    owner_exception: OwnerURLException | None = None,
    timeout: httpx.Timeout | float | None = None,
    overall_timeout: float = _OVERALL_TIMEOUT,
    local_address: str | None = None,
    socket_options: tuple[tuple[int, int, int], ...] = (),
) -> SafeAsyncClient:
    """Async exact-origin counterpart to :func:`configured_safe_client`."""
    origin = normalize_origin(configured_url)
    return safe_async_client(
        consumer,
        configured_origin=origin,
        security=_configured_security(
            consumer,
            origin,
            owner_exception,
            local_address=local_address,
            socket_options=socket_options,
        ),
        timeout=timeout,
        overall_timeout=overall_timeout,
    )


def require_json_mime(response: httpx.Response) -> None:
    """Reject catalog metadata not explicitly served as JSON."""
    content_type = response.headers.get("content-type", "")
    mime = content_type.split(";", 1)[0].strip().lower()
    subtype = (
        mime.removeprefix("application/") if mime.startswith("application/") else ""
    )
    if mime != "application/json" and not (
        len(subtype) > len("+json") and subtype.endswith("+json")
    ):
        raise URLPolicyError("MIME_TYPE_FORBIDDEN", normalize_origin(str(response.url)))


def raise_for_status_sanitized(response: httpx.Response) -> None:
    """Raise an origin-only status error using the standard HTTP reason."""
    if not 200 <= response.status_code < 300:
        raise SafeHTTPStatusError(
            response.status_code, normalize_origin(str(response.url))
        ) from None


__all__ = [
    "AuditEvent",
    "AsyncDecisionNetworkBackend",
    "AsyncManagedHTTPTransport",
    "CONSUMER_REGISTRY",
    "ConsumerSpec",
    "DecisionNetworkBackend",
    "ManagedHTTPTransport",
    "OutboundSecurityConfig",
    "PolicyProxyConfig",
    "SDKDisposition",
    "SafeAsyncClient",
    "SafeClient",
    "SafeHTTPStatusError",
    "configured_safe_async_client",
    "configured_safe_client",
    "require_json_mime",
    "raise_for_status_sanitized",
    "require_active_sdk_transport",
    "safe_async_client",
    "safe_client",
]
