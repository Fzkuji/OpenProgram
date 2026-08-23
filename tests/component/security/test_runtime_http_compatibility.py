import http.server
import subprocess
import sys
import threading
from dataclasses import replace
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

import httpcore
import pytest

from openprogram.security import safe_http
from openprogram.security.safe_http import (
    CONSUMER_REGISTRY,
    OutboundSecurityConfig,
    configured_safe_client,
    require_active_sdk_transport,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException, URLPolicyError


COMPATIBILITY_FIXTURES = {
    "tool.web_fetch": (
        "untrusted_public",
        "GET",
        "https://cdn.example.test",
        443,
        ("http", "https"),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        5_242_881,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "tool.web_search.fixed_api": (
        "fixed_public_service",
        "POST",
        "https://api.exa.ai",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        None,
    ),
    "tool.web_search.configured_api": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
    "tool.image_api.fixed": (
        "fixed_public_service",
        "POST",
        "https://api.openai.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        None,
    ),
    "tool.image_api.configured": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
    "tool.image_result.download": (
        "untrusted_public",
        "GET",
        "https://cdn.example.test",
        443,
        ("http", "https"),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        33_554_432,
        ("image/", "application/octet-stream"),
        "none",
        False,
        None,
    ),
    "channel.attachment.download": (
        "untrusted_public",
        "GET",
        "https://cdn.example.test",
        443,
        ("http", "https"),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        20_971_520,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "channel.telegram.api": (
        "fixed_public_service",
        "POST",
        "https://api.telegram.org",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "exact_origin",
    ),
    "channel.discord.api": (
        "fixed_public_service",
        "PATCH",
        "https://discord.com",
        443,
        ("https",),
        ("GET", "HEAD", "PATCH", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "exact_origin",
    ),
    "channel.discord.gateway_sdk": (
        "fixed_public_service",
        "POST",
        "https://discord.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "disabled",
    ),
    "channel.slack.api": (
        "fixed_public_service",
        "POST",
        "https://slack.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "exact_origin",
    ),
    "channel.slack.gateway_sdk": (
        "fixed_public_service",
        "POST",
        "https://slack.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "disabled",
    ),
    "channel.slack.attachment": (
        "fixed_public_service",
        "GET",
        "https://files.slack.com",
        443,
        ("https",),
        ("GET", "HEAD"),
        (80, 443),
        "same_origin",
        5,
        20_971_520,
        ("application/", "audio/", "image/", "text/", "video/"),
        "same_origin",
        False,
        None,
    ),
    "channel.slack.generated_asset.upload": (
        "fixed_public_service",
        "POST",
        "https://files.slack.com",
        443,
        ("https",),
        ("POST",),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "none",
        False,
        None,
    ),
    "channel.telegram.attachment": (
        "fixed_public_service",
        "GET",
        "https://api.telegram.org",
        443,
        ("https",),
        ("GET", "HEAD"),
        (80, 443),
        "same_origin",
        5,
        20_971_520,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "channel.wechat.api": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "exact_origin",
    ),
    "channel.feishu.api": (
        "fixed_public_service",
        "POST",
        "https://open.feishu.cn",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "exact_origin",
    ),
    "channel.matrix.configured": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "exact_origin",
    ),
    "channel.generated_asset.download": (
        "untrusted_public",
        "GET",
        "https://cdn.example.test",
        443,
        ("http", "https"),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "skills.github.catalog": (
        "fixed_public_service",
        "GET",
        "https://github.com",
        443,
        ("https",),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "skills.configured.catalog": (
        "configured_service",
        "GET",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD"),
        None,
        "same_origin",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "same_origin",
        True,
        None,
    ),
    "plugins.marketplace": (
        "configured_service",
        "GET",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD"),
        None,
        "same_origin",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "same_origin",
        True,
        None,
    ),
    "plugins.autoupdate": (
        "fixed_public_service",
        "GET",
        "https://pypi.org",
        443,
        ("https",),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "updater.github": (
        "fixed_public_service",
        "GET",
        "https://api.github.com",
        443,
        ("https",),
        ("GET", "HEAD"),
        (80, 443),
        "public",
        5,
        33_554_432,
        ("application/", "audio/", "image/", "text/", "video/"),
        "none",
        False,
        None,
    ),
    "provider.fixed_api": (
        "fixed_public_service",
        "POST",
        "https://api.openai.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        None,
    ),
    "provider.configured_api": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
    "provider.oauth.fixed": (
        "fixed_public_service",
        "POST",
        "https://accounts.google.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        None,
    ),
    "provider.google.sdk": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "injected_transport",
    ),
    "provider.openai.sdk": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "injected_transport",
    ),
    "provider.anthropic.sdk": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "injected_transport",
    ),
    "provider.amazon_bedrock.sdk": (
        "fixed_public_service",
        "POST",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        "disabled",
    ),
    "mcp.configured.http": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "injected_transport",
    ),
    "mcp.configured.sse": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "injected_transport",
    ),
    "mcp.loopback.callback": (
        "loopback_callback",
        "GET",
        "http://127.0.0.1:17654",
        17654,
        ("http",),
        ("GET", "HEAD"),
        None,
        "deny",
        1,
        1_048_576,
        ("application/", "text/"),
        "none",
        False,
        None,
    ),
    "tts.fixed_api": (
        "fixed_public_service",
        "POST",
        "https://api.elevenlabs.io",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("audio/", "application/octet-stream"),
        "same_origin",
        False,
        "exact_origin",
    ),
    "tts.configured_api": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("audio/", "application/octet-stream"),
        "same_origin",
        True,
        "exact_origin",
    ),
    "tts.edge_sdk": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        "disabled",
    ),
    "webui.mcp.catalog": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
    "webui.model_listing.fixed": (
        "fixed_public_service",
        "POST",
        "https://models.dev",
        443,
        ("https",),
        ("GET", "HEAD", "POST"),
        (80, 443),
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        False,
        None,
    ),
    "webui.model_listing.configured": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
    "runtime.local_probe": (
        "configured_service",
        "POST",
        "http://configured.example.test:17654",
        17654,
        ("http", "https"),
        ("GET", "HEAD", "POST"),
        None,
        "same_origin",
        5,
        16_777_216,
        ("application/", "text/"),
        "same_origin",
        True,
        None,
    ),
}


