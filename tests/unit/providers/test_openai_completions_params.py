import asyncio
from types import SimpleNamespace

import pytest

from openprogram.providers.openai_completions import openai_completions
from openprogram.providers.types import Context, Model, SimpleStreamOptions


class _EmptyStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.parametrize(("reasoning", "expected"), [(False, None), (True, "high")])
def test_reasoning_effort_requires_reasoning_model(monkeypatch, reasoning, expected):
    captured = {}

    class Completions:
        async def create(self, **params):
            captured.update(params)
            return _EmptyStream()

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(openai_completions._openai, "AsyncOpenAI", Client)
    model = Model(
        id="gpt-test",
        name="GPT test",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=reasoning,
    )

    async def consume():
        return [
            event async for event in openai_completions.stream_simple(
                model,
                Context(),
                SimpleStreamOptions(api_key="test-key", reasoning="high"),
            )
        ]

    asyncio.run(consume())

    if expected is None:
        assert "reasoning_effort" not in captured
    else:
        assert captured["reasoning_effort"] == expected
