"""The OpenAI-compatible endpoints run a real Agent turn behind owner auth.

The app is the server's own routes behind its owner-auth middleware; the
turn is the production turn path (run slot, canonical execution, agent loop,
session store) with only the model scripted (``real_agent_chat``).
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
from openprogram.interfaces.server.routes import openai_compatible as api
from tests.component.agent.execution.test_agent_continuation_real import (  # noqa: F401
    _wait,
    real_agent_chat,
)

BASE = "http://127.0.0.1:18100"


def _state() -> OwnerAuthState:
    return OwnerAuthState.from_raw_token(
        bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
        bind_host="127.0.0.1", port=18100, allowed_origins=(),
    )


@pytest.fixture
def agents(monkeypatch):
    from openprogram.runtime.agent_registry import manager

    specs = {"main": manager.AgentSpec(id="main", name="Main", default=True, created_at=1.0),
             "writer": manager.AgentSpec(id="writer", name="Writer", created_at=2.0)}
    monkeypatch.setattr(manager, "list_ids", lambda: ["writer", "main"])
    monkeypatch.setattr(manager, "get", lambda agent_id: specs.get(agent_id))
    monkeypatch.setattr(manager, "get_default", lambda: specs["main"])
    return specs


@pytest.fixture
def client(agents):
    state = _state()
    app = FastAPI()
    api.register(app)
    with TestClient(OwnerAuthMiddleware(app, auth_state=state), base_url=BASE) as test_client:
        test_client.headers["Authorization"] = f"Bearer {state.token}"
        yield test_client


def _ask(text: str, **fields) -> dict:
    return {"model": "main", "messages": [{"role": "user", "content": text}], **fields}


def _settled(h, session_id: str) -> None:
    _wait(lambda: not h.server._is_run_active(session_id))


# -- authentication -----------------------------------------------------------

@pytest.mark.parametrize("header", [None, "Bearer not-the-token", "Basic abc"])
def test_a_request_without_the_owner_token_is_refused_in_openai_shape(agents, header):
    app = FastAPI()
    api.register(app)
    headers = {"Authorization": header} if header else {}
    with TestClient(OwnerAuthMiddleware(app, auth_state=_state()), base_url=BASE) as test_client:
        for response in (test_client.get("/v1/models", headers=headers),
                         test_client.post("/v1/chat/completions", json=_ask("hi"), headers=headers)):
            assert response.status_code == 401
            error = response.json()["error"]
            assert error["type"] == "authentication_error"
            assert error["code"] == "invalid_api_key"
            assert "Bearer" in error["message"]


def test_other_paths_keep_the_servers_own_refusal(agents):
    app = FastAPI()
    with TestClient(OwnerAuthMiddleware(app, auth_state=_state()), base_url=BASE) as test_client:
        assert test_client.get("/api/x").json() == {"error": "authentication_required"}


# -- models -------------------------------------------------------------------

def test_models_are_the_agents_default_first(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [row["id"] for row in body["data"]] == ["main", "writer"]
    assert body["data"][0] == {"id": "main", "object": "model", "created": 1,
                               "owned_by": "openprogram", "name": "Main"}


@pytest.mark.parametrize("method, path", [
    ("POST", "/v1/responses"), ("POST", "/v1/embeddings"), ("GET", "/v1/chat/completions"),
])
def test_an_endpoint_not_served_is_not_found_in_openai_shape(client, method, path):
    response = client.request(method, path, json={})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "unknown_url" and path in error["message"]


def test_an_unknown_model_is_not_found(client):
    response = client.post("/v1/chat/completions", json={**_ask("hi"), "model": "gpt-4o"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert response.json()["error"]["param"] == "model"


@pytest.mark.parametrize("body, param", [
    ({"model": "main", "messages": []}, "messages"),
    ({"model": "main", "messages": [{"role": "assistant", "content": "x"}]}, "messages[0].role"),
    ({"model": "main", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:,"}}]}]}, "messages[0].content[0]"),
    ({**_ask("hi"), "n": 2}, "n"),
])
def test_a_malformed_request_is_an_invalid_request_error(client, body, param):
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error" and error["param"] == param


# -- turns --------------------------------------------------------------------

def test_a_completion_runs_one_turn_in_a_new_conversation(real_agent_chat, client):
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    h.provider.add_response(ScriptedText("Four."))
    response = client.post("/v1/chat/completions", json={
        "model": "main",
        "messages": [
            {"role": "system", "content": "Answer briefly."},
            {"role": "user", "content": "What is 1+1?"},
            {"role": "assistant", "content": "Two."},
            {"role": "user", "content": [{"type": "text", "text": "And 2+2?"}]},
        ],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    session_id = body["conversation_id"]
    assert response.headers["x-openprogram-conversation-id"] == session_id
    assert session_id.startswith("api_") and session_id != h.session_id
    assert body["object"] == "chat.completion" and body["model"] == "main"
    assert body["id"].startswith("chatcmpl-")
    assert body["choices"] == [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "Four."}}]
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    _settled(h, session_id)

    users = [m for m in h.sessions.get_branch(session_id) if m.get("role") == "user"]
    assert len(users) == 1
    sent = users[0]["content"]
    assert "Answer briefly." in sent and "User: What is 1+1?" in sent
    assert "Assistant: Two." in sent and sent.endswith("Current message:\nAnd 2+2?")
    assert h.provider.call_count == 1
    # Nobody is behind the request to answer: admitted as an API turn that
    # refuses what would ask, under the bearer's owner authority.
    request = h.store.get_agent_turn_input(body["id"][len("chatcmpl-"):])["request"]
    assert request["source"] == "api"
    assert request["interaction"] == "non-interactive"
    assert request["authority_tier"] == "owner"


def test_a_stream_whose_client_leaves_cancels_its_turn(real_agent_chat, agents):
    """Driven over raw ASGI: the test client reads a stream whole, so it
    cannot leave halfway the way a real client does."""
    import asyncio

    from openprogram.runtime.execution.model import ExecutionStatus
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    h.provider.add_response(ScriptedText("never read"))
    h.provider.block_calls = {0}
    state = _state()
    app = FastAPI()
    api.register(app)
    asgi = OwnerAuthMiddleware(app, auth_state=state)
    body = json.dumps(_ask("hi", stream=True)).encode()
    sent: list[dict] = []

    async def drive() -> None:
        left = asyncio.Event()
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                # The first chunk arrived and the model is busy: the client goes.
                await asyncio.to_thread(h.provider.entered.wait, 30)
                left.set()

        await asgi({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions", "query_string": b"",
            "root_path": "", "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 18100),
            "headers": [(b"host", b"127.0.0.1:18100"),
                        (b"authorization", f"Bearer {state.token}".encode()),
                        (b"content-type", b"application/json")],
        }, receive, send)

    asyncio.run(drive())
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    session_id = dict(start["headers"])[b"x-openprogram-conversation-id"].decode()
    first = next(m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body"))
    execution_id = json.loads(first.decode()[len("data: "):])["id"][len("chatcmpl-"):]
    h.provider.release.set()
    _wait(lambda: h.store.get_execution(execution_id).status is ExecutionStatus.CANCELLED,
          detail=lambda: h.store.get_execution(execution_id).status)
    _settled(h, session_id)


def test_a_named_conversation_gets_the_next_turn(real_agent_chat, client):
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    h.provider.add_response(ScriptedText("first"))
    h.provider.add_response(ScriptedText("second"))
    first = client.post("/v1/chat/completions", json=_ask("one")).json()
    session_id = first["conversation_id"]
    _settled(h, session_id)

    second = client.post("/v1/chat/completions", json={
        "model": "main", "conversation_id": session_id,
        "messages": [{"role": "user", "content": "one"},
                     {"role": "assistant", "content": "first"},
                     {"role": "user", "content": "two"}],
    })
    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == session_id
    assert second.json()["choices"][0]["message"]["content"] == "second"
    _settled(h, session_id)
    users = [m["content"] for m in h.sessions.get_branch(session_id) if m.get("role") == "user"]
    assert users == ["one", "two"]


def test_a_conversation_running_a_turn_is_busy(real_agent_chat, client):
    h = real_agent_chat
    h.sessions.create_session("api_busy", "main")
    assert h.server._try_reserve_run("api_busy", "typed")
    try:
        response = client.post("/v1/chat/completions",
                               json=_ask("hi", conversation_id="api_busy"))
    finally:
        h.server._take_owned_run("api_busy", "typed")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversation_busy"
    assert h.provider.call_count == 0


def test_an_unknown_conversation_is_not_found(real_agent_chat, client):
    response = client.post("/v1/chat/completions", json=_ask("hi", conversation_id="api_nope"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


def test_a_streamed_completion_sends_chunks_then_done(real_agent_chat, client):
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    h.provider.add_response(ScriptedText("Hello"), ScriptedText(" world"))
    with client.stream("POST", "/v1/chat/completions",
                       json=_ask("greet", stream=True,
                                 stream_options={"include_usage": True})) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        session_id = response.headers["x-openprogram-conversation-id"]
        lines = [line for line in response.iter_lines() if line]

    assert all(line.startswith("data: ") for line in lines)
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[len("data: "):]) for line in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    deltas = [c["choices"][0]["delta"].get("content") for c in chunks[1:] if c["choices"]]
    assert deltas[:2] == ["Hello", " world"]
    finish = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]]
    assert len(finish) == 1 and finish[0]["choices"][0] == {
        "index": 0, "delta": {}, "finish_reason": "stop"}
    assert chunks[-1]["choices"] == [] and "total_tokens" in chunks[-1]["usage"]
    assert chunks[-1]["conversation_id"] == session_id
    _settled(h, session_id)


def test_a_failed_turn_is_an_openai_error(real_agent_chat, client):
    from tests.component.providers.scripted_provider import ScriptedError

    h = real_agent_chat
    h.provider.add_response(ScriptedError("the key was refused", error_reason="auth",
                                          retryable=False))
    response = client.post("/v1/chat/completions", json=_ask("hi"))
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["type"] == "api_error" and error["code"] == "auth"
    assert "refused" in error["message"]
    _settled(h, response.headers["x-openprogram-conversation-id"])


def test_a_failed_streamed_turn_ends_with_an_error_event(real_agent_chat, client):
    from tests.component.providers.scripted_provider import ScriptedError

    h = real_agent_chat
    h.provider.add_response(ScriptedError("the key was refused", error_reason="auth",
                                          retryable=False))
    with client.stream("POST", "/v1/chat/completions", json=_ask("hi", stream=True)) as response:
        assert response.status_code == 200
        session_id = response.headers["x-openprogram-conversation-id"]
        lines = [line for line in response.iter_lines() if line]
    assert lines[-1] == "data: [DONE]"
    assert json.loads(lines[-2][len("data: "):])["error"]["code"] == "auth"
    _settled(h, session_id)


# -- the turn's input -----------------------------------------------------------

def test_a_continued_conversation_sends_only_the_last_message():
    messages = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": "hi"}]
    assert api.turn_text(messages, continuing=True) == "hi"
    folded = api.turn_text(messages, continuing=False)
    assert folded.startswith("Instructions from the application")
    assert folded.endswith("Current message:\nhi")


def test_only_the_turns_own_reply_text_is_streamed():
    """A text frame of another message in the conversation (a sub-agent
    branch the turn started) is not this request's reply."""
    def frame(msg_id, text="x", session_id="api_s"):
        return {"type": "chat_response", "data": {
            "type": "stream_event", "session_id": session_id, "msg_id": msg_id,
            "event": {"type": "text", "text": text}}}

    own = frozenset({None, "m1", "m1_reply"})
    assert api._text_delta(frame("m1"), "api_s", own) == "x"
    assert api._text_delta(frame("m1_reply"), "api_s", own) == "x"
    assert api._text_delta(frame("other"), "api_s", own) is None
    assert api._text_delta(frame("m1", session_id="api_t"), "api_s", own) is None