FIXED_ORIGIN_FIXTURES = {
    "tool.web_search.fixed_api": (
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
    ),
    "tool.image_api.fixed": (
        "https://api.anthropic.com",
        "https://api.openai.com",
        "https://generativelanguage.googleapis.com",
        "https://queue.fal.run",
    ),
    "channel.telegram.api": ("https://api.telegram.org",),
    "channel.discord.api": ("https://discord.com",),
    "channel.discord.gateway_sdk": ("https://discord.com",),
    "channel.slack.api": ("https://slack.com",),
    "channel.slack.gateway_sdk": ("https://slack.com",),
    "channel.slack.attachment": ("https://files.slack.com", "https://slack.com"),
    "channel.slack.generated_asset.upload": ("https://files.slack.com",),
    "channel.telegram.attachment": ("https://api.telegram.org",),
    "channel.feishu.api": ("https://open.feishu.cn", "https://open.larksuite.com"),
    "skills.github.catalog": (
        "https://clawhub.ai",
        "https://codeload.github.com",
        "https://github.com",
    ),
    "plugins.autoupdate": ("https://pypi.org", "https://registry.npmjs.org"),
    "updater.github": ("https://api.github.com",),
    "provider.fixed_api": (
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
        "https://cli-chat-proxy.grok.com",
        "https://cloudcode-pa.googleapis.com",
        "https://generativelanguage.googleapis.com",
        "https://opencode.ai",
        "https://openrouter.ai",
        "https://router.huggingface.co",
        "https://token-plan.cn-beijing.maas.aliyuncs.com",
    ),
    "provider.oauth.fixed": (
        "https://accounts.google.com",
        "https://accounts.x.ai",
        "https://api.github.com",
        "https://auth.openai.com",
        "https://auth.x.ai",
        "https://claude.ai",
        "https://console.anthropic.com",
        "https://github.com",
        "https://oauth2.googleapis.com",
    ),
    "provider.amazon_bedrock.sdk": (
        "https://bedrock.us-east-1.amazonaws.com",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
    ),
    "tts.fixed_api": ("https://api.elevenlabs.io", "https://api.openai.com"),
    "webui.model_listing.fixed": (
        "https://api.anthropic.com",
        "https://generativelanguage.googleapis.com",
        "https://models.dev",
    ),
}


