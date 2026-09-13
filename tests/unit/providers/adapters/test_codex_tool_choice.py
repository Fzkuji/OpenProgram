"""Regression: codex's _build_request_body must honor opts.tool_choice.

The Codex backend speaks the Responses API, which natively accepts
"auto" / "required" / "none" and the forced-pick {"type":"function",
"name":X} shape. _build_request_body used to hardcode tool_choice="auto"
(and parallel_tool_calls=True), silently dropping a caller's
tool_choice="required". That broke call_with_schema's forced-submit
pattern: gpt-5.5 would reply with text instead of calling the submit
tool, so structured-output helpers (e.g. research_harness review_loop's
submit_meta_review) failed every time and the agent looped.
"""
import openprogram.providers.openai_codex.openai_codex as cx
from openprogram.providers.types import SimpleStreamOptions


class _M:
    id = "gpt-5.5"
    api = "openai-responses"
    provider = "openai-codex"
    reasoning = False
    base_url = None


class _Ctx:
    system_prompt = ""
    tools = [{"dummy": 1}]


def _body(opts, monkeypatch):
    # Skip the tool-schema conversion — we're only testing tool_choice.
    monkeypatch.setattr(cx, "convert_responses_tools", lambda tools, api, mid: tools)
    return cx._build_request_body(_M(), _Ctx(), opts, [])


def test_required_is_passed_through(monkeypatch):
    """call_with_schema's tool_choice='required' + parallel=False must reach the body."""
    b = _body(SimpleStreamOptions(tool_choice="required", parallel_tool_calls=False), monkeypatch)
    assert b["tool_choice"] == "required"
    assert b["parallel_tool_calls"] is False


def test_default_is_auto(monkeypatch):
    """When the caller says nothing, fall back to the prior default (auto / parallel)."""
    b = _body(SimpleStreamOptions(), monkeypatch)
    assert b["tool_choice"] == "auto"
    assert b["parallel_tool_calls"] is True


def test_forced_tool_dict_is_passed_through(monkeypatch):
    """The forced-pick {"type":"function","name":X} shape passes verbatim."""
    choice = {"type": "function", "name": "submit_meta_review"}
    b = _body(SimpleStreamOptions(tool_choice=choice), monkeypatch)
    assert b["tool_choice"] == choice
