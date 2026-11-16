"""Models that will not go on from a conversation ending with the assistant.

Some models refuse a request whose last message is the assistant's (a
prefill): Anthropic models behind some gateways ("This model does not
support assistant message prefill. The conversation must end with a user
message."), Volcengine's Agent Plan, Gemini on Vertex AI ("Requests ending
with a model turn are not supported"). The same providers' other models
take one, so a refusal is remembered for that model on that provider and
API, and nowhere else.

A request to such a model that ends with the assistant's text (not a tool
call) goes with a short user turn after it, as the refusal asks; one
refused before the model was known is sent again so.

The memory is per process. After magpie's prefill memory (yetone/magpie, MIT).
"""
from __future__ import annotations

import re
import threading
import time

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    ToolCall,
    UserMessage,
)

CONTINUE_TEXT = "Continue."

_REFUSAL = re.compile(
    r"does not support (?:assistant message )?prefill"
    r"|requests ending with a model turn are not supported"
    r"|last message cannot be from the assistant",
    re.I,
)

_refusing: set[tuple[str, str, str]] = set()
_lock = threading.Lock()


def _key(model: Model) -> tuple[str, str, str]:
    return (str(model.provider), str(model.api), str(model.id))


def refuses_prefill(refusal: AssistantMessage) -> bool:
    return refusal.stop_reason == "error" and bool(_REFUSAL.search(refusal.error_message or ""))


def ends_with_assistant(context: Context) -> bool:
    """The conversation ends with the assistant's text rather than a call it made."""
    if not context.messages or not isinstance(context.messages[-1], AssistantMessage):
        return False
    return not any(isinstance(block, ToolCall) for block in context.messages[-1].content)


def known_to_refuse(model: Model) -> bool:
    with _lock:
        return _key(model) in _refusing


def forget() -> None:
    with _lock:
        _refusing.clear()


def _user_last(context: Context) -> Context:
    turn = UserMessage(content=CONTINUE_TEXT, timestamp=int(time.time() * 1000))
    return context.model_copy(update={"messages": [*context.messages, turn]})


def before_sending(model: Model, context: Context) -> Context:
    """``context`` with a user turn last when ``model`` is known to refuse a prefill."""
    if ends_with_assistant(context) and known_to_refuse(model):
        return _user_last(context)
    return context


def after_refusal(model: Model, context: Context, refusal: AssistantMessage) -> Context | None:
    """``context`` with a user turn last when ``refusal`` refused its prefill, else ``None``."""
    if not ends_with_assistant(context) or not refuses_prefill(refusal):
        return None
    with _lock:
        _refusing.add(_key(model))
    return _user_last(context)
