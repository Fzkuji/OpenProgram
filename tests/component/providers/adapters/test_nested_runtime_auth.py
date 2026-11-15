"""Nested Runtime authentication and terminal errors through public entries."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openprogram.runtime.agent.runtime import Runtime
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.models.providers.types import Model
from openprogram.models.providers.utils.errors import ErrorReason, LLMError
from openprogram.models.providers.utils.stream_retry import ProviderStreamError


@pytest.mark.parametrize("profile", [None, "pinned-account"])
def test_codex_runtime_uses_provider_active_account_unless_explicit(monkeypatch, profile):
    from openprogram.models.auth.account import account_selection
    from openprogram.models.providers.catalog import enabled_models, models
    from openprogram.runtime.agent.runtime import factory
    from openprogram.runtime.agent.provider_runtimes import openai_codex as codex

    monkeypatch.setattr(account_selection, "_read", lambda: {"openai-codex": "active-account"})
    calls = []
    def acquire(provider, account):
        calls.append((provider, account))
        return Credential(provider_id=provider, account_id=account, kind="oauth",
                          payload=CredentialData(kind="oauth", auth_value="fake-token"),
                          metadata={"account_id": "fake-chatgpt-account"})
    monkeypatch.setattr(codex, "get_credential_provider", lambda: SimpleNamespace(acquire_sync=acquire))
    monkeypatch.setattr(codex, "codex_client_version", lambda: "0.153.4")
    model = Model(id="test-model", name="Test", provider="openai-codex", api="openai-codex",
                  base_url="https://example.invalid")
    monkeypatch.setattr(enabled_models, "ENABLED_MODELS", {"openai-codex/test-model": model})
    monkeypatch.setattr(models, "ENABLED_MODELS", {"openai-codex/test-model": model})
    with factory.create_runtime(provider="openai-codex", model="test-model", profile=profile) as runtime:
        assert calls == [("openai-codex", profile or "active-account")]
        assert runtime.api_model.headers["chatgpt-account-id"] == "fake-chatgpt-account"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("reason,status", [(ErrorReason.AUTHENTICATION, 401),
                                         (ErrorReason.AUTHORIZATION, 403),
                                         (ErrorReason.UNKNOWN, None)])
def test_runtime_preserves_nonretryable_provider_verdict(monkeypatch, asynchronous, reason, status):
    from openprogram.models.providers.utils.event_stream import EventStream
    from openprogram.runtime.agent.runtime import execution

    monkeypatch.setattr(execution, "_retry_sleep_seconds", lambda *_: 0)
    calls = []
    failure = ProviderStreamError("token_revoked" if status else "opaque terminal failure",
                                  retryable=False, http_status=status, retry_after_s=30)
    def stream(*_args, **_kwargs):
        calls.append(True)
        events = EventStream()
        events.fail(failure)
        return events
    with Runtime(model="default", max_retries=3) as runtime:
        runtime.api_model = Model(id="test-model", name="Test", provider="openai",
                                  api="openai-completions", base_url="https://example.invalid")
        runtime.model = "openai:test-model"
        runtime.provider_id = "openai"
        runtime._stream_fn = stream
        with pytest.raises(LLMError) as caught:
            if asynchronous:
                asyncio.run(runtime.async_exec(content="test"))
            else:
                runtime.exec(content="test", tools=[], max_iterations=1)
        assert len(calls) == 1
        assert caught.value.reason is reason
        assert caught.value.retryable is False
        assert caught.value.attempts == 1
        assert caught.value.retry_after_s == 30


@pytest.mark.parametrize("asynchronous", [False, True])
def test_runtime_retains_transient_provider_recovery(monkeypatch, asynchronous):
    from openprogram.models.providers.types import AssistantMessage, EventStart, EventDone, TextContent
    from openprogram.runtime.agent.loop import session

    monkeypatch.setattr(session, "compute_backoff_ms", lambda *_: 0)
    calls = []
    async def stream(model, *_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ProviderStreamError("temporary upstream failure", http_status=503, retryable=True)
        response = AssistantMessage(content=[TextContent(text="recovered")], api=model.api,
                                    provider=model.provider, model=model.id, timestamp=1)
        yield EventStart(partial=response)
        yield EventDone(reason="stop", message=response)
    with Runtime(model="default", max_retries=3) as runtime:
        runtime.api_model = Model(id="test-model", name="Test", provider="openai",
                                  api="openai-completions", base_url="https://example.invalid")
        runtime.model = "openai:test-model"
        runtime.provider_id = "openai"
        runtime._stream_fn = stream
        result = (asyncio.run(runtime.async_exec(content="test")) if asynchronous
                  else runtime.exec(content="test", tools=[], max_iterations=1))
        assert result == "recovered"
        assert len(calls) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("declared", [None, False, True])
def test_runtime_distinguishes_opaque_inferred_and_declared_retry_verdict(monkeypatch, asynchronous, declared):
    from openprogram.models.providers.types import AssistantMessage, EventDone, EventStart, TextContent
    from openprogram.runtime.agent.runtime import execution

    monkeypatch.setattr(execution, "_retry_sleep_seconds", lambda *_: 0)
    calls = []
    failure = RuntimeError("opaque callback failure")
    if declared is not None:
        failure.retryable = declared
    async def stream(model, *_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise failure
        response = AssistantMessage(content=[TextContent(text="recovered")], api=model.api,
                                    provider=model.provider, model=model.id, timestamp=1)
        yield EventStart(partial=response)
        yield EventDone(reason="stop", message=response)
    with Runtime(model="default", max_retries=3) as runtime:
        runtime.api_model = Model(id="test-model", name="Test", provider="openai",
                                  api="openai-completions", base_url="https://example.invalid")
        runtime.model = "openai:test-model"
        runtime.provider_id = "openai"
        runtime._stream_fn = stream
        def execute():
            return (asyncio.run(runtime.async_exec(content="test")) if asynchronous
                    else runtime.exec(content="test", tools=[], max_iterations=1))
        if declared is False:
            with pytest.raises(LLMError) as caught:
                execute()
            assert caught.value.reason is ErrorReason.UNKNOWN
            assert caught.value.retryable is False
            assert caught.value.attempts == 1
        else:
            assert execute() == "recovered"
        assert len(calls) == (1 if declared is False else 2)


@pytest.mark.parametrize("declared", [False, True])
def test_agent_error_provenance_survives_message_and_event_serialization(declared):
    from openprogram.runtime.agent.loop.messages import convert_to_llm
    from openprogram.runtime.agent.loop.session import AgentSession
    from openprogram.runtime.agent.loop.types import AgentEventAgentEnd
    from openprogram.models.providers.types import AssistantMessage

    failure = RuntimeError("opaque callback failure")
    if declared:
        failure.retryable = False
    async def stream(*_args, **_kwargs):
        raise failure
        yield  # Async generator without a provider event.
    model = Model(id="test-model", name="Test", provider="openai",
                  api="openai-completions", base_url="https://example.invalid")
    events = []
    with AgentSession(model=model, stream_fn=stream) as session:
        unsubscribe = session.agent.subscribe(events.append)
        try:
            final = asyncio.run(session.run("test"))
        finally:
            unsubscribe()
        assert final.error_reason == "unknown"
        assert final.error_retryable is False
        assert final.error_retryable_inferred is (not declared)
        converted = convert_to_llm([final])[0]
        restored = AssistantMessage.model_validate_json(final.model_dump_json())
        assert restored.error_retryable_inferred is (not declared)
        assert converted["error_retryable_inferred"] is (not declared)
        event = next(event for event in events if event.type == "agent_end")
        restored_event = AgentEventAgentEnd.model_validate_json(event.model_dump_json())
        assert restored_event.messages[-1].error_retryable_inferred is (not declared)
        legacy = final.model_dump()
        legacy.pop("error_retryable_inferred")
        assert AssistantMessage.model_validate(legacy).error_retryable_inferred is False
