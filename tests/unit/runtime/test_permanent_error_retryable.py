"""Regression: exec's _is_permanent_error must honor a provider's explicit
``retryable=False`` verdict, not only string-match the message.

Background: codex's backend intermittently emits an empty
``{"type":"error"}`` SSE event (code/message both null). The shared
Responses parser raises ``RuntimeError("Error Code None: None")``, which
the stream-retry layer wraps as ``ProviderStreamError(retryable=False)``
(provider judged it non-retryable). Before this fix, exec's
``_is_permanent_error`` only string-matched the message — "Error Code
None: None" contains no permanent marker — so exec retried the
provider-declared-permanent failure the full max_retries times, turning
one backend hiccup into a long doomed retry storm that still crashed the
run.
"""
from openprogram.agentic_programming.runtime import _is_permanent_error
from openprogram.providers.utils.stream_retry import ProviderStreamError


def test_retryable_false_is_permanent():
    """Provider said don't retry -> exec treats it as permanent."""
    e = ProviderStreamError("RuntimeError: Error Code None: None",
                            retryable=False)
    assert _is_permanent_error(e) is True


def test_retryable_true_is_not_permanent():
    """A genuinely transient error stays retryable."""
    e = ProviderStreamError("overloaded, try again", retryable=True)
    assert _is_permanent_error(e) is False


def test_plain_exception_falls_back_to_string_match():
    """Exceptions without a retryable flag keep the old marker behavior."""
    assert _is_permanent_error(ValueError("something broke")) is False
    assert _is_permanent_error(RuntimeError("invalid api key")) is True
