"""Every Codex request names its conversation in the headers the backend routes by.

Measured against the ChatGPT backend: the same prompt with the same
``prompt_cache_key``, sent on a new connection 10-20 s after it was cached,
read 0 cached tokens five times out of five without these headers and the
whole prefix five times out of five with them. A turn opens a new
connection whenever its socket is not kept (a new process, a new event
loop, an idle socket), so without the headers the first request of most
turns paid for the whole prompt again.
"""
from __future__ import annotations

import asyncio

import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
from openprogram.models.providers.adapters.openai_codex import websocket_transport
from openprogram.models.providers.types import Context, UserMessage
from openprogram.models.providers.utils.stream_retry import ProviderStreamError


class _Model:
    id = "gpt-test"
    api = "openai-codex"
    provider = "openai-codex"
    reasoning = False
    base_url = "https://chatgpt.com/backend-api"
    headers = None
    input = ["text"]


def _sent_headers(monkeypatch, *, session_id, extra_headers=None):
    seen: list[dict] = []

    async def open_turn(*, base_url, headers, body, receive_next, on_frame=None):
        seen.append({"headers": dict(headers), "body": dict(body)})
        raise ProviderStreamError("stop after the request is formed", retryable=False)

    monkeypatch.setattr(websocket_transport, "open_turn", open_turn)
    monkeypatch.setattr(cx, "validate_input_modalities", lambda model, context: None)
    options = {
        "api_key": "test-token",
        "session_id": session_id,
        "headers": {"chatgpt-account-id": "acct", "originator": "codex_cli_rs",
                    **(extra_headers or {})},
    }
    context = Context(system_prompt="sys", messages=[UserMessage(content="hi", timestamp=0)])

    async def run():
        stream = cx.stream_openai_codex_responses(_Model(), context, options)
        try:
            async for _event in stream:
                pass
        except ProviderStreamError:
            pass

    asyncio.run(run())
    return seen


def test_a_request_names_its_conversation_in_the_routing_headers(monkeypatch):
    seen = _sent_headers(monkeypatch, session_id="local_abc")
    assert seen, "no request was formed"
    request = seen[0]
    assert request["body"]["prompt_cache_key"] == "local_abc"
    assert request["headers"]["session_id"] == "local_abc"
    assert request["headers"]["conversation_id"] == "local_abc"


def test_a_request_without_a_conversation_is_keyed_by_its_prefix(monkeypatch):
    """A title or memory call has no session; its instructions and tools
    repeat, so they key its route and the next such call finds the cache."""
    from openprogram.models.providers.shared.cache_key import prefix_cache_key

    seen = _sent_headers(monkeypatch, session_id=None)
    key = seen[0]["body"]["prompt_cache_key"]
    # The derivation a Runtime call uses for the same model and prompt.
    assert key == prefix_cache_key("openai-codex", "gpt-test", seen[0]["body"]["instructions"], [])
    assert seen[0]["headers"]["session_id"] == key


def test_a_prefix_key_follows_the_instructions_and_tools_only():
    class Ctx:
        system_prompt = "summarize"
        tools = None

    base = cx._build_request_body(_Model(), Ctx(), {}, [{"role": "user", "content": "a"}])
    other_input = cx._build_request_body(_Model(), Ctx(), {}, [{"role": "user", "content": "b"}])
    Ctx.system_prompt = "translate"
    other_instructions = cx._build_request_body(_Model(), Ctx(), {}, [])
    assert base["prompt_cache_key"] == other_input["prompt_cache_key"]
    assert base["prompt_cache_key"] != other_instructions["prompt_cache_key"]
    named = cx._build_request_body(_Model(), Ctx(), {"session_id": "local_x"}, [])
    assert named["prompt_cache_key"] == "local_x"


def test_a_caller_header_is_kept():
    headers = {"Session_Id": "chosen"}
    cx._route_to_conversation(headers, "local_abc")
    assert headers == {"Session_Id": "chosen", "conversation_id": "local_abc"}


def test_a_retry_route_moves_the_headers_with_the_key():
    headers = {"session_id": "local_abc", "conversation_id": "local_abc"}
    cx._route_to_conversation(headers, "op-retry-1", replace=True)
    assert headers == {"session_id": "op-retry-1", "conversation_id": "op-retry-1"}


def test_the_socket_handshake_carries_the_routing_headers():
    """The handshake drops HTTP-only headers; the routing ones must survive it."""
    for name in cx.CONVERSATION_HEADERS:
        assert name not in websocket_transport._HTTP_ONLY_HEADERS
