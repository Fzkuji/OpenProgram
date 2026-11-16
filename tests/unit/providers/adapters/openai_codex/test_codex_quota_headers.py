"""A Codex answer's plan windows are kept, from its ``x-codex-*`` headers, a 429 included.

The HTTP client is a fake that answers with recorded Codex headers; nothing
reaches the backend.
"""
from __future__ import annotations

import asyncio
import contextlib

import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
from openprogram.models.providers.adapters.openai_codex import websocket_transport
from openprogram.models.providers.types import Context, UserMessage
from openprogram.models.usage import quota

HEADERS = {
    "x-codex-primary-used-percent": "100",
    "x-codex-primary-window-minutes": "300",
    "x-codex-primary-reset-at": "1800003600",
    "x-codex-secondary-used-percent": "71",
    "x-codex-secondary-window-minutes": "10080",
}
BODY = b'{"error": {"type": "usage_limit_reached", "resets_in_seconds": 3600}}'


class _Model:
    id = "gpt-test"
    api = "openai-codex"
    provider = "openai-codex"
    reasoning = False
    base_url = "https://chatgpt.com/backend-api"
    headers = None
    input = ["text"]


class _Response:
    status_code = 429
    headers = HEADERS

    async def aread(self) -> bytes:
        return BODY


class _Client:
    @contextlib.asynccontextmanager
    async def stream(self, *args, **kwargs):
        yield _Response()


def test_the_windows_of_a_spent_plan_are_kept(monkeypatch):
    async def no_socket(**kwargs):
        raise websocket_transport.SocketUnavailable("HTTP for this test")

    monkeypatch.setattr(websocket_transport, "open_turn", no_socket)
    monkeypatch.setattr(cx, "get_shared_async_client", lambda *a, **k: _Client())
    monkeypatch.setattr(cx, "validate_input_modalities", lambda model, context: None)
    observed: list[tuple] = []
    monkeypatch.setattr(quota.default_store, "observe",
                        lambda provider, account, limits, **kw: observed.append((provider, account, limits)))
    context = Context(system_prompt="sys", messages=[UserMessage(content="hi", timestamp=0)])

    async def run():
        stream = cx.stream_openai_codex_responses(
            _Model(), context,
            {"api_key": "test-token", "headers": {"chatgpt-account-id": "acct", "originator": "codex_cli_rs"}},
        )
        try:
            async for _event in stream:
                pass
        except Exception:
            pass

    asyncio.run(run())
    assert observed, "the answer's headers were not read"
    provider, account, limits = observed[0]
    assert provider == "openai-codex" and account == ""
    by_key = {limit.limit_key: limit for limit in limits}
    assert by_key["primary"].status == "exhausted" and by_key["primary"].resets_at == 1800003600
    assert by_key["secondary"].label == "Weekly"
