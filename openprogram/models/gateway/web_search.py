"""Anthropic's ``web_search`` server tool on ``/v1/messages``.

Ported from magpie's gateway (yetone/magpie, ``internal/gateway/search.go``
and the ``KSearch`` case of ``anthropic.go``, MIT). A Messages request that
offers ``web_search_20250305`` (any ``web_search_*`` server tool) is searched
for one of two ways:

* the model's provider searches the web by itself on its API
  (``conversion.searches``): ``messages.parse_request`` sends it with
  ``web_search=True`` and the provider's searches come back as its own
  ``serverToolUse`` blocks;
* otherwise the model is given a ``web_search`` function tool, and the
  gateway answers its calls with OpenProgram's own web search (the
  searcher the server provides, :func:`provide`), for up to :data:`ROUNDS`
  rounds; a round past them is told to answer with what was found. A round
  that also calls the client's own tools goes to the client as it is.

Either way the client hears each search as Anthropic's own: a
``server_tool_use`` block with ``{"query"}`` and a ``web_search_tool_result``
of the pages found (``encrypted_content`` always empty: nothing sealed for
one vendor is handed to a client that may send it to another).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator, Awaitable, Callable

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventServerToolEnd,
    Model,
    ServerToolUse,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
)

from . import conversion as cv
from .conversion import GatewayError

#: Searches a reply may make before it is told to answer (magpie's searchRounds).
ROUNDS = 6
NAME = "web_search"
DESCRIPTION = ("Search the web for current information: news, releases, documentation, prices, anything "
               "after your training or that you are unsure of. Returns what the pages found say, with their "
               "URLs. Cite the URLs you use.")
_SCHEMA = {"type": "object", "properties": {"query": {"type": "string", "description": "What to search for"}},
           "required": ["query"]}

#: ``query -> (text, [{"title", "url", "page_age"?}])``; raises when nothing could search.
Searcher = Callable[[str], tuple[str, list[dict[str, Any]]]]
_searcher: Searcher | None = None


def provide(searcher: Searcher | None) -> None:
    """Set the search that answers a model's ``web_search`` calls (the
    server provides OpenProgram's own web search tool)."""
    global _searcher
    _searcher = searcher


def ours(body: Any, model: Model) -> bool:
    """Whether the gateway runs this request's searches itself: it offers
    Anthropic's web search tool (``messages.server_search``) and the model's
    provider can't search (``conversion.searches``)."""
    from .messages import server_search
    tools = body.get("tools") if isinstance(body, dict) else None
    return bool(server_search(tools)) and _searcher is not None and not cv.searches(model)


# Rendering, as Anthropic's own web search tells it

def server_id(block: ServerToolUse) -> str:
    if block.id.startswith("srvtoolu_"):
        return block.id
    return "srvtoolu_" + (re.sub(r"[^A-Za-z0-9_]", "", block.id) or cv.new_id(""))


def result_block(block: ServerToolUse) -> dict[str, Any]:
    """The ``web_search_tool_result`` block of a search's pages."""
    if block.status == "failed":
        content: Any = {"type": "web_search_tool_result_error", "error_code": "unavailable"}
    else:
        content = [{"type": "web_search_result", "title": str(hit.get("title") or ""), "url": str(hit.get("url") or ""),
                    "encrypted_content": "", "page_age": hit.get("page_age") or None}
                   for hit in block.results if isinstance(hit, dict) and hit.get("url")]
    return {"type": "web_search_tool_result", "tool_use_id": server_id(block), "content": content}


def query_of(block: ServerToolUse) -> dict[str, Any]:
    query = (block.input or {}).get("query") if isinstance(block.input, dict) else None
    return {"query": str(query or "")}


def renders(block: Any) -> bool:
    return isinstance(block, ServerToolUse) and block.name == NAME


# The gateway's own searches

def _tool_name(tools: list[Tool] | None) -> str:
    name = NAME
    while any(tool.name == name for tool in tools or []):
        name = "openprogram_" + name
    return name


