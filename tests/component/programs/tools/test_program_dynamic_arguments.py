from openprogram.programs._host import runtime as program_runtime

"""Public program mappings through canonical validation and real Codex transport."""

import importlib
import json

import pytest


@pytest.mark.parametrize(
    "call_args,expected,call_tool",
    [
        (
            {
                "program": "owned_mapping_consumer",
                "args": {"task": "Actual owned task"},
            },
            "Owned task saved",
            "program",
        ),
        (
            {
                "program": "owned_mapping_consumer",
                "args": {"task": "Actual owned task", "foreign": 1},
            },
            "Error: bad arguments",
            "program",
        ),
        (
            {"program": "not_an_owned_program", "args": {"task": "Actual owned task"}},
            "is not registered",
            "program",
        ),
        (
            {"program": "owned_mapping_consumer", "args": ["wrong object"]},
            "Validation failed",
            "program",
        ),
        (
            {"values": {"nullable": None, "integer": 7}},
            "Owned nullable mapping saved",
            "owned_nullable_mapping",
        ),
        (
            {"values": {"array": [7, None]}},
            "Owned nullable mapping saved",
            "owned_nullable_mapping",
        ),
    ],
)
def test_program_task_mapping_reaches_consumer_through_native_codex_tool_loop(
    tmp_path, monkeypatch, call_args, expected, call_tool, record_property
):
    from openprogram.state import paths
    from openprogram import Agent
    from openprogram.runtime.agent import call_state as af_runtime
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.programs._host import runtime as tools_runtime
    from openprogram.models.providers.types import Model
    from openprogram.state.store import SessionStore, session_scope
    from openprogram.state.worktree.context import reset_worktree, set_worktree
    from tests.component.providers.adapters.test_stream_fixes import (
        _COMPLETED,
        _FakeHTTPClient,
        _FakeSSEResponse,
        _fc_added,
        _fc_done,
        _sse,
    )

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(program_runtime, "_registry", dict(program_runtime._registry))
    monkeypatch.setattr(tools_runtime, "_registry", dict(tools_runtime._registry))
    importlib.import_module("openprogram.programs.tools.runtime.program")
    importlib.import_module("openprogram.programs.tools.code.execute_code")
    project = tmp_path / "project"
    project.mkdir()
    output = project / "consumer.txt"

    class OwnedMappingConsumerAgent(Agent):
        method_options = {
            "owned_mapping_consumer": {"name": "owned_mapping_consumer", "tool": True},
        }

        def owned_mapping_consumer(self, task: str):
            """Save the actual owned task received through the generic program tool."""
            output.write_text(task)
            return "Owned task saved"

    OwnedMappingConsumerAgent().owned_mapping_consumer

    @tools_runtime.function(name="owned_nullable_mapping")
    def owned_nullable_mapping(values: dict[str, int | list[int | None] | None]):
        output.write_text(json.dumps(values, sort_keys=True))
        return "Owned nullable mapping saved"

    args = json.dumps(call_args)
    first = _FakeSSEResponse(
        [
            _sse(_fc_added(0, "owned-program-call", call_tool)),
            _sse(_fc_done(0, "owned-program-call", call_tool, args)),
            _sse(_COMPLETED),
            "data: [DONE]",
        ]
    )
    last = _FakeSSEResponse(
        [
            _sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"type": "message", "id": "owned-final"},
                }
            ),
            _sse(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "delta": "Owned completed",
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "message",
                        "id": "owned-final",
                        "content": [{"text": "Owned completed"}],
                    },
                }
            ),
            _sse(_COMPLETED),
            "data: [DONE]",
        ]
    )
    transport = _FakeHTTPClient([first, last])
    provider = importlib.import_module(
        "openprogram.models.providers.adapters.openai_codex.openai_codex"
    )
    monkeypatch.setattr(provider, "get_shared_async_client", lambda *a, **kw: transport)
    monkeypatch.setattr(
        provider, "build_async_client", lambda **kw: pytest.fail("Unexpected retry")
    )
    from openprogram.models.providers.adapters.openai_codex import catalog as codex_catalog

    monkeypatch.setattr(codex_catalog, "codex_client_version", lambda: "owned-version")
    runtime = Runtime(
        call=lambda **kw: pytest.fail("Legacy provider bypass"),
        model="owned",
        api_key="owned-test-token",
        max_retries=2,
    )
    runtime.api_model = Model(
        id="owned-codex",
        name="Owned",
        api="openai-codex",
        provider="openai-codex",
        base_url="https://example.invalid",
    )
    runtime._stream_fn = provider.stream_simple_openai_codex_responses
    store = SessionStore(tmp_path / "sessions")
    store.create_session("owned-mapping", "Owned mapping test")
    worktoken = set_worktree(str(project))
    token = af_runtime._current_runtime.set(runtime)
    try:
        with session_scope(store, "owned-mapping"):
            runtime.exec(
                content=[{"type": "text", "text": "Invoke the owned task program"}],
                # Named in the call's tool list, so their schemas are sent
                # from the first request without a search.
                tools=[
                    tools_runtime.as_resident(tools_runtime.get("program")),
                    tools_runtime.as_resident(tools_runtime.get("execute_code")),
                    tools_runtime.as_resident(owned_nullable_mapping),
                ],
                max_iterations=2,
            )
            if expected.startswith("Owned "):
                assert output.exists(), [
                    i.get("output")
                    for i in json.loads(transport.contents[1])["input"]
                    if i.get("type") == "function_call_output"
                ]
                assert output.read_text() == (
                    "Actual owned task"
                    if call_tool == "program"
                    else json.dumps(call_args["values"], sort_keys=True)
                )
            else:
                assert not output.exists()

        assert transport.calls == 2
        # The adapter sends the body as encoded bytes; the second request
        # carries the program tool's result back to the model.
        tool_outputs = [
            str(item.get("output"))
            for item in json.loads(transport.contents[1])["input"]
            if item.get("type") == "function_call_output"
        ]
        assert any(expected in out for out in tool_outputs), tool_outputs
        wire = {
            tool["name"]: tool
            for tool in json.loads(transport.contents[0]).get("tools", [])
        }
        assert "program" in wire, json.loads(transport.contents[0])
        assert wire["program"]["strict"] is False
        assert (
            wire["program"]["parameters"]["properties"]["args"]["additionalProperties"]
            is True
        )
        assert wire["execute_code"]["strict"] is True
        record_property(
            "public_receipt",
            json.dumps(
                {
                    "controlled_http_requests": transport.calls,
                    "actual_consumer_ran": output.exists(),
                    "actual_program_tool_result": expected,
                    "program_strict": wire["program"]["strict"],
                    "call_tool": call_tool,
                    "execute_code_strict": wire["execute_code"]["strict"],
                    "source_origin": tools_runtime.__file__,
                }
            ),
        )
    finally:
        af_runtime._current_runtime.reset(token)
        reset_worktree(worktoken)
        runtime.close()
        store.close()


def test_execute_code_fixed_wire_rejects_extra_key():
    from jsonschema import Draft7Validator, ValidationError

    from openprogram.programs._host import runtime as tools_runtime
    from openprogram.models.providers.shared.openai_responses import convert_responses_tools

    importlib.import_module("openprogram.programs.tools.code.execute_code")
    tool = tools_runtime.get("execute_code")
    (wire,) = convert_responses_tools([tool], "openai-codex", "owned")
    assert wire["strict"] is True
    assert "additionalProperties" not in tool.parameters
    with pytest.raises(ValidationError, match="unexpected"):
        Draft7Validator(wire["parameters"]).validate(
            {
                "code": "pass",
                "timeout": None,
                "cwd": None,
                "python": None,
                "unexpected": 1,
            }
        )
