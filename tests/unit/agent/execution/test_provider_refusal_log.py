"""A provider's own stated refusal is one warning line; anything else keeps its traceback."""

from openprogram.models.providers.utils.errors import (
    ErrorReason,
    LLMError,
    failure_cause,
    taxonomy_fields,
)
from openprogram.runtime.agent.loop.driver import _provider_refusal


def _refusal(exc: BaseException) -> str | None:
    reason, _retryable, _after = taxonomy_fields(exc)
    return _provider_refusal(reason, failure_cause(exc))


def test_no_credit_is_a_refusal():
    err = LLMError("insufficient_quota: You exceeded your current quota", http_status=402)
    assert _refusal(err) == "no credit or quota left"


def test_rejected_login_is_a_refusal():
    err = LLMError("Unauthorized", reason=ErrorReason.AUTHENTICATION, http_status=401)
    assert _refusal(err) == "credential rejected"


def test_authentication_reason_without_a_status_is_a_refusal():
    err = LLMError("sign in again", reason=ErrorReason.AUTHENTICATION)
    assert _refusal(err) == "authentication"


def test_unexpected_exception_is_not_a_refusal():
    assert _refusal(RuntimeError("boom")) is None
    assert _refusal(KeyError("content")) is None


def test_transient_failures_are_not_refusals():
    assert _provider_refusal("transport", None) is None
    assert _provider_refusal("rate_limit", None) is None
    assert _provider_refusal("transport", {"cause": "unreachable"}) is None