def _add(total: Any, usage: Any) -> None:
    for key in ("input", "output", "cache_read", "cache_write", "total_tokens"):
        setattr(total, key, getattr(total, key, 0) + getattr(usage, key, 0))
    total.cost.input += usage.cost.input
    total.cost.output += usage.cost.output
    total.cost.cache_read += usage.cost.cache_read
    total.cost.cache_write += usage.cost.cache_write
    total.cost.total += usage.cost.total


class SearchCall:
    """A Messages reply whose ``web_search`` calls the gateway answers,
    read like an :class:`~openprogram.models.gateway.stream.UpstreamCall`."""

    def __init__(self, model: Model, context: Context, open_round: Callable[[Context], Awaitable[Any]]) -> None:
        self.model = model
        self.tool = _tool_name(context.tools)
        self.context = context.model_copy(update={
            "messages": list(context.messages),
            "tools": [*(context.tools or []), Tool(name=self.tool, description=DESCRIPTION, parameters=_SCHEMA)]})
        self._open = open_round
        self._call: Any = None

    async def start(self) -> "SearchCall":
        """Opens the first round: its failure is the request's."""
        self._call = await self._open(self.context)
        return self

    async def _search(self, call: ToolCall, n: int) -> tuple[ToolResultMessage, ServerToolUse | None]:
        def result(text: str, error: bool = False) -> ToolResultMessage:
            return ToolResultMessage(tool_call_id=call.id, tool_name=call.name, content=[TextContent(text=text)],
                                     is_error=error, timestamp=cv.now_ms())
        if n > ROUNDS:
            return result("No more searches: answer with what was found.", True), None
        query = str((call.arguments or {}).get("query") or "").strip()
        if not query:
            return result("query is empty", True), None
        searcher = _searcher
        try:
            if searcher is None:
                raise LookupError("no web search is set up")
            text, hits = await asyncio.to_thread(searcher, query)
        except Exception as exc:  # noqa: BLE001 - told to the model, as magpie does
            return result(f"search failed: {exc}", True), None
        block = ServerToolUse(id=cv.new_id("srvtoolu_"), name=NAME, input={"query": query},
                              results=[dict(hit) for hit in hits])
        return result(text), block

    async def events(self) -> AsyncIterator[Any]:
        content: list[Any] = []
        usage = None
        for n in range(1, ROUNDS + 3):
            final: AssistantMessage | None = None
            theirs = False
            async for event in self._call.events():
                kind = getattr(event, "type", None)
                if kind == "toolcall_end":
                    if event.tool_call.name == self.tool:
                        continue
                    theirs = True
                if kind == "done":
                    final = event.message
                    continue
                if kind == "start" and n > 1:
                    continue
                yield event
            if final is None:
                raise GatewayError(502, "api_error", "the model ended without a reply")
            mine = [block for block in final.content if isinstance(block, ToolCall) and block.name == self.tool]
            content += [block for block in final.content if not (isinstance(block, ToolCall) and block.name == self.tool)]
            if usage is None:
                usage = final.usage.model_copy(deep=True)
            else:
                _add(usage, final.usage)
            if not mine or theirs or n > ROUNDS + 1:
                stop = final.stop_reason
                if mine and not theirs:
                    stop = "stop"
                message = final.model_copy(update={"content": content, "usage": usage, "stop_reason": stop})
                yield EventDone(reason=stop if stop in ("stop", "length", "toolUse") else "stop", message=message)
                return
            answered = await asyncio.gather(*(self._search(call, n) for call in mine))
            for _, block in answered:
                if block is not None:
                    content.append(block)
                    yield EventServerToolEnd(content_index=len(content) - 1, server_tool=block, partial=final)
            self.context.messages.extend([final, *(result for result, _ in answered)])
            self._call = await self._open(self.context)

    async def final(self) -> AssistantMessage:
        message = None
        async for event in self.events():
            if getattr(event, "type", None) == "done":
                message = event.message
        if message is None:
            raise GatewayError(502, "api_error", "the model ended without a reply")
        return message