def test_every_registry_consumer_has_a_literal_compatibility_fixture() -> None:
    assert set(COMPATIBILITY_FIXTURES) == set(CONSUMER_REGISTRY)


def test_literal_fixed_origin_fixtures_cover_every_fixed_consumer() -> None:
    fixed_consumers = {
        consumer
        for consumer, spec in CONSUMER_REGISTRY.items()
        if spec.trust_class.value == "fixed_public_service"
    }
    assert set(FIXED_ORIGIN_FIXTURES) == fixed_consumers


@pytest.mark.parametrize("consumer, origins", FIXED_ORIGIN_FIXTURES.items())
def test_fixed_consumer_allows_only_its_complete_literal_origin_set(
    consumer, origins
) -> None:
    spec = CONSUMER_REGISTRY[consumer]
    assert spec.fixed_origins == frozenset(origins)
    method = "GET" if "GET" in spec.allowed_methods else "POST"
    with safe_client(
        consumer,
        security=OutboundSecurityConfig(resolver=lambda *_args: ("93.184.216.34",)),
    ) as client:
        for origin in origins:
            assert (
                client._transport._evaluate(method, origin + "/literal").origin
                == origin
            )
        with pytest.raises(Exception, match="FIXED_ORIGIN_MISMATCH"):
            client.request(method, "https://not-declared.example.test/literal")


@pytest.mark.parametrize("consumer, expected", COMPATIBILITY_FIXTURES.items())
def test_literal_fixture_matches_the_real_policy_boundary(consumer, expected) -> None:
    (
        trust_class,
        method,
        origin,
        port,
        schemes,
        methods,
        ports,
        redirect_policy,
        redirects,
        body_cap,
        mime_prefixes,
        credential_policy,
        owner_exceptions,
        sdk_disposition,
    ) = expected
    spec = CONSUMER_REGISTRY[consumer]

    assert spec.trust_class.value == trust_class
    assert spec.allowed_schemes == frozenset(schemes)
    assert spec.allowed_methods == frozenset(methods)
    assert spec.allowed_ports == (None if ports is None else frozenset(ports))
    assert spec.redirect_policy == redirect_policy
    assert spec.max_redirects == redirects
    assert spec.max_decoded_body_bytes == body_cap
    assert spec.accepted_mime_prefixes == mime_prefixes
    assert spec.credential_origin_policy == credential_policy
    assert spec.allow_owner_exceptions is owner_exceptions
    assert (
        None if spec.sdk_disposition is None else spec.sdk_disposition.value
    ) == sdk_disposition

    kwargs = {}
    if trust_class == "configured_service":
        kwargs["configured_origin"] = origin
    if trust_class == "loopback_callback":
        kwargs["callback_origin"] = origin
    resolver = lambda _host, _port: (
        ("127.0.0.1",) if trust_class == "loopback_callback" else ("93.184.216.34",)
    )
    with safe_client(
        consumer, security=OutboundSecurityConfig(resolver=resolver), **kwargs
    ) as client:
        decision = client._transport._evaluate(method, origin + "/compatibility")

    assert decision.origin == origin
    assert decision.port == port


def _literal_reachable_scheme_port_urls(
    consumer: str, expected: tuple[object, ...]
) -> tuple[tuple[str, int], ...]:
    trust_class, _method, origin, _port, schemes, _methods, ports, *_rest = expected
    literal_origins = (
        FIXED_ORIGIN_FIXTURES[consumer]
        if trust_class == "fixed_public_service"
        else (origin,)
    )
    urls: list[tuple[str, int]] = []
    for literal_origin in literal_origins:
        parsed = urlsplit(literal_origin)
        hostname = parsed.hostname
        assert hostname is not None
        for scheme in schemes:
            default_port = 443 if scheme == "https" else 80
            if trust_class == "fixed_public_service":
                if parsed.scheme != scheme:
                    continue
                reachable_ports = (parsed.port or default_port,)
            elif ports is None:
                reachable_ports = (parsed.port or default_port,)
            else:
                reachable_ports = ports
            for port in reachable_ports:
                netloc = (
                    f"[{hostname}]:{port}" if ":" in hostname else f"{hostname}:{port}"
                )
                urls.append((urlunsplit((scheme, netloc, "/allowed", "", "")), port))
    return tuple(urls)


