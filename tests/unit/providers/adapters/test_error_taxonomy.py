"""``errors.taxonomy_fields`` + AssistantMessage error fields — the backend
half of error-taxonomy propagation
(docs/design/providers/reliability/error-taxonomy-propagation.md).

The agent error boundary calls taxonomy_fields(exc) to populate the structured
error on the AssistantMessage, so surfaces above can tell a retryable
rate-limit from a fatal auth/context failure.
"""
from __future__ import annotations

from openprogram.models.providers.utils.errors import (
    ErrorReason,
    LLMError,
    taxonomy_fields,
)
from openprogram.models.providers.types import AssistantMessage


def test_taxonomy_fields_llm_error_passthrough():
    e = LLMError(
        message="rate limited",
        reason=ErrorReason.RATE_LIMIT,
        retryable=True,
        retry_after_s=30.0,
    )
    assert taxonomy_fields(e) == ("rate_limit", True, 30.0)


def test_taxonomy_fields_llm_error_fatal_auth():
    e = LLMError(message="bad key", reason=ErrorReason.AUTHENTICATION, retryable=False)
    reason, retryable, retry_after = taxonomy_fields(e)
    assert reason == ErrorReason.AUTHENTICATION.value and retryable is False and retry_after is None


def test_taxonomy_fields_generic_exception_is_classified():
    reason, retryable, retry_after = taxonomy_fields(ValueError("something went wrong"))
    # a non-LLMError gets classified; shape is always (str reason, bool, None)
    assert reason in {r.value for r in ErrorReason}
    assert isinstance(retryable, bool)
    assert retry_after is None  # only an LLMError carries a server retry hint


def test_assistant_message_has_error_taxonomy_fields():
    fields = set(AssistantMessage.model_fields)
    assert {
        "error_reason",
        "error_retryable",
        "error_retry_after_s",
        "error_transport_exhausted",
    } <= fields
    # all optional with a None default
    for f in (
        "error_reason",
        "error_retryable",
        "error_retry_after_s",
        "error_transport_exhausted",
    ):
        assert AssistantMessage.model_fields[f].default is None


def test_sdk_connection_errors_are_retryable_transport():
    """The openai/anthropic SDKs report a dropped network as
    APIConnectionError("Connection error.") caused by the httpx error."""
    from openprogram.models.providers.utils.errors import classify_error

    class APIConnectionError(Exception):
        pass

    class ConnectError(Exception):
        pass

    assert classify_error(APIConnectionError("Connection error.")) == (ErrorReason.TRANSPORT, True)
    try:
        try:
            raise ConnectError("nodename nor servname provided")
        except ConnectError as cause:
            raise RuntimeError("Connection error.") from cause
    except RuntimeError as wrapped:
        assert classify_error(wrapped) == (ErrorReason.TRANSPORT, True)


def test_error_kind_names_a_stalled_stream_whichever_layer_gave_up():
    """A stalled stream and a reset connection share the ``transport``
    reason; ``error_kind`` tells them apart, through any wrapping."""
    from openprogram.models.providers.adapters.openai_codex.read_budget import StreamTotalTimeout
    from openprogram.models.providers.utils.errors import error_kind
    from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout

    idle = StreamIdleTimeout("no response event for 120s")
    assert error_kind(idle) == "stream_idle"
    assert error_kind(StreamTotalTimeout("deadline")) == "stream_total"
    wrapped = LLMError(message="gave up", reason=ErrorReason.TRANSPORT, cause=idle)
    assert error_kind(wrapped) == "stream_idle"
    try:
        try:
            raise idle
        except StreamIdleTimeout as inner:
            raise RuntimeError("turn failed") from inner
    except RuntimeError as outer:
        assert error_kind(outer) == "stream_idle"
    named = LLMError(message="gave up", reason=ErrorReason.TRANSPORT,
                     last_error_type="StreamIdleTimeout")
    assert error_kind(named) == "stream_idle"
    assert error_kind(ConnectionResetError("reset")) is None


def test_error_kind_names_memory_and_auth_failures():
    from openprogram.models.providers.utils.errors import error_kind
    from openprogram.state.memory.policy import MemoryPolicyError

    assert error_kind(MemoryPolicyError("memory is required")) == "memory_unavailable"
    assert error_kind(LLMError(message="bad key", reason=ErrorReason.AUTHENTICATION)) == "auth"
    assert error_kind(LLMError(message="slow down", reason=ErrorReason.RATE_LIMIT)) is None
