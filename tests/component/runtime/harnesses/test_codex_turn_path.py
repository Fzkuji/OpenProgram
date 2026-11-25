"""A chat turn on a conversation pinned to Codex runs through the real turn path.

The message is admitted as an execution, the turn hands off to the Codex
engine (no agent loop), Codex's approval request becomes a live approval
wait the person answers with ``execution.wait.answer``, and the reply is
stored with its cards.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from openprogram.runtime.execution.model import ExecutionStatus
from tests.component.agent.execution.test_agent_continuation_real import (  # noqa: F401 - fixture
    _chat, _pending_question, _question_action, _wait, real_agent_chat,
)

FAKE = Path(__file__).with_name("fake_codex_app_server.py")

TURN = [
    {"send": {"method": "item/started", "params": {"item": {
        "type": "commandExecution", "id": "c1", "command": "/bin/zsh -lc 'touch x'", "cwd": "/w",
        "status": "inProgress"}}}},
    {"request": {"method": "item/commandExecution/requestApproval", "params": {
        "threadId": "$THREAD", "turnId": "$TURN", "itemId": "c1", "command": "/bin/zsh -lc 'touch x'",
        "cwd": "/w", "reason": "writes a file"}}},
    {"send": {"method": "item/completed", "params": {"item": {
        "type": "commandExecution", "id": "c1", "command": "/bin/zsh -lc 'touch x'", "cwd": "/w",
        "aggregatedOutput": "", "exitCode": 0, "status": "completed"}}}},
    {"send": {"method": "item/agentMessage/delta", "params": {"itemId": "a1", "delta": "Made x."}}},
]


def test_a_codex_conversation_turn_asks_and_stores_its_reply(real_agent_chat, tmp_path, monkeypatch):  # noqa: F811
    import openprogram.runtime.conversation.turn_runtime as dispatcher
    from openprogram.runtime.harnesses.codex import client

    harness = real_agent_chat
    binary = tmp_path / "codex"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "codex.log"
    (tmp_path / "scenario.json").write_text(json.dumps([TURN]))
    monkeypatch.setenv("OPENPROGRAM_CODEX_BINARY", str(binary))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "turns"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    pool = client.ProcessPool()
    monkeypatch.setattr(client, "_pool", pool)
    harness.sessions.update_session(harness.session_id, provider_override="codex-harness",
                                    model_override="gpt-fake")
    model = harness.model

    def resolve(_profile, override):
        if override and str(override).startswith("codex-harness"):
            # As the real resolver does: no OpenProgram model has this id.
            from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
            raise LLMError("not an OpenProgram model", reason=ErrorReason.INVALID_REQUEST)
        return model

    monkeypatch.setattr(dispatcher, "_resolve_model", resolve)
    try:
        execution = _chat(harness)
        assert execution.capabilities.names() == frozenset({"steer"})
        question = _pending_question(harness, kind="approval")
        assert question.prompt == "允许执行 bash？"
        _question_action(harness, "question_reply", question.id, answer="allow")
        _wait(lambda: harness.store.get_execution(harness.execution_id).status is ExecutionStatus.COMPLETED,
              detail=lambda: harness.store.get_execution(harness.execution_id).to_dict())
    finally:
        pool.close_all()

    rows = [json.loads(line) for line in log.read_text().splitlines()]
    answer = next(row for row in rows if row["dir"] == "in" and row.get("id") == 1000)
    assert answer["result"] == {"decision": "accept"}
    assert harness.provider.call_count == 0
    reply = next(item for item in harness.sessions.get_branch(harness.session_id)
                 if str(item.get("id", "")).endswith("_reply"))
    assert reply.get("content") == "Made x."
    extra = reply.get("extra")
    blocks = json.loads(extra)["blocks"] if isinstance(extra, str) else (extra or {}).get("blocks")
    assert [block["type"] for block in blocks] == ["tool", "text"]
    assert blocks[0]["tool"] == "bash" and json.loads(blocks[0]["input"])["command"] == "touch x"
