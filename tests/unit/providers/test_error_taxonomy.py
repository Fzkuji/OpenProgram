"""``errors.taxonomy_fields`` + AssistantMessage error fields — the backend
half of error-taxonomy propagation
(docs/design/providers/reliability/error-taxonomy-propagation.md).

The agent error boundary calls taxonomy_fields(exc) to populate the structured
error on the AssistantMessage, so surfaces above can tell a retryable
rate-limit from a fatal auth/context failure.
"""
from __future__ import annotations

from openprogram.providers.utils.errors import (
    ErrorReason,
    LLMError,
    taxonomy_fields,
)
from openprogram.providers.types import AssistantMessage


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
    assert {"error_reason", "error_retryable", "error_retry_after_s"} <= fields
    # all optional with a None default
    for f in ("error_reason", "error_retryable", "error_retry_after_s"):
        assert AssistantMessage.model_fields[f].default is None