@pytest.mark.parametrize("consumer, expected", COMPATIBILITY_FIXTURES.items())
def test_every_literal_allowed_scheme_and_port_reaches_policy_boundary(
    consumer, expected
) -> None:
    (
        trust_class,
        method,
        _origin,
        _port,
        _schemes,
        _methods,
        ports,
        *_rest,
    ) = expected
    resolver_calls = []

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolver_calls.append((hostname, port))
        return (
            ("127.0.0.1",) if trust_class == "loopback_callback" else ("93.184.216.34",)
        )

    for url, expected_port in _literal_reachable_scheme_port_urls(consumer, expected):
        kwargs = {}
        if trust_class == "configured_service":
            kwargs["configured_origin"] = urlunsplit(
                urlsplit(url)._replace(path="", query="", fragment="")
            )
        if trust_class == "loopback_callback":
            kwargs["callback_origin"] = urlunsplit(
                urlsplit(url)._replace(path="", query="", fragment="")
            )
        with safe_client(
            consumer, security=OutboundSecurityConfig(resolver=resolver), **kwargs
        ) as client:
            decision = client._transport._evaluate(method, url)
        assert decision.port == expected_port

    original = urlsplit(expected[2])
    calls_before_rejections = len(resolver_calls)
    kwargs = {}
    if trust_class == "configured_service":
        kwargs["configured_origin"] = expected[2]
    if trust_class == "loopback_callback":
        kwargs["callback_origin"] = expected[2]
    with safe_client(
        consumer, security=OutboundSecurityConfig(resolver=resolver), **kwargs
    ) as client:
        with pytest.raises(URLPolicyError):
            client._transport._evaluate(
                method,
                urlunsplit(("ftp", original.netloc, "/blocked", "", "")),
            )
        if ports is not None:
            host = original.hostname or "invalid"
            netloc = f"[{host}]:65535" if ":" in host else f"{host}:65535"
            with pytest.raises(URLPolicyError):
                client._transport._evaluate(
                    method,
                    urlunsplit((original.scheme, netloc, "/blocked", "", "")),
                )
    assert len(resolver_calls) == calls_before_rejections


@pytest.mark.parametrize("consumer, expected", COMPATIBILITY_FIXTURES.items())
def test_each_literal_row_rejects_unapproved_method_scheme_and_origin_port(
    consumer, expected
) -> None:
    trust_class, _method, origin, _port, *_rest = expected
    parsed = urlsplit(origin)
    kwargs = {}
    if trust_class == "configured_service":
        kwargs["configured_origin"] = origin
    if trust_class == "loopback_callback":
        kwargs["callback_origin"] = origin
    resolver = lambda _host, _port: (
        ("127.0.0.1",) if trust_class == "loopback_callback" else ("93.184.216.34",)
    )
    host = parsed.hostname or "invalid"
    netloc = f"[{host}]:65535" if ":" in host else f"{host}:65535"
    wrong_scheme = urlunsplit(("ftp", parsed.netloc, "/blocked", "", ""))
    wrong_port = urlunsplit((parsed.scheme, netloc, "/blocked", "", ""))

    with safe_client(
        consumer, security=OutboundSecurityConfig(resolver=resolver), **kwargs
    ) as client:
        with pytest.raises(URLPolicyError):
            client.request("DELETE", origin + "/blocked")
        with pytest.raises(URLPolicyError):
            client.get(wrong_scheme)
        with pytest.raises(URLPolicyError):
            client.get(wrong_port)


class _CompatibilityHandler(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, str]]] = []

    def _reply(self) -> None:
        type(self).requests.append((self.command, self.path, dict(self.headers)))
        path = self.path.split("?", 1)[0]
        if path == "/redirect":
            self.send_response(int(self.headers.get("X-Compatibility-Redirect", "302")))
            self.send_header(
                "Location",
                "http://127.0.0.1:1/forbidden"
                if self.path.endswith("?cross")
                else "/ok",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"12345" if path == "/over-cap" else b"ok"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "model/invalid"
            if path == "/wrong-mime"
            else self.headers.get("X-Compatibility-Mime", "text/plain"),
        )
        if path != "/over-cap":
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _reply
    do_HEAD = _reply
    do_POST = _reply
    do_PATCH = _reply

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def compatibility_server():
    _CompatibilityHandler.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CompatibilityHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _PublicReportedStream(httpcore.NetworkStream):
    def __init__(self, stream: httpcore.NetworkStream, peer: str):
        self._stream = stream
        self._peer = peer

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(self, *args, **kwargs):
        return self._stream.start_tls(*args, **kwargs)

    def get_extra_info(self, info: str):
        if info == "server_addr":
            return (self._peer, 80)
        return self._stream.get_extra_info(info)


