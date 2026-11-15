"""What one Agent turn on a candidate can and cannot say about it."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.models.providers.utils.errors import LLMError
from openprogram.operations.self_update.control.turn_check import _classify
from openprogram.runtime.conversation.model_tools import _raise_model_unavailable


def _failed(error: str, reason: str, retryable: bool = False) -> SimpleNamespace:
    return SimpleNamespace(failed=True, error=error, error_reason=reason, error_retryable=retryable, tool_calls=[])


def test_a_machine_with_no_model_chosen_says_nothing_about_the_new_version() -> None:
    with pytest.raises(LLMError) as raised:
        _raise_model_unavailable(None)

    verdict = _classify(_failed(f"{raised.value.message} [reason=invalid]", "invalid"))

    assert verdict["outcome"] == "inconclusive"
    assert "No model is configured" in verdict["error"]


def test_a_turn_the_new_version_broke_still_fails_the_check() -> None:
    assert _classify(_failed("the tool result was lost", "invalid"))["outcome"] == "failed"


def test_a_provider_outage_is_not_held_against_the_new_version() -> None:
    assert _classify(_failed("upstream 503", "provider", retryable=True))["outcome"] == "inconclusive"
