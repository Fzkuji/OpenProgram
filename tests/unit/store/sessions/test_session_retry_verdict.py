"""The session retry gate honors the provider layer's structured verdict.

Every provider error reaches AgentSession as an assistant message carrying
``error_retryable`` from the error taxonomy (agent.py fills it via
``taxonomy_fields``). ``is_retryable_error`` must obey that verdict before
falling back to text matching — otherwise a definitive 401 "credits
exhausted" whose text happens to hit the regex (or got stripped to an empty
message) rides the whole backoff ladder: observed as 6 attempts / 40+
seconds of waiting on a failure that can only repeat.
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.agent.retry import is_retryable_error


def _msg(
    error_message: str,
    error_retryable=None,
    *,
    error_transport_exhausted: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="error",
        error_message=error_message,
        error_retryable=error_retryable,
        error_transport_exhausted=error_transport_exhausted,
    )


def test_verdict_false_blocks_retry_even_when_text_matches_regex() -> None:
    # "401" isn't in the regex but "retry delay" style texts are — use a
    # message that DOES match to prove the verdict wins over the text.
    msg = _msg("LLMError: 429 Too Many Requests (insufficient credits)",
               error_retryable=False)
    assert is_retryable_error(msg) is False


def test_verdict_false_blocks_retry_on_empty_message() -> None:
    # Empty text alone means "stream dropped, retry" — but not when the
    # taxonomy already ruled the failure permanent.
    assert is_retryable_error(_msg("", error_retryable=False)) is False


def test_verdict_true_retries_even_when_text_evades_regex() -> None:
    msg = _msg("LLMError: upstream hiccup of a novel phrasing",
               error_retryable=True)
    assert is_retryable_error(msg) is True


def test_exhausted_provider_budget_blocks_session_retry() -> None:
    msg = _msg(
        "ProviderStreamError: connection terminated",
        error_retryable=True,
        error_transport_exhausted=True,
    )
    assert is_retryable_error(msg) is False


def test_no_verdict_falls_back_to_text_matching() -> None:
    assert is_retryable_error(_msg("503 Service Unavailable")) is True
    assert is_retryable_error(_msg("Invalid API key provided")) is False
    # Content-free error without a verdict still retries (stream drop).
    assert is_retryable_error(_msg("")) is True


def test_non_error_stop_reason_never_retries() -> None:
    msg = SimpleNamespace(stop_reason="stop", error_message="",
                          error_retryable=True)
    assert is_retryable_error(msg) is False
