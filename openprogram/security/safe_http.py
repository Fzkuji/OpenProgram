"""Consumer declarations and peer-constrained Runtime HTTP clients."""

from __future__ import annotations

import ipaddress
import ssl
import threading
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from .url_policy import (
    OwnerURLException,
    Resolver,
    URLDecision,
    URLPolicyError,
    URLTrustClass,
    evaluate_url,
    resolve_all,
)


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
    allow_owner_exceptions: bool
    sdk_disposition: SDKDisposition | None = None


_HTTP_SCHEMES = frozenset({"http", "https"})
_HTTPS_SCHEME = frozenset({"https"})
_PUBLIC_PORTS = frozenset({80, 443})
_READ_METHODS = frozenset({"GET", "HEAD"})
_API_METHODS = frozenset({"GET", "HEAD", "POST"})
_ANY_MIME = ("application/", "audio/", "image/", "text/", "video/")
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
        "channel.slack.api": frozenset({"https://slack.com"}),
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
        "updater.pip": frozenset({"https://pypi.org"}),
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
        "provider.oauth.fixed": frozenset(
            {
                "https://accounts.google.com",
                "https://api.github.com",
                "https://auth.openai.com",
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
        allow_owner_exceptions=False,
    )


_SPECS = (
    _download("tool.web_fetch", URLTrustClass.UNTRUSTED_PUBLIC),
    _api("tool.web_search.fixed_api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("tool.web_search.configured_api", URLTrustClass.CONFIGURED_SERVICE),
    _api("tool.image_api.fixed", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("tool.image_api.configured", URLTrustClass.CONFIGURED_SERVICE),
    _download("tool.image_result.download", URLTrustClass.UNTRUSTED_PUBLIC),
    _download("channel.attachment.download", URLTrustClass.UNTRUSTED_PUBLIC),
    _api("channel.telegram.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.discord.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.slack.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.wechat.api", URLTrustClass.CONFIGURED_SERVICE),
    _api("channel.feishu.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.matrix.configured", URLTrustClass.CONFIGURED_SERVICE),
    _download("channel.generated_asset.download", URLTrustClass.UNTRUSTED_PUBLIC),
    _download("skills.github.catalog", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("skills.configured.catalog", URLTrustClass.CONFIGURED_SERVICE),
    _download("plugins.marketplace", URLTrustClass.CONFIGURED_SERVICE),
    _download("plugins.autoupdate", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("updater.github", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("updater.pip", URLTrustClass.FIXED_PUBLIC_SERVICE),
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
    _api("mcp.configured.http", URLTrustClass.CONFIGURED_SERVICE),
    _api("mcp.configured.sse", URLTrustClass.CONFIGURED_SERVICE),
    _callback("mcp.loopback.callback"),
    _api("tts.fixed_api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("tts.configured_api", URLTrustClass.CONFIGURED_SERVICE),
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


PoolKey = tuple[str, str, str, tuple[str, ...], str | None]


@dataclass(frozen=True)
class OutboundSecurityConfig:
    resolver: Resolver = resolve_all
    owner_exceptions: tuple[OwnerURLException, ...] = ()
    ca_bundle: ssl.SSLContext | str | None = None
    retries: int = 0
    policy_proxy_identity: str | None = None

    def __post_init__(self) -> None:
        insecure_context = isinstance(self.ca_bundle, ssl.SSLContext) and (
            not self.ca_bundle.check_hostname
            or self.ca_bundle.verify_mode != ssl.CERT_REQUIRED
        )
        if isinstance(self.ca_bundle, bool) or insecure_context:
            raise ValueError("TLS verification cannot be disabled")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")


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
        self._underlying = underlying or AutoBackend()
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
    def __init__(self, stream):
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self) -> None:
        self._stream.close()


class _AsyncResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


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
        verify = self._security.ca_bundle
        if isinstance(verify, str):
            verify = ssl.create_default_context(cafile=verify)
        elif verify is None:
            verify = True
        self._ssl_context = httpx.create_ssl_context(verify=verify, trust_env=False)

    def _evaluate(self, method: str, url: str) -> URLDecision:
        spec = CONSUMER_REGISTRY[self._consumer]
        return evaluate_url(
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
            exceptions=self._security.owner_exceptions,
            resolver=self._security.resolver,
        )

    def _pool_key(self, decision: URLDecision) -> PoolKey:
        return (
            decision.consumer,
            decision.trust_class.value,
            decision.origin,
            tuple(map(str, decision.resolved_ips)),
            self._security.policy_proxy_identity,
        )


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
        self._pools: dict[PoolKey, httpcore.ConnectionPool] = {}
        self._pools_lock = threading.Lock()

    def _pool(self, decision: URLDecision) -> httpcore.ConnectionPool:
        key = self._pool_key(decision)
        with self._pools_lock:
            pool = self._pools.get(key)
            if pool is None:
                pool = httpcore.ConnectionPool(
                    ssl_context=self._ssl_context,
                    retries=self._security.retries,
                    network_backend=DecisionNetworkBackend(decision),
                )
                self._pools[key] = pool
        return pool

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        decision = self._evaluate(request.method, str(request.url))
        core_request = httpcore.Request(
            method=request.method,
            url=decision.normalized_url,
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool(decision).handle_request(core_request)
        except httpcore.ConnectError as exc:
            raise httpx.ConnectError(str(exc), request=request) from exc
        extensions = dict(response.extensions)
        extensions["url_decision"] = decision
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=extensions,
        )

    def close(self) -> None:
        with self._pools_lock:
            pools = tuple(self._pools.values())
            self._pools.clear()
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
        self._pools: dict[PoolKey, httpcore.AsyncConnectionPool] = {}

    def _pool(self, decision: URLDecision) -> httpcore.AsyncConnectionPool:
        key = self._pool_key(decision)
        pool = self._pools.get(key)
        if pool is None:
            pool = httpcore.AsyncConnectionPool(
                ssl_context=self._ssl_context,
                retries=self._security.retries,
                network_backend=AsyncDecisionNetworkBackend(decision),
            )
            self._pools[key] = pool
        return pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        decision = self._evaluate(request.method, str(request.url))
        core_request = httpcore.Request(
            method=request.method,
            url=decision.normalized_url,
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._pool(decision).handle_async_request(core_request)
        except httpcore.ConnectError as exc:
            raise httpx.ConnectError(str(exc), request=request) from exc
        extensions = dict(response.extensions)
        extensions["url_decision"] = decision
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_AsyncResponseStream(response.stream),
            extensions=extensions,
        )

    async def aclose(self) -> None:
        pools = tuple(self._pools.values())
        self._pools.clear()
        for pool in pools:
            await pool.aclose()


class SafeClient(httpx.Client):
    def __init__(self, transport: ManagedHTTPTransport):
        super().__init__(transport=transport, trust_env=False, follow_redirects=False)


class SafeAsyncClient(httpx.AsyncClient):
    def __init__(self, transport: AsyncManagedHTTPTransport):
        super().__init__(transport=transport, trust_env=False, follow_redirects=False)


def safe_client(
    consumer: str,
    *,
    configured_origin: str | None = None,
    callback_origin: str | None = None,
    security: OutboundSecurityConfig | None = None,
) -> SafeClient:
    return SafeClient(
        ManagedHTTPTransport(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        )
    )


def safe_async_client(
    consumer: str,
    *,
    configured_origin: str | None = None,
    callback_origin: str | None = None,
    security: OutboundSecurityConfig | None = None,
) -> SafeAsyncClient:
    return SafeAsyncClient(
        AsyncManagedHTTPTransport(
            consumer,
            configured_origin=configured_origin,
            callback_origin=callback_origin,
            security=security,
        )
    )


__all__ = [
    "AsyncDecisionNetworkBackend",
    "AsyncManagedHTTPTransport",
    "CONSUMER_REGISTRY",
    "ConsumerSpec",
    "DecisionNetworkBackend",
    "ManagedHTTPTransport",
    "OutboundSecurityConfig",
    "PoolKey",
    "SDKDisposition",
    "SafeAsyncClient",
    "SafeClient",
    "safe_async_client",
    "safe_client",
]
