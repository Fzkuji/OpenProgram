from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from openprogram.security.safe_http import (
    CONSUMER_REGISTRY,
    SDKDisposition,
)
from openprogram.security.url_policy import URLTrustClass


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
    "channel.slack.api",
    "channel.wechat.api",
    "channel.feishu.api",
    "channel.matrix.configured",
    "channel.generated_asset.download",
    "skills.github.catalog",
    "skills.configured.catalog",
    "plugins.marketplace",
    "plugins.autoupdate",
    "updater.github",
    "updater.pip",
    "provider.fixed_api",
    "provider.configured_api",
    "provider.oauth.fixed",
    "provider.google.sdk",
    "provider.openai.sdk",
    "provider.anthropic.sdk",
    "mcp.configured.http",
    "mcp.configured.sse",
    "mcp.loopback.callback",
    "tts.fixed_api",
    "tts.configured_api",
    "webui.mcp.catalog",
    "webui.model_listing.fixed",
    "webui.model_listing.configured",
    "runtime.local_probe",
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


def test_sdk_consumers_have_managed_dispositions():
    expected = {
        "provider.google.sdk",
        "provider.openai.sdk",
        "provider.anthropic.sdk",
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