def test_a_single_message_is_sent_as_is():
    assert api.turn_text([{"role": "user", "content": "hi"}], continuing=False) == "hi"


def test_a_conversation_whose_lock_is_not_had_is_locked_not_busy(monkeypatch):
    """Nothing was admitted and no turn runs: the client is told to try
    again (503 ``conversation_locked``), not that the conversation is busy."""
    import asyncio

    from openprogram.runtime.conversation import owner_turn
    from openprogram.state.store.session.session_lock import SessionLockTimeout

    def locked(session_id, *_a, **_k):
        raise SessionLockTimeout(session_id, 60.0, "another process")

    monkeypatch.setattr(owner_turn, "start_owner_turn", locked)

    async def scenario():
        with pytest.raises(api.OpenAIError) as caught:
            api._start(api.TurnRun("api_locked"), "main", "hi", {})
        return caught.value

    error = asyncio.run(scenario())
    assert error.status == 503 and error.code == "conversation_locked"


def test_a_named_conversation_whose_lock_is_not_had_is_locked(client, monkeypatch):
    """Reading the named conversation waited out its lock: 503
    ``conversation_locked``, not an internal error, and nothing started."""
    from openprogram.state.store.session.session_lock import SessionLockTimeout

    class _Locked:
        def get_session(self, session_id):
            raise SessionLockTimeout(session_id, 60.0, "another process")

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: _Locked())
    response = client.post("/v1/chat/completions", json=_ask("hi", conversation_id="api_locked"))
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "conversation_locked" and error["param"] == api.CONVERSATION_FIELD
