"""Provider-side web search: request resolution and event/block shapes.

The session's Search switch (and an explicit ``web_search=True`` runtime
kwarg) is an *intent*. Per request it resolves against the API provider the
request goes to (docs/reference/design/providers/web-search.html):

* the provider advertises ``supports_web_search`` → the request option
  ``web_search=True`` is set and the local ``web_search`` function tool is
  removed from that request's tools (the provider runs the search);
* otherwise the option stays off and the local tool stays.

A provider search reaches the loop as a ``serverToolUse`` content block and
``servertool_start`` / ``servertool_end`` events. Everything here reads them
by name (duck typing), so it works with either pydantic blocks or their
dict dumps and needs nothing new from the models package.
"""
from __future__ import annotations

import json
from typing import Any

LOCAL_TOOL_NAME = "web_search"
SERVER_TOOL_BLOCK = "serverToolUse"
SERVER_TOOL_EVENTS = ("servertool_start", "servertool_end")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def provider_supports_web_search(model: Any) -> bool | None:
    """``supports_web_search`` of the provider ``model`` resolves to.

    ``None`` when the registry snapshot has no such attribute (a models
    layer that predates the flag) or cannot be resolved.
    """
    if model is None:
        return None
    try:
        from openprogram.models.providers.catalog.api_registry import (
            resolve_api_provider_snapshot,
        )

        snapshot = resolve_api_provider_snapshot(model)
    except Exception:
        return None
    if not hasattr(snapshot, "supports_web_search"):
        return None
    return bool(getattr(snapshot, "supports_web_search", False))


def resolve_web_search(
    model: Any,
    tools: list | None,
    intent: bool,
    *,
    explicit: bool = False,
) -> tuple[bool | None, list | None]:
    """Resolve the search intent for one request.

    Returns ``(request_option, tools)``. ``request_option`` is ``True`` when
    the provider runs the search, else ``None`` (option off). ``tools`` is
    the list without the local ``web_search`` tool when the provider runs
    it, else the list unchanged.

    ``explicit`` marks a program's ``web_search=True`` kwarg: against a
    models layer that does not report the capability yet, the option is
    kept as passed (its historical behaviour) and the local tool stays.
    """
    if not intent:
        return None, tools
    supported = provider_supports_web_search(model)
    if supported is None:
        return (True if explicit else None), tools
    if not supported:
        return None, tools
    if tools is None:
        return True, None
    kept = [tool for tool in tools if _get(tool, "name") != LOCAL_TOOL_NAME]
    return True, kept


#: The per-turn override ``openprogram --print --no-web`` sends: the Search
#: intent off, and no tool whose name says web (:func:`no_web_tools`).
NO_WEB_OVERRIDE: dict[str, Any] = {"inherit": True, "web_search": False, "web": False}


def no_web_tools(override: Any) -> bool:
    """Whether a turn's override takes every web tool away (``"web": False``):
    the local ``web_search`` and ``web_fetch`` tools, any other tool whose
    name says web, deferred or not, and the provider's own search."""
    return isinstance(override, dict) and override.get("web") is False


def session_search_intent(override: Any, profile: dict | None) -> bool:
    """The Search switch as a turn sees it: per-turn override, else profile.

    ``{"inherit": True, "web_search": True}`` (the session intent), an agent
    profile whose tools config says ``web_search: True``, and a /skill name
    list that includes ``web_search`` all count as on.
    """
    wanted = override if override is not None else (profile or {}).get("tools")
    if isinstance(wanted, dict):
        return wanted.get("web_search") is True
    if isinstance(wanted, (list, tuple)):
        return LOCAL_TOOL_NAME in wanted
    return False


def is_server_tool_block(block: Any) -> bool:
    return _get(block, "type") == SERVER_TOOL_BLOCK


def server_tool_block_at(event: Any) -> Any:
    """The ``serverToolUse`` block a servertool_* event refers to."""
    block = getattr(event, "server_tool", None)
    if block is not None:
        return block
    partial = getattr(event, "partial", None)
    index = getattr(event, "content_index", None)
    content = _get(partial, "content") or []
    if isinstance(index, int) and 0 <= index < len(content):
        candidate = content[index]
        if is_server_tool_block(candidate):
            return candidate
    return None


def server_tool_name(block: Any) -> str:
    # The client shows every provider search as the web_search tool.
    return LOCAL_TOOL_NAME if not _get(block, "name") else str(_get(block, "name"))


def server_tool_query(block: Any) -> str:
    payload = _get(block, "input") or {}
    query = payload.get("query") if isinstance(payload, dict) else None
    return query if isinstance(query, str) else ""


def server_tool_input(block: Any) -> str:
    """JSON ``{"query": ...}`` the tool card shows as the call's input."""
    return json.dumps({"query": server_tool_query(block)}, ensure_ascii=False)


def server_tool_result(block: Any) -> str:
    """One ``title — url`` line per reported source, or ``completed``."""
    lines = []
    for item in _get(block, "results") or []:
        url = _get(item, "url") or ""
        title = _get(item, "title") or ""
        if not url and not title:
            continue
        lines.append(f"{title} — {url}" if title else str(url))
    return "\n".join(lines) if lines else "completed"


def server_tool_failed(block: Any) -> bool:
    return _get(block, "status") == "failed"


def server_tool_card(block: Any) -> dict:
    """The persisted ordered block for one provider search."""
    return {
        "type": "tool",
        "tool": server_tool_name(block),
        "tool_call_id": str(_get(block, "id") or ""),
        "provider_executed": True,
        "input": server_tool_input(block),
        "result": server_tool_result(block),
        "is_error": server_tool_failed(block),
    }


def text_citations(block: Any) -> list[dict]:
    """``citations`` of a text block as ``[{url, title, start, end}]``."""
    out = []
    for item in _get(block, "citations") or []:
        url = _get(item, "url")
        if not url:
            continue
        out.append({
            "url": url,
            "title": _get(item, "title") or "",
            "start": _get(item, "start"),
            "end": _get(item, "end"),
        })
    return out


def text_end_citations(event: Any) -> list[dict]:
    """Citations of the text block a ``text_end`` event closes."""
    partial = getattr(event, "partial", None)
    index = getattr(event, "content_index", None)
    content = _get(partial, "content") or []
    if isinstance(index, int) and 0 <= index < len(content):
        return text_citations(content[index])
    return []
