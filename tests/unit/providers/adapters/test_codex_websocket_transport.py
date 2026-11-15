"""A Codex turn over the backend's WebSocket, sent as a delta when it carries on.

A local WebSocket server stands in for chatgpt.com: it records each
``response.create`` message, answers it with one output item and
``response.completed``, and can refuse the handshake, close 1009 or answer
``previous_response_not_found`` on demand.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from openprogram.models.providers.adapters.openai_codex import request_compression
from openprogram.models.providers.adapters.openai_codex import websocket_transport as ws
from openprogram.models.providers.utils.stream_retry import ProviderStreamError


@pytest.fixture(autouse=True)
def _socket_on(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_CODEX_WEBSOCKET", "1")
    ws._shunned.clear()
    ws._too_big.clear()
    yield
    ws._shunned.clear()
    ws._too_big.clear()


class Backend:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.connections = 0
        self.refuse_handshake = 0
        self.close_too_big = False
        self.forget_responses = False
        self.cut_after_first_frame = False
        self.headers: list[Headers] = []

    def process_request(self, connection, request):
        self.headers.append(request.headers)
        if self.refuse_handshake:
            return Response(self.refuse_handshake, "Refused", Headers(), b"no")
        return None

    async def handler(self, connection) -> None:
        self.connections += 1
        async for raw in connection:
            message = json.loads(raw)
            self.messages.append(message)
            if self.close_too_big:
                await connection.close(1009, "message too big")
                return
            if self.forget_responses and "previous_response_id" in message:
                await connection.send(json.dumps({
                    "type": "error", "status": 400,
                    "error": {"code": "previous_response_not_found", "message": "gone"},
                }))
                continue
            turn = len(self.messages)
            await connection.send(json.dumps({"type": "codex.rate_limits", "plan_type": "plus"}))
            await connection.send(json.dumps({"type": "response.created", "response": {"id": f"resp_{turn}"}}))
            if self.cut_after_first_frame:
                await connection.close()
                return
            item = {"type": "message", "role": "assistant", "id": f"msg_{turn}", "status": "completed",
                    "content": [{"type": "output_text", "text": f"reply {turn}", "annotations": []}]}
            await connection.send(json.dumps({"type": "response.output_item.done", "item": item}))
            await connection.send(json.dumps({"type": "response.completed", "response": {"id": f"resp_{turn}"}}))


def run(scenario) -> Backend:
    """Run ``scenario(backend)`` against a fresh local backend on a new loop."""
    state = Backend()

    async def main() -> None:
        async with serve(state.handler, "127.0.0.1", 0, process_request=state.process_request) as server:
            port = server.sockets[0].getsockname()[1]
            state.base_url = f"http://127.0.0.1:{port}"
            try:
                await scenario(state)
            finally:
                await ws.drain()

    asyncio.run(main())
    return state


async def _receive(connection):
    return await asyncio.wait_for(connection.recv(), 5)


def _body(*items, instructions="Be brief.") -> dict:
    return {"model": "gpt-test", "instructions": instructions, "stream": True, "store": False,
            "prompt_cache_key": "chat-1", "input": list(items)}


USER_1 = {"role": "user", "content": [{"type": "input_text", "text": "first"}]}
USER_2 = {"role": "user", "content": [{"type": "input_text", "text": "second"}]}
# The reply as a client sends it back: no id, no status, no empty annotations.
REPLY_1 = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "reply 1"}]}
HEADERS = {"Authorization": "Bearer tok", "chatgpt-account-id": "acct", "Content-Type": "application/json"}


async def _turn(backend, body) -> list[dict]:
    turn = await ws.open_turn(base_url=backend.base_url, headers=HEADERS, body=body, receive_next=_receive)
    return [event async for event in turn.events(_receive)]


def test_a_continuing_turn_goes_as_its_new_input_after_the_last_reply():
    async def scenario(backend):
        first = await _turn(backend, _body(USER_1))
        assert [e["type"] for e in first] == ["response.created", "response.output_item.done", "response.completed"]

        await _turn(backend, _body(USER_1, REPLY_1, USER_2))

        assert backend.connections == 1
        whole, delta = backend.messages
        assert whole["type"] == "response.create" and whole["input"] == [USER_1]
        assert delta["previous_response_id"] == "resp_1"
        assert delta["input"] == [USER_2]
        assert delta["instructions"] == "Be brief."

    run(scenario)




def test_the_handshake_says_the_socket_beta_and_not_the_http_headers():
    async def scenario(backend):
        await _turn(backend, _body(USER_1))
        sent = backend.headers[0]
        assert sent["OpenAI-Beta"] == ws.BETA
        assert sent["chatgpt-account-id"] == "acct"
        assert "Content-Type" not in sent

    run(scenario)




def test_a_turn_that_changed_anything_else_goes_whole_on_the_kept_socket():
    async def scenario(backend):
        await _turn(backend, _body(USER_1))
        await _turn(backend, _body(USER_1, REPLY_1, USER_2, instructions="Be thorough."))
        assert backend.connections == 1
        assert "previous_response_id" not in backend.messages[1]
        assert backend.messages[1]["input"] == [USER_1, REPLY_1, USER_2]

    run(scenario)




def test_an_edited_history_goes_whole():
    async def scenario(backend):
        await _turn(backend, _body(USER_1))
        edited = {"role": "user", "content": [{"type": "input_text", "text": "first, edited"}]}
        await _turn(backend, _body(edited, REPLY_1, USER_2))
        assert "previous_response_id" not in backend.messages[1]

    run(scenario)




def test_a_forgotten_response_sends_the_turn_whole_on_a_new_socket():
    async def scenario(backend):
        await _turn(backend, _body(USER_1))
        backend.forget_responses = True
        events = await _turn(backend, _body(USER_1, REPLY_1, USER_2))
        assert events[-1]["type"] == "response.completed"
        assert backend.connections == 2
        assert "previous_response_id" in backend.messages[1]
        assert backend.messages[2]["input"] == [USER_1, REPLY_1, USER_2]

    run(scenario)




def test_another_account_never_finds_the_socket():
    async def scenario(backend):
        await _turn(backend, _body(USER_1))
        other = {**HEADERS, "Authorization": "Bearer other"}
        turn = await ws.open_turn(base_url=backend.base_url, headers=other,
                                  body=_body(USER_1, REPLY_1, USER_2), receive_next=_receive)
        [event async for event in turn.events(_receive)]
        assert backend.connections == 2
        assert "previous_response_id" not in backend.messages[1]

    run(scenario)




def test_a_refused_handshake_goes_over_http_and_the_host_is_shunned():
    async def scenario(backend):
        backend.refuse_handshake = 403
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        backend.refuse_handshake = 0
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        assert len(backend.headers) == 1

    run(scenario)




def test_an_account_fault_at_the_handshake_is_not_held_against_the_host():
    async def scenario(backend):
        backend.refuse_handshake = 401
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        backend.refuse_handshake = 0
        assert (await _turn(backend, _body(USER_1)))[-1]["type"] == "response.completed"

    run(scenario)




def test_a_message_too_big_goes_over_http_and_is_remembered():
    async def scenario(backend):
        backend.close_too_big = True
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        backend.close_too_big = False
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        assert backend.connections == 1

    run(scenario)




def test_a_socket_cut_mid_reply_is_a_retryable_break():
    async def scenario(backend):
        backend.cut_after_first_frame = True
        turn = await ws.open_turn(base_url=backend.base_url, headers=HEADERS, body=_body(USER_1), receive_next=_receive)
        with pytest.raises(ProviderStreamError) as caught:
            [event async for event in turn.events(_receive)]
        assert caught.value.retryable

    run(scenario)




def test_turned_off_it_is_never_tried(monkeypatch):
    async def scenario(backend):
        monkeypatch.setenv("OPENPROGRAM_CODEX_WEBSOCKET", "0")
        with pytest.raises(ws.SocketUnavailable):
            await _turn(backend, _body(USER_1))
        assert backend.connections == 0

    run(scenario)




def test_item_digests_ignore_what_a_client_does_not_send_back():
    # Recorded from the live backend (2026-11-08): it writes ids, status,
    # empty annotations and logprobs, a message's phase, and compact
    # arguments; a client sends none of that back verbatim.
    served = {"type": "message", "role": "assistant", "id": "msg_1", "status": "completed",
              "content": [{"type": "output_text", "text": "hi", "annotations": [], "logprobs": []}],
              "phase": "final_answer"}
    sent_back = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}
    assert ws.item_digest(served) == ws.item_digest(sent_back)

    served_call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_weather",
                   "arguments": '{"city":"Paris"}', "status": "completed"}
    sent_call = {"type": "function_call", "call_id": "call_1", "name": "get_weather",
                 "arguments": '{"city": "Paris"}'}
    assert ws.item_digest(served_call) == ws.item_digest(sent_call)
    assert ws.item_digest(served_call) != ws.item_digest({**sent_call, "arguments": '{"city": "Rome"}'})


def test_a_chatgpt_turn_goes_zstd_compressed_and_others_plain():
    import zstandard

    body = {"model": "m", "input": ["x" * 4096]}
    compressed, headers = request_compression.encode_turn(body, chatgpt_sign_in=True)
    assert headers == {"Content-Encoding": "zstd"}
    assert json.loads(zstandard.ZstdDecompressor().decompress(compressed)) == body
    assert len(compressed) < 200

    plain, headers = request_compression.encode_turn(body, chatgpt_sign_in=False)
    assert headers == {} and json.loads(plain) == body


def test_the_rate_limit_frame_goes_to_the_observer_not_the_reply():
    seen: list[dict] = []

    async def scenario(backend):
        turn = await ws.open_turn(base_url=backend.base_url, headers=HEADERS, body=_body(USER_1),
                                  receive_next=_receive, on_frame=seen.append)
        events = [event async for event in turn.events(_receive)]
        assert all(event["type"] != "codex.rate_limits" for event in events)

    run(scenario)
    assert [frame["type"] for frame in seen] == ["codex.rate_limits"]
    assert seen[0]["plan_type"] == "plus"