class _PublicLoopbackBackend(httpcore.NetworkBackend):
    def __init__(self, peer: str):
        self._peer = peer
        self._backend = httpcore.SyncBackend()

    def connect_tcp(self, _host, port, **kwargs):
        stream = self._backend.connect_tcp("127.0.0.1", port, **kwargs)
        return _PublicReportedStream(stream, self._peer)

    def connect_unix_socket(self, *args, **kwargs):
        return self._backend.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


def _literal_accepted_mime(prefixes: tuple[str, ...]) -> str:
    for prefix, mime in (
        ("application/octet-stream", "application/octet-stream"),
        ("application/", "application/json"),
        ("audio/", "audio/mpeg"),
        ("image/", "image/png"),
        ("text/", "text/plain"),
        ("video/", "video/mp4"),
    ):
        if prefix in prefixes:
            return mime
    raise AssertionError(f"no accepted MIME fixture for {prefixes!r}")


@pytest.mark.parametrize("consumer, expected", COMPATIBILITY_FIXTURES.items())
def test_every_literal_row_enforces_its_socket_contract(
    monkeypatch, compatibility_server, consumer, expected
) -> None:
    (
        trust_class,
        _selected_method,
        _selected_origin,
        _selected_port,
        _schemes,
        methods,
        _ports,
        redirect_policy,
        _redirects,
        _body_cap,
        mime_prefixes,
        credential_policy,
        owner_exceptions,
        _sdk_disposition,
    ) = expected
    port = compatibility_server.server_address[1]
    is_configured = trust_class == "configured_service"
    is_callback = trust_class == "loopback_callback"
    hostname = "127.0.0.1" if is_configured or is_callback else "public.example.test"
    origin = f"http://{hostname}:{port}"
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry[consumer]
    registry[consumer] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        fixed_origins=frozenset({origin})
        if trust_class == "fixed_public_service"
        else frozenset(),
        max_decoded_body_bytes=4,
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    if not (is_configured or is_callback):
        original = safe_http.DecisionNetworkBackend
        monkeypatch.setattr(
            safe_http,
            "DecisionNetworkBackend",
            lambda decision: original(
                decision,
                underlying=_PublicLoopbackBackend(str(decision.resolved_ips[0])),
            ),
        )
    exception = (
        OwnerURLException(consumer=consumer, origin=origin)
        if owner_exceptions
        else None
    )
    security = OutboundSecurityConfig(
        resolver=lambda *_args: (
            ("127.0.0.1",) if (is_configured or is_callback) else ("93.184.216.34",)
        ),
        owner_exceptions=(() if exception is None else (exception,)),
    )
    kwargs = {}
    if is_configured:
        kwargs["configured_origin"] = origin
    if is_callback:
        kwargs["callback_origin"] = origin
    headers = {
        "Authorization": "Bearer row-secret",
        "X-Compatibility-Mime": _literal_accepted_mime(mime_prefixes),
        "X-Compatibility-Redirect": "302" if "GET" in methods else "307",
    }
    first_method = methods[0]
    with safe_client(consumer, security=security, **kwargs) as client:
        for method in methods:
            assert (
                client.request(method, origin + "/ok", headers=headers).status_code
                == 200
            )
        if redirect_policy == "deny":
            with pytest.raises(URLPolicyError, match="REDIRECT_FORBIDDEN"):
                client.request(first_method, origin + "/redirect", headers=headers)
        else:
            response = client.request(
                first_method, origin + "/redirect", headers=headers
            )
            assert len(response.history) == 1
        with pytest.raises(URLPolicyError, match="MIME_TYPE_FORBIDDEN"):
            client.request(first_method, origin + "/wrong-mime", headers=headers)
        with pytest.raises(URLPolicyError, match="BODY_TOO_LARGE"):
            client.request(first_method, origin + "/over-cap", headers=headers)

    credential_request = next(
        request
        for request in compatibility_server.RequestHandlerClass.requests
        if request[1] == "/ok"
    )
    if credential_policy == "none":
        assert "Authorization" not in credential_request[2]
    else:
        assert credential_request[2]["Authorization"] == "Bearer row-secret"
    if owner_exceptions:
        no_exception_security = replace(security, owner_exceptions=())
        with safe_client(consumer, security=no_exception_security, **kwargs) as client:
            with pytest.raises(URLPolicyError):
                client.request(
                    first_method, origin + "/owner-required", headers=headers
                )


