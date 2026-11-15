"""What an OpenAI reasoning request carries about reasoning.

With ``store: false`` the server keeps no reasoning, so every request of a
reasoning model asks for it back encrypted, whatever the effort (as Codex
does); and "thinking off" is sent as ``effort: "none"`` where the model
takes it (as Pi and Codex send it), not as no field, which leaves the
server reasoning at its default.
"""
from __future__ import annotations

import pytest

from openprogram.models.providers.adapters.azure_openai_responses.azure_openai_responses import (
    _build_params as _build_azure_params,
)
from openprogram.models.providers.adapters.openai_codex.openai_codex import _build_request_body
from openprogram.models.providers.adapters.openai_responses import openai_responses
from openprogram.models.providers.adapters.openai_responses.openai_responses import _build_params
from openprogram.models.providers.settings import thinking_spec
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions

ENCRYPTED = ["reasoning.encrypted_content"]


def _model(model_id: str = "gpt-5.2", *, reasoning: bool = True, provider: str = "openai",
           api: str = "openai-responses") -> Model:
    return Model(id=model_id, name=model_id, api=api, provider=provider,
                 base_url="https://api.openai.invalid/v1", reasoning=reasoning)


def test_a_reasoning_model_without_an_effort_still_asks_for_its_reasoning():
    params = _build_params(_model(), Context(), {})
    assert params["include"] == ENCRYPTED
    assert params["store"] is False
    assert "reasoning" not in params


def test_a_model_that_does_not_reason_asks_for_nothing():
    params = _build_params(_model("gpt-4.1", reasoning=False), Context(),
                           {"reasoning_effort": "high"})
    assert "include" not in params and "reasoning" not in params


def test_the_xai_subscription_never_asks_for_encrypted_reasoning():
    params = _build_params(_model("grok-4.5", provider="xai-subscription"), Context(),
                           {"reasoning_effort": "high"})
    assert "include" not in params
    assert params["reasoning"] == {"effort": "high", "summary": "auto"}


def test_an_effort_sends_the_effort_its_summary_and_the_include():
    params = _build_params(_model(), Context(), {"reasoning_effort": "medium"})
    assert params["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert params["include"] == ENCRYPTED


def test_thinking_off_sends_effort_none_and_no_summary():
    params = _build_params(_model(), Context(), {"reasoning_effort": "none"})
    assert params["reasoning"] == {"effort": "none"}
    assert params["include"] == ENCRYPTED


def test_azure_sends_effort_none_the_same_way():
    params = _build_azure_params(_model(api="azure-openai-responses",
                                        provider="azure-openai-responses"),
                                 Context(), {"reasoning_effort": "none"}, "gpt-5.2")
    assert params["reasoning"] == {"effort": "none"}


def test_the_codex_subscription_asks_for_its_reasoning_whatever_the_effort():
    body = _build_request_body(_model(provider="openai-codex"), Context(), {}, [])
    assert body["include"] == ENCRYPTED and "reasoning" not in body
    body = _build_request_body(_model("gpt-4.1", reasoning=False, provider="openai-codex"),
                               Context(), {}, [])
    assert "include" not in body


# ------------------------------------------------------------ the off value

def test_off_translates_to_none_on_the_responses_api():
    assert thinking_spec.translate_reasoning("openai-responses", "gpt-5.2", "off") == "none"
    assert thinking_spec.translate_reasoning("openai-responses", "gpt-5.2", "none") == "none"


@pytest.mark.parametrize("model_id", ["o3", "o4-mini", "gpt-5", "gpt-5-codex"])
def test_a_model_that_predates_none_sends_no_reasoning_field_when_off(model_id):
    assert thinking_spec.translate_reasoning("openai-responses", model_id, "off") is None
    assert thinking_spec.off_wire_value("openai", model_id, "openai-responses") is None
    # Its strengths are unchanged.
    assert thinking_spec.translate_reasoning("openai-responses", model_id, "high") == "high"


def test_a_provider_with_no_spec_of_its_own_reads_its_apis():
    assert thinking_spec.off_wire_value("openai", "gpt-5.2", "openai-responses") == "none"


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex", "google", "deepseek"])
def test_off_is_no_field_where_the_provider_has_no_off_value(provider):
    assert thinking_spec.translate_reasoning(provider, "some-model", "off") is None
    assert thinking_spec.off_wire_value(provider, "some-model", "openai-completions") is None


def test_off_is_never_a_picker_level():
    levels = thinking_spec.derive_thinking_levels("openai-responses", "gpt-5.2", True)
    assert "off" not in levels and "high" in levels


def test_the_adapter_turns_off_into_effort_none(monkeypatch):
    seen = {}

    def fake_stream(model, context, opts):
        seen.update(opts)
        return "stream"

    monkeypatch.setattr(openai_responses, "stream_openai_responses", fake_stream)
    options = SimpleStreamOptions(reasoning="off", api_key="sk-test")
    assert openai_responses.stream_simple_openai_responses(_model(), Context(), options) \
        == "stream"
    assert seen["reasoning_effort"] == "none"
    seen.clear()
    openai_responses.stream_simple_openai_responses(_model("o3"), Context(), options)
    assert seen["reasoning_effort"] is None
