"""Tool calls a model wrote into its text, made into real ones.

Deep into a long conversation a model may write its tool calls into its
reply's text instead of making them, in the shape its own chat template
uses:

* Hermes / Qwen: ``<tool_call>{"name": …, "arguments": {…}}</tool_call>``
* GLM: ``<tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>``
* Claude / MiniMax: ``<invoke name="…"><parameter name="k">v</parameter></invoke>``,
  bare or inside ``<function_calls>`` / ``<minimax:tool_call>``
* DeepSeek's DSML: the same invoke with ``｜DSML｜`` in each tag
  (``<｜DSML｜invoke name="…">``), one or two pipes of either width.

The agent got text, ran nothing and ended the turn; told to go on, the
model read its own written call back and wrote the next one the same way,
and the session was lost. So a reply that made no call, to a request that
offered tools, is read from the first such tag on: when every block there
is a call of an offered tool with readable arguments, the blocks become
real calls and the reply ends as one that called tools. Text after the
calls (a result the model made up before anything ran) is dropped; the
real result follows. Anything less leaves the reply as it was.

After magpie's text-call recovery (yetone/magpie, MIT).
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from openprogram.models.providers.types import AssistantMessage, TextContent, ToolCall

# <｜DSML｜invoke …>, </ ||DSML|| invoke> … → <invoke …>, </invoke>
_DSML = re.compile(r"<(\s*/)?\s*[|｜][|｜\s]*DSML[|｜\s]*")
_FIRST_MARK = re.compile(r"<tool_call>|<invoke\s+name=|<function_calls>|<minimax:tool_call>")
# Wrappers and closes carried around calls; they say nothing themselves.
_WRAPPER = re.compile(
    r"\s*(?:</?function_calls\s*>|</?minimax:tool_call\s*>|</tool_call\s*>"
    r"|</invoke\s*>|</parameter\s*>|</arg_value\s*>|</arg_key\s*>)"
)
_INVOKE = re.compile(r'\s*<invoke\s+name="([^"]+)"\s*>')
_PARAMETER = re.compile(r'\s*<parameter\s+name="([^"]+)"((?:\s+\w+="[^"]*")*)\s*>')
_STRING_FLAG = re.compile(r'string="(true|false)"')
_INVOKE_END = re.compile(r"</invoke\s*>|<invoke\s+name=|</function_calls\s*>|$")
_PARAMETER_END = re.compile(r'</parameter\s*>|<parameter\s+name=|</invoke\s*>|$')
_TOOL_CALL = re.compile(r"\s*<tool_call>\s*")
_GLM_NAME = re.compile(r"([A-Za-z0-9_.:\-]+)\s*(?=<arg_key>|</tool_call>|$)")
_GLM_PAIR = re.compile(
    r"\s*<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)(?:</arg_value>|(?=<arg_key>|</tool_call>|$))",
    re.S,
)


@dataclass
class WrittenCalls:
    """What a reply's text said: the text before the calls, and the calls."""

    before: str
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def read_written_calls(text: str, offered: Iterable[str]) -> WrittenCalls | None:
    """The calls ``text`` wrote of ``offered`` tools, or ``None`` when it wrote none cleanly."""
    names = set(offered)
    normalized = _DSML.sub(lambda m: "</" if m.group(1) else "<", text)
    first = _FIRST_MARK.search(normalized)
    if not names or first is None:
        return None
    rest = normalized[first.start():]
    calls: list[tuple[str, dict[str, Any]]] = []
    while True:
        rest = _skip_wrappers(rest)
        if not rest.strip():
            break
        call, rest = _one_call(rest, names)
        if call is None:
            if not calls:
                return None
            break  # the model went on as if its call had run
        calls.append(call)
    if not calls:
        return None
    return WrittenCalls(before=normalized[: first.start()].rstrip(), calls=calls)


def recover_text_tool_calls(message: AssistantMessage, tools: Iterable[Any] | None) -> bool:
    """Turn ``message``'s written calls into real ones, in place; whether it did."""
    if not tools or message.stop_reason not in ("stop", "length"):
        return False
    if any(isinstance(block, ToolCall) for block in message.content):
        return False
    texts = [block for block in message.content if isinstance(block, TextContent)]
    if not texts:
        return False
    written = read_written_calls(
        "".join(block.text for block in texts),
        (getattr(tool, "name", "") for tool in tools),
    )
    if written is None:
        return False
    content: list[Any] = [block for block in message.content if not isinstance(block, TextContent)]
    if written.before:
        content.append(TextContent(text=written.before))
    content.extend(
        ToolCall(id=f"call_text_{uuid.uuid4().hex[:16]}", name=name, arguments=arguments)
        for name, arguments in written.calls
    )
    message.content = content
    message.stop_reason = "toolUse"
    return True


def _skip_wrappers(text: str) -> str:
    while True:
        match = _WRAPPER.match(text)
        if match is None or match.end() == 0:
            return text
        text = text[match.end():]


def _one_call(text: str, names: set[str]) -> tuple[tuple[str, dict] | None, str]:
    invoke = _INVOKE.match(text)
    if invoke:
        return _invoke_call(invoke.group(1), text[invoke.end():], names)
    opened = _TOOL_CALL.match(text)
    if not opened:
        return None, text
    body = text[opened.end():]
    if _INVOKE.match(body):  # <tool_call> wrapped around an invoke
        return _one_call(body, names)
    if body.startswith("{"):
        return _hermes_call(body, names)
    return _glm_call(body, names)


def _invoke_call(name: str, text: str, names: set[str]) -> tuple[tuple[str, dict] | None, str]:
    if name not in names:
        return None, text
    arguments: dict[str, Any] = {}
    end = _INVOKE_END.search(text)
    body, rest = text[: end.start()], text[end.start():]
    if end.group(0).startswith("</invoke"):
        rest = text[end.end():]
    while body.strip():
        parameter = _PARAMETER.match(body)
        if parameter is None:
            return None, text
        value_text = body[parameter.end():]
        close = _PARAMETER_END.search(value_text)
        flag = _STRING_FLAG.search(parameter.group(2) or "")
        arguments[parameter.group(1)] = _value(
            value_text[: close.start()], as_string=flag.group(1) == "true" if flag else None
        )
        body = value_text[close.end() if close.group(0).startswith("</parameter") else close.start():]
    return (name, arguments), rest


def _hermes_call(text: str, names: set[str]) -> tuple[tuple[str, dict] | None, str]:
    try:
        document, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None, text
    if not isinstance(document, dict):
        return None, text
    name = document.get("name")
    arguments = document.get("arguments", document.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            return None, text
    if name not in names or not isinstance(arguments, dict):
        return None, text
    return (name, arguments), text[end:]


def _glm_call(text: str, names: set[str]) -> tuple[tuple[str, dict] | None, str]:
    named = _GLM_NAME.match(text)
    if named is None or named.group(1) not in names:
        return None, text
    arguments: dict[str, Any] = {}
    rest = text[named.end():]
    while True:
        pair = _GLM_PAIR.match(rest)
        if pair is None:
            break
        arguments[pair.group(1).strip()] = _value(pair.group(2), as_string=None)
        rest = rest[pair.end():]
    return (named.group(1), arguments), rest


def _value(raw: str, *, as_string: bool | None) -> Any:
    """A written argument: JSON when it is JSON (unless marked a string), else its text."""
    text = raw.strip("\n")
    if as_string is True:
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text
