"""Durable wait discovery for reconnecting clients."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import openprogram.execution as execution_module
from openprogram.agent.questions import PendingQuestion, QuestionRegistry, get_question_registry
from openprogram.agent.run_control import reset_current_execution_id, set_current_execution_id
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore
from openprogram.webui.routes import questions as _routes


@pytest.fixture
def client(monkeypatch, tmp_path):
    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "test"})
    execution = store.create_execution(
        execution_id="exec_questions", run_id="run_questions", session_id="s",
        revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version, owner_id="test", ttl_seconds=30)
    attempts.activate(leased.attempt_id, generation=leased.generation, expected_execution_version=reserved.status_version)
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr("openprogram.agent.questions._registry", QuestionRegistry())
    token = set_current_execution_id(execution.execution_id)
    app = FastAPI()
    _routes.register(app)
    try:
        yield TestClient(app)
    finally:
        reset_current_execution_id(token)


def test_list_reconnects_durable_question_with_exact_target(client):
    get_question_registry().register(PendingQuestion(
        id="wait_reconnect", session_id="s", kind="form", prompt="Configure",
        schema={"mode": {"type": "string", "enum": ["fast", "safe"]}},
    ))
    response = client.get("/api/questions", params={"session_id": "s"})
    assert response.status_code == 200
    question = response.json()["questions"][0]
    assert question["id"] == "wait_reconnect"
    assert question["execution_id"] == "exec_questions"
    assert question["wait_generation"] == 0
    assert question["schema"]["mode"]["enum"] == ["fast", "safe"]
