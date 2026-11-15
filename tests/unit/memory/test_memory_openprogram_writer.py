"""The memory writer uses OpenProgram's configured chat-agent runtime."""

from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.providers.types import Model


def _model(model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="scripted-memory-api",
        provider="scripted-memory-provider",
        base_url="http://scripted.invalid",
    )


def _patch_model_resolution(monkeypatch, seen: list[str | None]) -> None:
    from openprogram.runtime.conversation import model_tools as _model_tools
    from openprogram.runtime.agent_registry import manager

    monkeypatch.setattr(
        manager, "get_default", lambda: SimpleNamespace(id="main")
    )
    monkeypatch.setattr(
        _model_tools,
        "load_agent_profile",
        lambda agent_id: {
            "id": agent_id,
            "model": {"provider": "chat", "id": "default-model"},
        },
    )

    def _resolve(_profile, override=None):
        seen.append(override)
        return _model("override-model" if override else "default-model")

    monkeypatch.setattr(_model_tools, "resolve_model", _resolve)


def test_writer_defaults_to_the_chat_agents_model(monkeypatch):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})

    from openprogram.state.memory import writing

    writer = writing._agent()
    assert writer.model.id == "default-model"
    assert seen == [None]


def test_writer_setting_overrides_only_the_model(monkeypatch):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)
    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"memory": {"writer": {"model": "other/model"}}},
    )

    from openprogram.state.memory import writing

    writer = writing._agent()
    assert writer.model.id == "override-model"
    assert seen == ["other/model"]


def test_openprogram_writer_preserves_structured_permanent_errors(
    monkeypatch, tmp_path,
):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)

    from openprogram.models.providers.types import (
        AssistantMessage,
        EventError,
    )

    async def denied(model, context, options=None):
        yield EventError(
            reason="error",
            error=AssistantMessage(
                content=[], api=model.api, provider=model.provider,
                model=model.id, stop_reason="error",
                error_message="credential rejected",
                error_reason="authentication", error_retryable=False,
                timestamp=0,
            ),
        )

    from openprogram.runtime.conversation.memory_agent import OpenProgramAgent
    from openprogram.state.memory.agent_runtime import AgentExecutionError

    writer = OpenProgramAgent(stream_fn=denied)
    try:
        writer.run(
            prompt="organize", system_prompt="manage memory", cwd=tmp_path,
        )
    except AgentExecutionError as exc:
        assert exc.retryable is False
        assert exc.reason == "authentication"
    else:  # pragma: no cover - the assertion documents the public contract
        raise AssertionError("permanent provider failure was reported as success")


def test_openprogram_writer_executes_the_existing_managed_tools(
    monkeypatch, tmp_path,
):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)

    from tests.component.providers.scripted_provider import (
        ScriptedProvider,
        ScriptedText,
        ScriptedToolCall,
    )
    from openprogram.runtime.conversation.memory_agent import OpenProgramAgent
    from openprogram.state.memory.management.tools import management_tools
    from openprogram.state.memory.management.workspace import MemoryWorkspace

    scripted = ScriptedProvider()
    scripted.add_response(ScriptedToolCall(
        "Write",
        {"file_path": "topics/note.md", "content": "# Note\n"},
        "call-1",
    ))
    scripted.add_response(ScriptedText("done"))
    space = MemoryWorkspace(tmp_path / "memory")
    audit: list[dict] = []
    try:
        result = OpenProgramAgent(stream_fn=scripted.stream_simple).run(
            prompt="write a topic",
            system_prompt="manage memory",
            cwd=space.stage_dir,
            tools=management_tools(space, audit),
        )
        assert (space.stage_dir / "topics/note.md").read_text() == "# Note\n"
        assert result.text == "done"
        assert result.turns[0]["tool"] == "Write"
        assert result.turns[1] == {
            "result": "Wrote 7 bytes to topics/note.md",
            "is_error": False,
        }
    finally:
        space.close()


def test_openprogram_writer_rejects_an_unenforceable_budget(monkeypatch, tmp_path):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)

    from openprogram.runtime.conversation.memory_agent import OpenProgramAgent

    writer = OpenProgramAgent()
    try:
        writer.run(
            prompt="organize", system_prompt="manage memory", cwd=tmp_path,
            max_budget_usd=1.0,
        )
    except ValueError as exc:
        assert str(exc) == (
            "max_budget_usd is not supported by the OpenProgram writer"
        )
    else:  # pragma: no cover
        raise AssertionError("an ignored cost limit was accepted")


def test_the_runtime_registers_the_openprogram_writer(monkeypatch):
    seen: list[str | None] = []
    _patch_model_resolution(monkeypatch, seen)

    import openprogram.runtime  # noqa: F401 - registers the writer
    from openprogram.runtime.conversation.memory_agent import OpenProgramAgent
    from openprogram.state.memory.writer_agent import writer_agent

    assert isinstance(writer_agent("other/model"), OpenProgramAgent)
    assert seen == ["other/model"]
