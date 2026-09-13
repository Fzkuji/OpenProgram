from __future__ import annotations

import asyncio
import importlib

import pytest

from openprogram.agent.types import AgentContext, AgentLoopConfig
from openprogram.providers.api_registry import ApiProviderSnapshot
from openprogram.providers.structured_output import StructuredOutputCapabilities
from openprogram.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
)


@pytest.mark.parametrize(
    ("primary_support", "fallback_support", "failover"),
    [
        (True, False, True),
        (False, True, True),
        (True, True, True),
        (True, False, False),
    ],
)
def test_provider_options_follow_dispatch_candidate_capabilities(
    monkeypatch, primary_support, fallback_support, failover
):
    agent_loop = importlib.import_module("openprogram.agent.agent_loop")
    provider_stream = importlib.import_module("openprogram.providers.stream")
    failover_utils = importlib.import_module("openprogram.providers.utils.failover")
    api_registry = importlib.import_module("openprogram.providers.api_registry")

    primary = Model(
        id="primary-model", name="Primary", api="primary-api",
        provider="primary", base_url="https://primary.invalid",
    )
    fallback = Model(
        id="fallback-model", name="Fallback", api="fallback-api",
        provider="fallback", base_url="https://fallback.invalid",
    )
    snapshots = {
        id(primary): ApiProviderSnapshot(
            object(), StructuredOutputCapabilities(), primary_support,
        ),
        id(fallback): ApiProviderSnapshot(
            object(), StructuredOutputCapabilities(), fallback_support,
        ),
    }
    monkeypatch.setattr(
        api_registry, "resolve_api_provider_snapshot",
        lambda model: snapshots[id(model)],
    )
    monkeypatch.setattr(
        failover_utils,
        "resolve_fallback_models",
        lambda _model: [fallback] if failover else [],
    )

    calls = []

    async def fake_stream(provider, model, context, options, get_api_key=None):
        calls.append((model.provider, options.supports_idempotency_key, options.idempotency_key))
        if model is primary and failover:
            raise RuntimeError("internal server error")
        message = AssistantMessage(
            content=[TextContent(text="ok")], api=model.api,
            provider=model.provider, model=model.id, timestamp=1,
        )
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)

    monkeypatch.setattr(provider_stream, "stream_simple_with_provider", fake_stream)
    before_payloads = []

    async def safe_point(kind, payload):
        if kind == "provider.before":
            before_payloads.append(dict(payload))
            payload["idempotency_key"] = "stable-key"

    config = AgentLoopConfig(
        model=primary,
        convert_to_llm=lambda messages: messages,
        safe_point_hook=safe_point,
    )
    message = asyncio.run(
        agent_loop._stream_assistant_response(
            AgentContext(messages=[], tools=[]), config, None,
            agent_loop._create_agent_stream(), None, None, snapshots[id(primary)],
        )
    )

    assert message.provider == ("fallback" if failover else "primary")
    assert len(before_payloads) == 1
    payload = before_payloads[0]
    expected_support = (
        primary_support and fallback_support if failover else primary_support
    )
    assert payload["supports_idempotency_key"] is expected_support
    assert [item["provider"] for item in payload["dispatch_candidates"]] == (
        ["primary", "fallback"] if failover else ["primary"]
    )
    assert calls == (
        [
            ("primary", expected_support, "stable-key" if expected_support else None),
            ("fallback", expected_support, "stable-key" if expected_support else None),
        ]
        if failover
        else [("primary", expected_support, "stable-key" if expected_support else None)]
    )
