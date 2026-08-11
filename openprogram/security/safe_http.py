"""Consumer declarations for the future managed HTTP transport.

Task 1 intentionally defines policy data only. Network I/O is added in later
tasks after the transport has its own socket-level tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .url_policy import URLTrustClass


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
    redirect_policy: str
    max_redirects: int
    max_decoded_body_bytes: int
    accepted_mime_prefixes: tuple[str, ...]
    credential_origin_policy: str
    allow_owner_exceptions: bool
    sdk_disposition: SDKDisposition | None = None


_HTTP_SCHEMES = frozenset({"http", "https"})
_PUBLIC_PORTS = frozenset({80, 443})
_READ_METHODS = frozenset({"GET", "HEAD"})
_API_METHODS = frozenset({"GET", "HEAD", "POST"})
_ANY_MIME = ("application/", "audio/", "image/", "text/", "video/")


def _download(consumer: str, trust_class: URLTrustClass) -> ConsumerSpec:
    configured = trust_class == URLTrustClass.CONFIGURED_SERVICE
    return ConsumerSpec(
        consumer=consumer,
        trust_class=trust_class,
        allowed_schemes=_HTTP_SCHEMES,
        allowed_methods=_READ_METHODS,
        allowed_ports=None if configured else _PUBLIC_PORTS,
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
    return ConsumerSpec(
        consumer=consumer,
        trust_class=trust_class,
        allowed_schemes=_HTTP_SCHEMES,
        allowed_methods=_API_METHODS,
        allowed_ports=None if configured else _PUBLIC_PORTS,
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
    _api("channel.wechat.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.feishu.api", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _api("channel.matrix.configured", URLTrustClass.CONFIGURED_SERVICE),
    _download("channel.generated_asset.download", URLTrustClass.UNTRUSTED_PUBLIC),
    _download("skills.github.catalog", URLTrustClass.FIXED_PUBLIC_SERVICE),
    _download("skills.configured.catalog", URLTrustClass.CONFIGURED_SERVICE),
    _download("plugins.marketplace", URLTrustClass.FIXED_PUBLIC_SERVICE),
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

CONSUMER_REGISTRY = MappingProxyType({spec.consumer: spec for spec in _SPECS})


__all__ = ["CONSUMER_REGISTRY", "ConsumerSpec", "SDKDisposition"]
