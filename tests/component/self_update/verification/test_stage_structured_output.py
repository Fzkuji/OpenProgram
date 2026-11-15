from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from openprogram.models.providers.structured_output import (
    JsonSchemaOutput,
    StructuredOutputCapabilities,
    negotiate_structured_output,
    normalize_response_format,
)
from openprogram.operations.self_update.repair import diagnosis, source_repair
from openprogram.operations.self_update.verification.verifier_config import _response_format

REQUEST = SimpleNamespace(update_id="su_x", candidate_sha="a" * 40, assertions=["docs show the marker"])


def _stage_formats():
    verifier = {"attempt": 1, "tools_override": [], "profile_snapshot": {"tools": []},
                "response_format": asdict(normalize_response_format(_response_format(REQUEST, 1)))}
    repair = source_repair.freeze_config(REQUEST, verifier, candidate_path="/c", branch_name="op/wt/x")
    return {
        "verifier": verifier["response_format"],
        "diagnosis": diagnosis.freeze_config(REQUEST, verifier)["response_format"],
        "source_repair": repair["response_format"],
    }


@pytest.mark.parametrize("stage", ["verifier", "diagnosis", "source_repair"])
def test_a_model_without_native_or_strict_tool_output_can_run_every_stage(stage):
    output = JsonSchemaOutput(**_stage_formats()[stage])
    model = SimpleNamespace(provider="deepseek", api="openai-completions", id="deepseek-chat")
    plan = negotiate_structured_output(model, StructuredOutputCapabilities(), output, [object()])
    assert plan.mode == "prompt"


def test_native_output_is_still_preferred_where_it_is_verified():
    output = JsonSchemaOutput(**_stage_formats()["verifier"])
    model = SimpleNamespace(provider="openai", api="openai-responses", id="gpt")
    capabilities = StructuredOutputCapabilities(native="supported", with_tools=True)
    assert negotiate_structured_output(model, capabilities, output, [object()]).mode == "native"