def test_public_client_strips_credentials_before_a_real_local_socket_send(
    monkeypatch, compatibility_server
) -> None:
    port = compatibility_server.server_address[1]
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["tool.web_fetch"] = replace(
        registry["tool.web_fetch"], allowed_ports=frozenset({port})
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    original = safe_http.DecisionNetworkBackend
    monkeypatch.setattr(
        safe_http,
        "DecisionNetworkBackend",
        lambda decision: original(
            decision,
            underlying=_PublicLoopbackBackend(str(decision.resolved_ips[0])),
        ),
    )
    with safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(resolver=lambda *_args: ("93.184.216.34",)),
    ) as client:
        assert (
            client.get(
                f"http://public.example.test:{port}/credential",
                headers={"Authorization": "Bearer never-send"},
            ).content
            == b"ok"
        )

    assert (
        "Authorization" not in compatibility_server.RequestHandlerClass.requests[0][2]
    )


def test_configured_client_rejects_wrong_mime_from_a_real_local_socket(
    compatibility_server,
) -> None:
    origin = f"http://127.0.0.1:{compatibility_server.server_address[1]}"
    with (
        configured_safe_client(
            "provider.configured_api",
            origin,
            owner_exception=OwnerURLException(
                consumer="provider.configured_api", origin=origin
            ),
        ) as client,
        pytest.raises(URLPolicyError, match="MIME_TYPE_FORBIDDEN"),
    ):
        client.get(origin + "/wrong-mime")


def test_configured_client_enforces_its_decoded_body_cap_on_a_real_socket(
    monkeypatch, compatibility_server
) -> None:
    origin = f"http://127.0.0.1:{compatibility_server.server_address[1]}"
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["provider.configured_api"] = replace(
        registry["provider.configured_api"], max_decoded_body_bytes=4
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    with (
        configured_safe_client(
            "provider.configured_api",
            origin,
            owner_exception=OwnerURLException(
                consumer="provider.configured_api", origin=origin
            ),
        ) as client,
        pytest.raises(URLPolicyError, match="BODY_TOO_LARGE"),
    ):
        client.get(origin + "/over-cap")


def test_configured_client_rejects_cross_origin_redirect_before_a_second_send(
    compatibility_server,
) -> None:
    origin = f"http://127.0.0.1:{compatibility_server.server_address[1]}"
    with (
        configured_safe_client(
            "provider.configured_api",
            origin,
            owner_exception=OwnerURLException(
                consumer="provider.configured_api", origin=origin
            ),
        ) as client,
        pytest.raises(URLPolicyError, match="REDIRECT_ORIGIN_FORBIDDEN"),
    ):
        client.get(
            origin + "/redirect?cross", headers={"Authorization": "Bearer local"}
        )

    assert len(compatibility_server.RequestHandlerClass.requests) == 1
    assert (
        compatibility_server.RequestHandlerClass.requests[0][2]["Authorization"]
        == "Bearer local"
    )


def test_configured_private_origin_requires_its_own_literal_owner_exception(
    compatibility_server,
) -> None:
    origin = f"http://127.0.0.1:{compatibility_server.server_address[1]}"
    with (
        safe_client(
            "provider.configured_api",
            configured_origin=origin,
            security=OutboundSecurityConfig(resolver=lambda *_args: ("127.0.0.1",)),
        ) as client,
        pytest.raises(URLPolicyError),
    ):
        client.get(origin + "/owner-exception")

    assert compatibility_server.RequestHandlerClass.requests == []


def test_sdk_dispositions_are_enforced_by_the_runtime_inventory() -> None:
    for consumer, expected in COMPATIBILITY_FIXTURES.items():
        disposition = expected[-1]
        if disposition == "disabled":
            with pytest.raises(URLPolicyError, match="UNMANAGED_TRANSPORT"):
                require_active_sdk_transport(consumer, expected[2])
        elif disposition is not None:
            require_active_sdk_transport(consumer, expected[2])

    result = subprocess.run(
        [sys.executable, "scripts/check_runtime_http.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "active_unmanaged=0" in result.stdout
