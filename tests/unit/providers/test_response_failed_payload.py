"""A failed Responses stream surfaces the error the event carried.

``response.failed`` carries ``response.error.{code, message}``; the shared
processor raised a bare ``RuntimeError("Unknown error")``, so every
Responses-family provider (openai, azure, openai-codex over HTTP and WS)
reported failures without their code or message and the error taxonomy saw
an unclassifiable exception. The vendored reference (references/pi-ai,
openai-responses-shared.ts) surfaces both fields.
"""
import asyncio

import pytest

from openprogram.models.providers.shared.openai_responses import (
    process_responses_stream,
)
from openprogram.models.providers.types import AssistantMessage, Model


class _Collector:
    def __init__(self):
        self.events = []

    def push(self, evt):
        self.events.append(evt)


async def _feed(events):
    for e in events:
        yield e


def _model() -> Model:
    return Model(id="gpt-x", name="x", api="openai-responses", provider="openai",
                 base_url="https://example.invalid")


def _output() -> AssistantMessage:
    return AssistantMessage(content=[], api="openai-responses", provider="openai",
                            model="gpt-x", timestamp=0)


def test_response_failed_surfaces_the_error_payload():
    events = [{
        "type": "response.failed",
        "response": {
            "status": "failed",
            "error": {"code": "rate_limit_exceeded", "message": "Too many requests"},
        },
    }]
    with pytest.raises(RuntimeError, match="rate_limit_exceeded: Too many requests"):
        asyncio.run(process_responses_stream(_feed(events), _output(), _Collector(), _model()))


def test_response_failed_without_an_error_object_still_names_the_event():
    events = [{"type": "response.failed", "response": {"status": "failed"}}]
    with pytest.raises(RuntimeError, match="Unknown error"):
        asyncio.run(process_responses_stream(_feed(events), _output(), _Collector(), _model()))
