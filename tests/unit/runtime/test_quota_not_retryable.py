"""A spent subscription quota must fail immediately, not ride the retry ladder.

Both a per-minute throttle and an exhausted plan arrive as HTTP 429, but they
want opposite handling: the throttle clears in seconds, while a spent quota
resets hours or days out (codex reports ``resets_in_seconds`` in the hundreds
of thousands). Retrying the latter can only fail — it turns an instant error
into a 40-second one and reports "rate limit" when the real answer is "your
plan is out".

Guards both halves so they can't drift apart: ``is_retryable_status`` decides
whether the stream layer retries, ``classify_error`` decides what the user is
told.
"""
from __future__ import annotations

import pytest

from openprogram.providers.utils.errors import ErrorReason, classify_error
from openprogram.providers.utils.stream_retry import is_retryable_status


# Verbatim from a real codex 429 (the body that prompted this).
CODEX_QUOTA = (
    '{"error":{"type":"usage_limit_reached","message":"The usage limit has '
    'been reached","plan_type":"pro","resets_at":1786159984,'
    '"eligible_promo":null,"resets_in_seconds":241439}}'
)

QUOTA_BODIES = [
    CODEX_QUOTA,
    '{"error":{"code":"insufficient_quota","message":"You exceeded your '
    'current quota, please check your plan and billing details."}}',
    '{"type":"error","error":{"type":"invalid_request_error","message":'
    '"Your credit balance is too low to access the Anthropic API."}}',
    '{"error":{"message":"Quota exceeded for this project."}}',
    '{"error":{"code":"billing_hard_limit_reached"}}',
]

# Ordinary throttling — must stay retryable, or a transient blip becomes a
# hard failure.
THROTTLE_BODIES = [
    '{"error":{"message":"Rate limit reached for requests","type":'
    '"rate_limit_error"}}',
    '{"error":{"message":"Too many requests, please slow down."}}',
    "",
]


@pytest.mark.parametrize("body", QUOTA_BODIES)
def test_quota_is_not_retryable(body):
    assert is_retryable_status(429, body) is False


@pytest.mark.parametrize("body", QUOTA_BODIES)
def test_quota_classified_as_plan_problem(body):
    reason, retryable = classify_error(
        Exception(body), http_status=429, error_text=body,
    )
    # AUTHORIZATION is "scope / plan error" — the caller surfaces a plan
    # message rather than scheduling another attempt.
    assert reason is ErrorReason.AUTHORIZATION
    assert retryable is False


@pytest.mark.parametrize("body", THROTTLE_BODIES)
def test_plain_throttling_still_retries(body):
    assert is_retryable_status(429, body) is True
    reason, retryable = classify_error(
        Exception(body), http_status=429, error_text=body,
    )
    assert reason is ErrorReason.RATE_LIMIT
    assert retryable is True


def test_quota_wording_survives_case_and_nesting():
    """The marker is matched against the whole lowered body, so casing and
    JSON nesting don't matter — only that the phrase is present."""
    assert is_retryable_status(429, '{"E":{"T":"USAGE_LIMIT_REACHED"}}') is False


def test_5xx_unaffected_by_quota_check():
    """A quota phrase can't appear in a 500, but the guard runs before the
    status check — make sure an ordinary 500 still retries."""
    assert is_retryable_status(500, "internal server error") is True
