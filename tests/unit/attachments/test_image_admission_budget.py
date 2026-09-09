"""Image bytes have a separate budget from instructions and tool input."""
import base64
import pytest
from openprogram.agent.production_driver import AgentDriverError, normalize_agent_turn_payload
from openprogram.execution import AttemptStore, DriverRegistry, ExecutionStore, RuntimeControlService
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.production_driver import CanonicalAgentAdapter


def payload(data, **extra):
    return {"version": 1, "kind": "chat", "request": {
        "user_text": "Inspect the image", "agent_id": "main", "source": "web",
        "attachments": [{"type": "image", "media_type": "image/png", "data": data, **extra}],
    }}


def test_image_larger_than_text_budget_is_preserved_without_mutating_input():
    data = base64.b64encode(b"x" * (1024 * 1024)).decode()
    original = payload(data)
    result = normalize_agent_turn_payload(original)
    assert result["request"]["attachments"][0]["data"] == data
    assert original == result
    assert result is not original


def test_image_does_not_exempt_large_instruction_or_metadata():
    original = payload("eA==", filename="n" * (256 * 1024))
    with pytest.raises(AgentDriverError, match="size limit"):
        normalize_agent_turn_payload(original)
    original = payload("eA==")
    original["request"]["user_text"] = "t" * (256 * 1024)
    with pytest.raises(AgentDriverError, match="size limit"):
        normalize_agent_turn_payload(original)


@pytest.mark.parametrize("data", ["not base64!", "a" * (8 * 1024 * 1024)])
def test_invalid_or_excessive_image_is_rejected(data):
    with pytest.raises(AgentDriverError):
        normalize_agent_turn_payload(payload(data))


def test_nonimage_bytes_are_not_exempt():
    original = payload(base64.b64encode(b"x" * (256 * 1024)).decode())
    original["request"]["attachments"][0]["type"] = "document"
    with pytest.raises(AgentDriverError):
        normalize_agent_turn_payload(original)


def test_multiple_images_have_a_total_budget():
    image = base64.b64encode(b"x" * (5 * 1024 * 1024)).decode()
    original = payload(image)
    original["request"]["attachments"] *= 4
    assert len(normalize_agent_turn_payload(original)["request"]["attachments"]) == 4
    original["request"]["attachments"].append(original["request"]["attachments"][0])
    with pytest.raises(AgentDriverError, match="image size limit"):
        normalize_agent_turn_payload(original)


def test_forced_tool_input_keeps_its_original_budget():
    with pytest.raises(AgentDriverError, match="size limit"):
        normalize_agent_turn_payload({"version": 1, "kind": "forced_tool", "tool_name": "test",
                                      "tool_input": {"data": "x" * (256 * 1024)}})


def test_canonical_admission_persists_image_above_text_budget(tmp_path, monkeypatch):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: control)
    adapter = CanonicalAgentAdapter(store=store)
    data = base64.b64encode(b"x" * (1024 * 1024)).decode()
    request = TurnRequest(
        session_id="image-admission",
        user_text="Inspect the image",
        agent_id="main",
        source="web",
        attachments=[{"type": "image", "media_type": "image/png", "data": data}],
    )
    admitted = adapter.admit(
        request,
        trusted_actor={"subject": "test", "session_ids": ["image-admission"]},
        user_message_id="user-1",
        config_snapshot_ref="config:test",
    )
    stored = store.get_agent_turn_input(admitted.execution_id)
    assert stored is not None
    assert stored["request"]["attachments"][0]["data"] == data
