from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from openprogram.security.safe_http import (
    CONSUMER_REGISTRY,
    SDKDisposition,
)
from openprogram.security.url_policy import URLTrustClass, evaluate_url


EXPECTED_CONSUMERS = {
    "tool.web_fetch",
    "tool.web_search.fixed_api",
    "tool.web_search.configured_api",
    "tool.image_api.fixed",
    "tool.image_api.configured",
    "tool.image_result.download",
    "channel.attachment.download",
    "channel.telegram.api",
    "channel.discord.api",
    "channel.discord.gateway_sdk",
    "channel.slack.api",
    "channel.slack.gateway_sdk",
    "channel.slack.attachment",
    "channel.slack.generated_asset.upload",
    "channel.telegram.attachment",
    "channel.wechat.api",
    "channel.feishu.api",
    "channel.matrix.configured",
    "channel.generated_asset.download",
    "skills.github.catalog",
    "skills.configured.catalog",
    "plugins.marketplace",
    "plugins.autoupdate",
    "updater.github",
    "provider.fixed_api",
    "provider.configured_api",
    "provider.oauth.fixed",
    "provider.google.sdk",
    "provider.openai.sdk",
    "provider.anthropic.sdk",
    "provider.amazon_bedrock.sdk",
    "mcp.configured.http",
    "mcp.configured.sse",
    "mcp.loopback.callback",
    "tts.fixed_api",
    "tts.configured_api",
    "tts.edge_sdk",
    "webui.mcp.catalog",
    "webui.model_listing.fixed",
    "webui.model_listing.configured",
    "runtime.local_probe",
}

EXPECTED_FIXED_ORIGINS = {
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
        {"https://clawhub.ai", "https://codeload.github.com", "https://github.com"}
    ),
    "plugins.autoupdate": frozenset({"https://pypi.org", "https://registry.npmjs.org"}),
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
    "provider.amazon_bedrock.sdk": frozenset(
        {
            "https://bedrock.us-east-1.amazonaws.com",
            "https://bedrock-runtime.us-east-1.amazonaws.com",
        }
    ),
    "tts.fixed_api": frozenset({"https://api.elevenlabs.io", "https://api.openai.com"}),
    "webui.model_listing.fixed": frozenset(
        {
            "https://api.anthropic.com",
            "https://generativelanguage.googleapis.com",
            "https://models.dev",
        }
    ),
}


def test_registry_is_complete_and_immutable():
    assert isinstance(CONSUMER_REGISTRY, MappingProxyType)
    assert set(CONSUMER_REGISTRY) == EXPECTED_CONSUMERS
    assert len(CONSUMER_REGISTRY) == len(EXPECTED_CONSUMERS)

    with pytest.raises(TypeError):
        CONSUMER_REGISTRY["tool.web_fetch"] = CONSUMER_REGISTRY["tool.web_fetch"]
    with pytest.raises(FrozenInstanceError):
        CONSUMER_REGISTRY["tool.web_fetch"].max_redirects = 99


def test_every_consumer_declares_finite_limits_and_mime_policy():
    for key, spec in CONSUMER_REGISTRY.items():
        assert spec.consumer == key
        assert spec.allowed_schemes
        assert spec.allowed_schemes <= {"http", "https"}
        assert spec.allowed_methods
        assert all(method == method.upper() for method in spec.allowed_methods)
        assert spec.allowed_ports is None or all(
            0 < port <= 65535 for port in spec.allowed_ports
        )
        assert 0 < spec.max_redirects <= 20
        assert 0 < spec.max_decoded_body_bytes <= 128 * 1024 * 1024
        assert spec.accepted_mime_prefixes
        assert all(
            prefix and prefix == prefix.lower()
            for prefix in spec.accepted_mime_prefixes
        )


def test_redirect_and_credential_policies_are_consistent():
    for spec in CONSUMER_REGISTRY.values():
        assert spec.redirect_policy in {"public", "same_origin", "deny"}
        assert spec.credential_origin_policy in {"none", "same_origin"}
        if spec.credential_origin_policy == "same_origin":
            assert spec.redirect_policy != "public"
        if spec.trust_class == URLTrustClass.LOOPBACK_CALLBACK:
            assert spec.redirect_policy == "deny"
            assert spec.credential_origin_policy == "none"


def test_owner_exceptions_are_restricted_to_declared_configured_consumers():
    exception_consumers = {
        spec.consumer
        for spec in CONSUMER_REGISTRY.values()
        if spec.allow_owner_exceptions
    }
    assert exception_consumers
    for consumer in exception_consumers:
        assert (
            CONSUMER_REGISTRY[consumer].trust_class == URLTrustClass.CONFIGURED_SERVICE
        )


def test_fixed_consumers_declare_only_audited_normalized_origins():
    actual = {
        key: spec.fixed_origins
        for key, spec in CONSUMER_REGISTRY.items()
        if spec.trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE
    }
    assert actual == EXPECTED_FIXED_ORIGINS
    for key, spec in CONSUMER_REGISTRY.items():
        if spec.trust_class != URLTrustClass.FIXED_PUBLIC_SERVICE:
            assert spec.fixed_origins == frozenset(), key
    assert (
        CONSUMER_REGISTRY["channel.wechat.api"].trust_class
        == URLTrustClass.CONFIGURED_SERVICE
    )


@pytest.mark.parametrize(
    ("consumer", "origin"),
    [
        (consumer, origin)
        for consumer, origins in EXPECTED_FIXED_ORIGINS.items()
        for origin in sorted(origins)
    ],
)
def test_each_audited_fixed_origin_is_accepted_by_policy(consumer, origin):
    spec = CONSUMER_REGISTRY[consumer]
    method = "GET" if "GET" in spec.allowed_methods else min(spec.allowed_methods)
    decision = evaluate_url(
        consumer,
        method,
        f"{origin}/resource",
        trust_class=spec.trust_class,
        allowed_schemes=spec.allowed_schemes,
        allowed_methods=spec.allowed_methods,
        allowed_ports=spec.allowed_ports,
        fixed_origins=spec.fixed_origins,
        resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    assert decision.origin == origin


def test_sdk_consumers_have_managed_dispositions():
    expected = {
        "channel.discord.api",
        "channel.discord.gateway_sdk",
        "channel.feishu.api",
        "channel.matrix.configured",
        "channel.slack.api",
        "channel.slack.gateway_sdk",
        "channel.telegram.api",
        "channel.wechat.api",
        "mcp.configured.http",
        "mcp.configured.sse",
        "provider.google.sdk",
        "provider.openai.sdk",
        "provider.anthropic.sdk",
        "provider.amazon_bedrock.sdk",
        "tts.configured_api",
        "tts.edge_sdk",
        "tts.fixed_api",
    }
    sdk_specs = {
        key: spec
        for key, spec in CONSUMER_REGISTRY.items()
        if spec.sdk_disposition is not None
    }
    assert set(sdk_specs) == expected
    assert {spec.sdk_disposition for spec in sdk_specs.values()} <= {
        SDKDisposition.INJECTED_TRANSPORT,
        SDKDisposition.EXACT_ORIGIN,
        SDKDisposition.POLICY_PROXY,
        SDKDisposition.DISABLED,
    }
    assert all(
        spec.sdk_disposition.value != "unmanaged_transport"
        for spec in sdk_specs.values()
    )


def test_image_result_registry_rejects_explicit_non_image_mime() -> None:
    spec = CONSUMER_REGISTRY["tool.image_result.download"]

    assert spec.accepted_mime_prefixes == ("image/", "application/octet-stream")
