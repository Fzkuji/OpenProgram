"""Agent-loop run stage — pipeline step 4 (dispatcher-split).

``run_loop_blocking`` builds the AgentContext (profile → tools → system
prompt → model), routes history through the context engine
(auto-compact), kicks off ``agent_loop`` and drains its EventStream to
completion inside a fresh asyncio loop.

Re-exported by ``__init__.py`` as ``_run_loop_blocking`` — the seam
tests patch on the dispatcher package. The profile / model helpers are
also patched on the package (``dispatcher._load_agent_profile`` /
``dispatcher._resolve_model``), so this module reads them through the
package attribute at call time instead of freezing them with a
module-level from-import.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, TYPE_CHECKING

from openprogram.runtime.conversation.session_settings import plan_mode as _plan_mode
from openprogram.runtime.conversation.session_settings.session_config import fallback_value, reasoning_from_config, SessionRunConfig
from openprogram.runtime.permissions.approvals.approval import (
    wrap_with_approval as _wrap_with_approval,
)
from openprogram.runtime.conversation.event_parsing import (
    agent_event_to_envelope as _agent_event_to_envelope,
    aiter_event_stream as _aiter_event_stream,
    extract_text as _extract_text,
    extract_usage as _extract_usage,
    shorten as _shorten,
)
from openprogram.state.reply_text import ReplyText
from openprogram.runtime.conversation.model_tools import (
    log_resolved_tools as _log_resolved_tools,
    resolve_tools as _resolve_tools,
)
from openprogram.runtime.conversation.turn_runtime.runtime_attach import _wrap_agentic_runtime_block
from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
from openprogram.models.providers.utils.http_transport.http_client import shutdown_loop_clients as _shutdown_loop_clients
from openprogram.runtime.execution.admission_input.runtime_contract import validate_runtime_contract

if TYPE_CHECKING:
    from openprogram.runtime.conversation.turn_runtime.types import EventCallback
    from openprogram.runtime.agent.turn_request import TurnRequest


def _undeferred(tool):
    from openprogram.programs._host.runtime import as_resident

    return as_resident(tool)


def _keep_signature(ordered: dict, signature: object) -> None:
    if isinstance(signature, str) and signature:
        ordered["signature"] = signature


def _ordered_block_from_content(block: object) -> dict | None:
    """Map one provider content block onto the persisted ordered-card shape."""
    btype = getattr(block, "type", None)
    if btype == "text":
        text = getattr(block, "text", "") or ""
        if not text:
            return None
        ordered = {"type": "text", "text": text}
        _keep_signature(ordered, getattr(block, "text_signature", None))
        from openprogram.runtime.agent.provider_search import text_citations
        citations = text_citations(block)
        if citations:
            ordered["citations"] = citations
        return ordered
    if btype == "serverToolUse":
        # A search the provider ran: shown and persisted like a tool card,
        # never executed here.
        from openprogram.runtime.agent.provider_search import server_tool_card
        card = server_tool_card(block)
        return card if card.get("tool_call_id") else None
    if btype == "thinking":
        text = getattr(block, "thinking", "") or ""
        signature = getattr(block, "thinking_signature", None)
        if not text and not signature:
            return None
        # The signature is what the provider takes back (a Responses
        # reasoning item with its encrypted content): every later request
        # sends it again, so the next turn's prefix is the bytes this turn
        # sent (context/session_dag/render.py). The transcript shows only the text.
        ordered = {"type": "thinking", "text": text}
        _keep_signature(ordered, signature)
        if getattr(block, "redacted", None):
            ordered["redacted"] = True
        return ordered
    if btype == "toolCall":
        tool_id = getattr(block, "id", None)
        name = getattr(block, "name", None)
        args = getattr(block, "arguments", None)
        try:
            payload = json.dumps(args, default=str) if args is not None else None
        except (TypeError, ValueError):
            payload = None
        return {
            "type": "tool",
            "tool": name,
            "tool_call_id": tool_id,
            "input": payload,
        }
    return None


def _cards_are_suffix(haystack: list[dict], needle: list[dict]) -> bool:
    if not needle:
        return True
    if len(haystack) < len(needle):
        return False
    tail = haystack[-len(needle):]
    for left, right in zip(tail, needle):
        if left.get("type") != right.get("type"):
            return False
        if left.get("type") == "tool":
            if str(left.get("tool_call_id") or "") != str(right.get("tool_call_id") or ""):
                return False
        elif str(left.get("text") or "") != str(right.get("text") or ""):
            return False
    return True


def _seed_continuation_cards(
    continuation,
    *,
    ordered_blocks_out: list[dict] | None,
    tool_calls: list[dict],
    reply: ReplyText,
) -> set[str]:
    """Restore the full pre-pause trace, then current-decision fallback.

    ``reply`` takes the restored trace's blocks: a reply that paused is one
    reply, so its flat text is every passage it showed, before the pause
    and after (the stream tap's row already reads that way while it runs).
    """
    seen_tool_ids: set[str] = set()

    def _note_tool(tool_id: object, *, tool: object = None, result=None,
                   is_error=None, input_value=None) -> None:
        if not tool_id:
            return
        key = str(tool_id)
        existing = next(
            (row for row in tool_calls if row.get("tool_call_id") == key),
            None,
        )
        if existing is None:
            tool_calls.append({
                "id": key,
                "tool_call_id": key,
                "tool": tool,
                "result": result,
                "is_error": is_error,
                "input": input_value,
            })
        else:
            if tool is not None and not existing.get("tool"):
                existing["tool"] = tool
            if result is not None and existing.get("result") is None:
                existing["result"] = result
            if is_error is not None and existing.get("is_error") is None:
                existing["is_error"] = is_error
            if input_value is not None and existing.get("input") is None:
                existing["input"] = input_value
        seen_tool_ids.add(key)

    decision_cards: list[dict] = []
    for block in continuation.assistant_message.content:
        ordered = _ordered_block_from_content(block)
        if ordered is None:
            continue
        decision_cards.append(ordered)
        if ordered.get("type") == "tool" and not ordered.get("provider_executed"):
            _note_tool(
                ordered.get("tool_call_id"),
                tool=ordered.get("tool"),
                input_value=ordered.get("input"),
            )

    display = tuple(getattr(continuation, "display", ()) or ())
    if display:
        # The restored trace, kept by the caller or only read for its text.
        trace = ordered_blocks_out if ordered_blocks_out is not None else []
        for card in display:
            if not isinstance(card, Mapping):
                continue
            item = dict(card)
            kind = item.get("type")
            trace.append(item)
            if kind == "tool" and not item.get("provider_executed"):
                _note_tool(
                    item.get("tool_call_id"),
                    tool=item.get("tool"),
                    result=item.get("result"),
                    is_error=item.get("is_error"),
                    input_value=item.get("input"),
                )
        overlap = 0
        for n in range(len(decision_cards), 0, -1):
            if _cards_are_suffix(trace, decision_cards[:n]):
                overlap = n
                break
        present = {
            str(block.get("tool_call_id"))
            for block in trace
            if block.get("type") == "tool" and block.get("tool_call_id")
        }
        for ordered in decision_cards[overlap:]:
            if (
                ordered.get("type") == "tool"
                and ordered.get("tool_call_id")
                and str(ordered["tool_call_id"]) in present
            ):
                continue
            trace.append(dict(ordered))
        reply.extend(trace)
        for result in continuation.tool_results:
            _note_tool(
                result.tool_call_id,
                tool=result.tool_name,
                result=_shorten(result),
                is_error=result.is_error,
            )
        return seen_tool_ids

    reply.extend(decision_cards)
    for ordered in decision_cards:
        if ordered_blocks_out is not None:
            ordered_blocks_out.append(ordered)
    for result in continuation.tool_results:
        _note_tool(
            result.tool_call_id,
            tool=result.tool_name,
            result=_shorten(result),
            is_error=result.is_error,
        )
        tool_id = str(result.tool_call_id or "")
        if (
            ordered_blocks_out is not None
            and tool_id
            and not any(
                block.get("type") == "tool" and block.get("tool_call_id") == tool_id
                for block in (ordered_blocks_out or [])
            )
        ):
            ordered_blocks_out.append({
                "type": "tool",
                "tool": result.tool_name,
                "tool_call_id": tool_id,
                "input": None,
            })
    return seen_tool_ids


_log = logging.getLogger(__name__)

def _named_tools(agent_profile, tools_override):
    """The tool names this turn is limited to, or the override as given.

    A request's own list wins. Otherwise an Agent whose tools are a selected
    list (the read-only Explorer, a saved specialist) is limited to that
    list, so the screen tool is not added to an Agent that did not name it.
    """
    if isinstance(tools_override, list):
        return tools_override
    selection = (agent_profile or {}).get("tools") if isinstance(agent_profile, dict) else None
    if isinstance(selection, dict) and selection.get("mode") == "selected":
        allowed = selection.get("allowed")
        if isinstance(allowed, list):
            return list(allowed)
    return tools_override


def _configure_browser_tool(tools, surface_context, *, enabled: bool | None = None,
                             tools_override=None):
    """Built-in browser Pages are a computer_use_agent target; while they exist the turn gets it.

    Without the browser application there are no Pages to operate. A turn whose
    tool list is given by name (an always-on check-in, a skill's allowed tools)
    gets it only when that list names it: a check-in is read-only, and the
    screen tool would wait on a system-access prompt nobody is there to answer.
    """
    from openprogram.runtime.conversation.surface_context import browser_pages_available, tool_enabled
    from openprogram.programs._host import services as _services

    current = list(tools or [])
    if isinstance(tools_override, list) and "computer_use_agent" not in tools_override:
        return [tool for tool in current if tool.name != "computer_use_agent"], False
    if _services.lookup("browser") is None:
        return current, False
    if enabled is None:
        enabled = tool_enabled(surface_context) or (bool(current) and browser_pages_available(surface_context))
    if not enabled:
        return current, False

    from openprogram.programs import get_agent_tool

    screen_tool = next((tool for tool in current if tool.name == "computer_use_agent"), None)
    if screen_tool is None:
        screen_tool = get_agent_tool("computer_use_agent")
    current = [tool for tool in current if tool.name != "computer_use_agent"]
    if screen_tool is not None:
        current.append(screen_tool)
    return current, screen_tool is not None


def _saved_browser_tool(saved_runtime_contract: Mapping | None) -> bool | None:
    """Whether the checkpoint's turn could operate browser Pages, read from its tools."""
    if saved_runtime_contract is None:
        return None
    names = {tool.get("name") for tool in saved_runtime_contract.get("tools") or []
             if isinstance(tool, Mapping)}
    return "computer_use_agent" in names


def _retain_saved_contract_tools(tools, saved_runtime_contract: Mapping | None):
    """Keep the checkpoint's tool set. Extra live tools do not block resume."""
    if saved_runtime_contract is None:
        return tools
    saved = saved_runtime_contract.get("tools")
    if not isinstance(saved, list):
        return tools
    names = [
        item.get("name") for item in saved
        if isinstance(item, Mapping) and isinstance(item.get("name"), str) and item["name"]
    ]
    live = {tool.name: tool for tool in tools or []}
    return [live[name] for name in names if name in live]


def owns_conversation_contract(req) -> bool:
    """Whether this turn is its conversation's own turn.

    Only such a turn reads and writes the branch's runtime contract (the
    recorded system prompt, ``context.prompt_assembly.system_prompt_node``) and the section
    notes beside its messages (``context.prompt_assembly.turn_context``). A goal
    verification and a background check-in run on the conversation with a
    system prompt and tools of their own: recording theirs would make the
    conversation's next turn rebuild its prompt, and a note kept against
    their prompt would not match the conversation's.
    """
    return not (getattr(req, "goal_verification", None) or getattr(req, "background", False))


def _prompt_channel(req) -> str:
    """The channel the system prompt is written for.

    A turn sent from the App to a chat bound to a channel conversation (its
    session names a ``channel``) has its replies delivered to that channel
    as text, so it is not written for the App: the App's drawing guide
    (```visual blocks) is left out of its prompt.
    """
    source = req.source or ""
    if source != "web":
        return source
    try:
        from openprogram.runtime.context.session_db import default_db
        session = default_db().get_session(req.session_id) or {}
    except Exception:  # noqa: BLE001 - no store means no binding
        return source
    return "" if str(session.get("channel") or "").strip() else source



def _chat_delivery(req) -> str | None:
    """Who reads this turn's reply: a channel name, "" for the App alone, or
    None when the turn is not a message to the user at all (a verifier, a
    sub-agent, a self-update step)."""
    source = req.source or ""
    if req.goal_verification or req.spawned_from_session or source == "agent_spawn" or source.startswith("self_update_"):
        return None
    from openprogram.programs.tools.interaction.chat_send import CHANNEL_SOURCES
    if source in CHANNEL_SOURCES:
        return source
    from openprogram.runtime.conversation import bound_reply
    try:
        target = bound_reply.reply_target(req.session_id)
    except Exception:  # noqa: BLE001 - no store means no binding
        return ""
    return target.channel if target is not None else ""


_SELF_UPDATE_SOURCES = frozenset({"self_update_verify", "self_update_diagnose", "self_update_repair"})


def pins_tools(req) -> bool:
    """Whether this turn keeps the conversation's tools array whatever it
    can use now: the conversation's own turn does; a verification, a
    background check-in and a self-update step build their own."""
    return owns_conversation_contract(req) and (req.source or "") not in _SELF_UPDATE_SOURCES


def in_plan_mode(req) -> bool:
    return req.permission_mode == "plan" or _plan_mode.is_plan_mode(req.session_id)


def turn_availability(req, tools) -> dict[str, str]:
    """``{tool name: why}`` for the tools of ``tools`` this turn cannot use.

    These depend on the turn (where it came from, who is behind it, the
    memory setting), not on the conversation, so the conversation's own turn
    keeps them in its array and refuses their calls (``pins_tools``).
    """
    names = {tool.name for tool in tools or []}
    out: dict[str, str] = {}
    from openprogram.programs.tools.knowledge.memory import MEMORY_TOOL_NAMES
    from openprogram.state.memory.policy import allowed as _memory_allowed
    for name in names & set(MEMORY_TOOL_NAMES):
        if not _memory_allowed(write=name == "memory_update"):
            out[name] = "memory is turned off for this conversation"
    from openprogram.runtime.permissions.identity import SCHEDULER_SPEAKER, outside_text
    # A scheduled run reports with its reply; it runs in its own process,
    # where a report would reach neither the page nor the channels.
    if "always_on_report" in names and req.speaker_id == SCHEDULER_SPEAKER:
        out["always_on_report"] = "a scheduled run reports with its reply"
    # A preference binds every later check-in and scheduled run, so only
    # the owner's own turn writes one, never work a page or a message drives.
    if "always_on_preference" in names and (
            req.speaker_id == SCHEDULER_SPEAKER or req.spawned_from_session or outside_text(req)):
        out["always_on_preference"] = "only the owner's own message can set a report preference"
    # chat_send posts into the chat a channel turn came from; no other
    # source has one.
    from openprogram.programs.tools.interaction.chat_send import CHANNEL_SOURCES
    if "chat_send" in names and req.source not in CHANNEL_SOURCES:
        out["chat_send"] = "this message did not come from a chat channel"
    # A question waits for a person. With none behind the turn it would wait
    # out its time and fail the run, so the turn goes on with what it has.
    from openprogram.runtime.permissions.attended import ASK_TOOLS
    from openprogram.runtime.permissions.policy import nobody_answers
    if nobody_answers(req):
        for name in names & set(ASK_TOOLS):
            out[name] = "nobody is there to answer a question in this run"
    return out


# What a helper started with ``agent`` does not get: it reports, and the
# agent that started it schedules, posts and remembers (Hermes' delegate
# blocklist, reduced). ``agent`` itself goes by the depth limit.
DELEGATE_BLOCKED = frozenset({
    # ``cron`` is the scheduler's older name, still registered.
    "scheduler", "cron", "remind", "save_routine", "always_on_schedule",
    "chat_send", "memory_update",
})
DELEGATE_BLOCKED_REASON = "a helper reports back; the agent that started it does this"


def forked_prompt(req) -> Optional[str]:
    """The system prompt the caller's branch recorded, for a helper forked
    with the conversation (``depth.forked_turn``), or None to build one.
    A fork is only offered with the caller's own Agent (``agent.py``), so
    the recorded prompt is that Agent's."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        forked_turn,
        helper_turn,
    )
    if not (helper_turn() and forked_turn()):
        return None
    anchor = getattr(req, "branch_from", None)
    if not isinstance(anchor, str) or not anchor or not getattr(req, "session_id", None):
        return None
    try:
        from openprogram.runtime.context import Context
        from openprogram.runtime.context.prompt_assembly.system_prompt_node import latest_recorded_prompt
        return latest_recorded_prompt(Context.turn_store(req.session_id), req.session_id, anchor)
    except Exception:  # noqa: BLE001 - built afresh; the cache is not shared
        _log.debug("caller prompt not read for fork in %s", req.session_id, exc_info=True)
        return None


def delegate_blocked(tools, unavailable: dict[str, str]):
    """``(tools, unavailable)`` for a helper's turn: the blocked tools leave
    its list; a forked helper keeps them refused, so its list stays its
    caller's bytes and the prompt cache is shared."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        forked_turn,
        helper_turn,
    )
    if not helper_turn():
        return tools, unavailable
    blocked = {tool.name for tool in tools or []} & DELEGATE_BLOCKED
    if not blocked:
        return tools, unavailable
    if forked_turn():
        return tools, {**unavailable, **{name: DELEGATE_BLOCKED_REASON for name in blocked}}
    return [tool for tool in tools if tool.name not in blocked], unavailable


def refused_while_unavailable(tool, why: str):
    """``tool`` as this turn sends it: listed as always, every call refused
    with ``why``. Not an approval: nothing is asked."""
    import copy as _copy
    from openprogram.runtime.permissions import refusal

    refused = _copy.copy(tool)

    async def execute(call_id, args, cancel=None, on_update=None):
        return refusal.tool_result(refusal.unavailable(tool.name, args, why))

    refused.execute = execute
    for attribute in ("_permission_preflight", "_interaction_manifest"):
        if getattr(refused, attribute, None) is not None:
            setattr(refused, attribute, None)
    refused._permission_managed = False
    refused._unavailable_reason = why
    return refused


def resolve_agent_runtime(
    req: "TurnRequest", *, assistant_msg_id: Optional[str] = None,
    on_event: Optional["EventCallback"] = None,
    saved_runtime_contract: Mapping | None = None,
):
    """Resolve the exact model, prompt, tools, and durable runtime contract."""
    from openprogram.runtime.conversation import turn_runtime as _dispatcher

    event_sink = on_event or (lambda _event: None)
    agent_profile = (
        deepcopy(req.profile_snapshot) if req.profile_snapshot is not None
        else _dispatcher._load_agent_profile(req.agent_id)
    )
    # Plan's hiding is per provider call (lifecycle.wrap_live_permission), not
    # here: a turn whose mode changes keeps the tools the new mode allows.
    tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)
    # The conversation's own turn keeps one tools array for the epoch: a tool
    # this turn cannot use (no channel to post to, nobody to answer, no open
    # Page, memory off …) stays in it, its calls are refused, and the
    # ``tool_availability`` section beside the message says so (Codex, Hermes).
    # Other turns (a verification, a check-in, a self-update step) have their
    # own prompt and lane and drop such tools.
    pinned = pins_tools(req)
    # Always-on tools belong to conversations whose Agent is always-on: that
    # holds for the whole conversation, so they leave the array.
    always_on_only = {"always_on_report", "always_on_schedule", "always_on_preference"}
    if any(tool.name in always_on_only for tool in tools or []):
        from openprogram.runtime.conversation.always_on import agent_for_session
        if agent_for_session(req.session_id) is None:
            tools = [tool for tool in tools if tool.name not in always_on_only]
    # The goal Program is the Programs form's entry; agents use create_goal.
    tools = [tool for tool in tools or [] if tool.name != "goal"]
    unavailable: dict[str, str] = {}
    # Page inventory is transient. Keep this turn's browser-tool selection
    # through reconnects; each actual browser action still validates access.
    saved_browser_tool = _saved_browser_tool(saved_runtime_contract)
    if pinned and saved_browser_tool is None:
        # Whenever the browser application is loaded the screen tool is in
        # the array, at its end; without a Page its calls are refused. A turn
        # with no tools at all (an Agent set to none) gets none.
        tools, browser_enabled = _configure_browser_tool(tools, req.surface_context, enabled=None,
                                                          tools_override=_named_tools(agent_profile, req.tools_override))
        if not browser_enabled and tools:
            union, in_union = _configure_browser_tool(tools, req.surface_context, enabled=True,
                                                      tools_override=_named_tools(agent_profile, req.tools_override))
            if in_union:
                tools = union
                unavailable["computer_use_agent"] = "no browser Page is open in this conversation"
    else:
        tools, browser_enabled = _configure_browser_tool(tools, req.surface_context, enabled=saved_browser_tool,
                                                          tools_override=_named_tools(agent_profile, req.tools_override))
    unavailable.update(turn_availability(req, tools))
    tools, unavailable = delegate_blocked(tools, unavailable)
    if not pinned:
        tools = [tool for tool in tools or [] if tool.name not in unavailable]
        unavailable = {}
    if req.source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        from openprogram.programs import apply_tool_policy

        tools = apply_tool_policy(tools or [], source=req.source)
        browser_enabled = False
    tools = _retain_saved_contract_tools(tools, saved_runtime_contract)
    if req.goal_verification:
        from openprogram.programs.tools.planning.goal import verification
        from openprogram.runtime.execution.run_control import get_current_execution_id
        from openprogram.programs import _is_verifier_read_tool
        verification.require_candidate(req.session_id, vars(req), execution_id=get_current_execution_id())
        tools = [tool for tool in tools or [] if _is_verifier_read_tool(tool) and tool.name != "self_update_observe"]
        browser_enabled = False
    if req.goal_verification or req.source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        # These stages cannot search for tools, so their pinned tools ship with full schemas.
        tools = [_undeferred(tool) for tool in tools or []]
    override = getattr(req, "model_override", None)
    if not override and getattr(req, "session_id", None):
        from openprogram.runtime.conversation.session_settings.session_model import (
            ensure_session_chat_model,
            read_session_chat_model,
            override_string,
        )
        if req.profile_snapshot is not None:
            provider, model_id = read_session_chat_model(req.session_id)
        else:
            provider, model_id = ensure_session_chat_model(
                req.session_id, getattr(req, "agent_id", None),
            )
        override = override_string(provider, model_id)
        if override:
            req.model_override = override
    model = _dispatcher._resolve_model(agent_profile, override)
    _log.info(
        "turn model session=%r override=%r resolved=%s/%s api=%s",
        getattr(req, "session_id", None),
        override,
        getattr(model, "provider", None),
        getattr(model, "id", None),
        getattr(model, "api", None),
    )
    if "image" not in (model.input or []) and any(
        isinstance(attachment, dict) and attachment.get("type") == "image"
        and attachment.get("data") for attachment in (req.attachments or [])
    ):
        raise ValueError(
            "The selected model does not support image input. Choose a model that supports images."
        )
    # The Search switch follows the model: a provider with its own web search
    # runs it (request option, set by run_loop_blocking from this flag) and
    # the local web_search tool leaves this turn's tools; otherwise the local
    # tool stays. Resolved before the prompt and contract see the tools.
    from openprogram.runtime.agent.provider_search import (
        no_web_tools,
        resolve_web_search,
        session_search_intent,
    )
    search_intent = session_search_intent(req.tools_override, agent_profile) and any(
        tool.name == "web_search" for tool in tools or []
    )
    web_search_option, searched_tools = resolve_web_search(model, tools, search_intent)
    if pinned and searched_tools is not None and len(searched_tools) != len(tools or []):
        # The provider runs the search: the local tool stays in the array and
        # its calls are refused, so the Search switch never rewrites it.
        unavailable["web_search"] = "the provider's own web search runs this conversation's searches"
    else:
        tools = searched_tools
    if no_web_tools(req.tools_override):
        # ``--print --no-web``: no web tool of any kind, deferred ones
        # included, and no provider-side search.
        web_search_option = None
        tools = [tool for tool in tools or [] if "web" not in tool.name.lower()]
    req._provider_web_search = bool(web_search_option)
    if not any(tool.name == "tool_search" for tool in tools or []):
        # Without the loader a deferred tool could never be called: each
        # goes out with its schema (Claude Code with tool search off).
        tools = [_undeferred(tool) for tool in tools or []] if tools is not None else tools
    from openprogram.programs import install_turn_tools
    install_turn_tools(tools)
    _log_resolved_tools(req, tools)
    if tools:
        tools = [_wrap_with_approval(tool, req, event_sink) for tool in tools]
        tools = [refused_while_unavailable(tool, unavailable[tool.name]) if tool.name in unavailable else tool
                 for tool in tools]
        if req.goal_verification:
            tools = [verification.wrap_read(tool, req) if tool.name == "read" else tool for tool in tools]
        if assistant_msg_id is not None:
            wrapped = []
            for tool in tools:
                if getattr(tool, "_is_agent_method", False):
                    wrapped.append(_wrap_agentic_runtime_block(
                        tool, req, event_sink, assistant_msg_id,
                    ))
                else:
                    wrapped.append(tool)
            tools = wrapped
    from openprogram.runtime.context.prompt_assembly.components import build_system_prompt
    owner = owns_conversation_contract(req)
    saved_system_prompt = None if saved_runtime_contract is None else saved_prompt(saved_runtime_contract, req)
    # A continuation uses the prompt that produced its saved decision.
    # Memory, dates and project text may change while a human is deciding;
    # rebuilding them is neither necessary nor the context we will execute.
    # The conversation's own turn sends the deferred-tool catalog beside its
    # message, so a tool that connects or goes never changes the prompt.
    catalog_beside = bool(saved_system_prompt is None and getattr(req, "session_id", None) and owner)
    # A helper forked with the conversation (same Agent, its history copied)
    # sends the prompt its caller's branch recorded, byte for byte, so the
    # provider's prompt cache is shared (Claude Code's fork).
    fork_prompt = forked_prompt(req) if saved_system_prompt is None and owner else None
    recordable_prompt = saved_system_prompt if saved_system_prompt is not None else fork_prompt if fork_prompt is not None else build_system_prompt(
        agent_profile, tools=tools,
        channel=_prompt_channel(req),
        chat_delivery=_chat_delivery(req),
        additional_working_dirs=getattr(req, "additional_working_dirs", None),
        # The conversation's own turn gets the plan-mode note beside its
        # message (``plan_mode.note_for``); a turn with a prompt of its own
        # keeps it at the end of that prompt.
        plan_mode=not owner and in_plan_mode(req),
        catalog_beside=catalog_beside,
    )
    if catalog_beside:
        from openprogram.programs import deferred_catalog
        try:
            from openprogram.programs._host.ui.bindings import open_application_ids
            open_apps = open_application_ids(req.session_id)
        except Exception:
            open_apps = set()
        req._deferred_catalog = deferred_catalog(tools, open_apps)
    else:
        req._deferred_catalog = None
    sections: dict = {}
    if saved_system_prompt is None and getattr(req, "session_id", None) and owner:
        # The prompt recorded on this branch is sent again when only its
        # movable sections changed (memory, environment, AGENTS.md, open
        # work, capabilities); the turn then carries each changed section
        # beside its message (_new_turn_agent_context).
        from openprogram.runtime.context import Context
        from openprogram.runtime.context.prompt_assembly.turn_context import kept_sections
        recordable_prompt, sections = kept_sections(
            Context.turn_store(req.session_id), req.session_id, recordable_prompt,
        )
    # The parts the sent prompt is joined from, named so a prompt-cache
    # check can say which one changed (components.remember_composed).
    parts = [("base", recordable_prompt)]
    if saved_system_prompt is None and req.source != "agent_spawn":
        if req.goal_verification:
            parts.append(("goal_verification", verification.instructions(req.session_id, req)))
        elif not owner:
            # A background check-in has a prompt of its own; the
            # conversation's own turn has the Goal beside its message
            # (``goal.chat.goal_note``), so starting or ending a Goal never
            # rebuilds its prompt.
            from openprogram.programs.tools.planning.goal.chat import instructions
            parts.append(("goal", instructions(req.session_id, getattr(req, "branch_id", None))))
    # This call's sections (L2): the open browser Pages, when the browser
    # application is loaded. The conversation's own turn sends them beside
    # its message, as a section that is never in its prompt; another turn
    # (a verification, a check-in) has them at the end of its own prompt.
    from openprogram.runtime.context.prompt_assembly.components import build_turn_sections, remember_composed
    surface_prompt = build_turn_sections(
        agent_profile,
        {"surface_context": req.surface_context, "browser_enabled": browser_enabled},
        channel=_prompt_channel(req), tools=tools,
    )
    if saved_system_prompt is None and getattr(req, "session_id", None) and owner:
        from openprogram.runtime.context.prompt_assembly.turn_context import TOOL_AVAILABILITY, Section, availability_text
        sections["pages"] = Section(current=surface_prompt or "", in_prompt="")
        sections[TOOL_AVAILABILITY] = Section(current=availability_text(unavailable), in_prompt="")
    elif surface_prompt and saved_system_prompt is None:
        parts.append(("pages", f"\n\n{surface_prompt}"))
    req._sections = sections
    system_prompt = remember_composed(parts)
    contract = runtime_contract_snapshot(
        model=model, system_prompt=system_prompt, tools=tools, request=req,
        structured_output=req.response_format, toolset=agent_profile.get("toolset"),
    )
    return agent_profile, tools, recordable_prompt, system_prompt, model, contract


def with_current_location(snapshot: dict, session_id: str) -> dict:
    """Record where the rest of the turn runs in a safe-point snapshot.

    A tool may create or leave a worktree mid-turn (``worktree_create``);
    a resume resolves the location afresh, so a safe point must record the
    location as it is now rather than as it was when the turn began. It is
    resolved the way a resume resolves it, from the session's records and not
    from the running context: ``worktree_create`` binds its worktree only in
    the context the tool ran in.
    """
    semantics = snapshot.get("request_semantics")
    if not isinstance(semantics, dict):
        return snapshot
    from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
    location = runtime_location_for(session_id, use_context=False)
    return {**snapshot, "request_semantics": {**semantics, "execution_location": location}}


def _continuation_agent_context(req, continuation, *, db, model, tools, runtime_contract):
    """Rebuild a resumed turn's AgentContext from its journal, without the context engine."""
    from openprogram.runtime.agent.loop.types import AgentContext

    # Resume is not a new user turn.  Re-running the context engine would
    # perform its normal auto-compact/system-prompt bookkeeping and could
    # change the provider prefix captured by the checkpoint.  Render the
    # persisted branch directly at the user anchor and use the resolved
    # prompt stored by the original provider boundary.
    snapshot = continuation.resolved_snapshot
    durable_prompt = saved_prompt(snapshot, req) if isinstance(snapshot, Mapping) else None
    durable_model = snapshot.get("model")
    if not isinstance(durable_prompt, str) or not isinstance(durable_model, dict):
        raise ValueError("Agent checkpoint resolved snapshot is invalid")
    if durable_model.get("id") != getattr(model, "id", None):
        raise ValueError("Agent checkpoint model no longer resolves")
    from openprogram.runtime.context.session_dag.nodes import render_context
    from openprogram.runtime.context.session_dag.render import render_dag_messages
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter

    graph = SessionNodeWriter(db, req.session_id).load()
    anchor = continuation.user_message_id
    if anchor not in graph.nodes:
        raise ValueError("Agent checkpoint user anchor is not in the session graph")
    # The history ends at the turn's user message. What the turn did after it
    # (earlier decisions, their results, steering) comes from the journal.
    # A clean run started from its own message, and resumes from it.
    read_ids = (
        [anchor] if req.history_override == []
        else render_context(graph, head_id=anchor, frame_entry_seq=-1)
    )
    # The notes render as the turn's first request rendered them: the turn's
    # message records whether it showed the memory section's
    # (_new_turn_agent_context).
    from openprogram.runtime.context.prompt_assembly.turn_context import OWN_PROMPT, conversation_audience
    audience = (conversation_audience(bool((graph.nodes[anchor].metadata or {}).get("memory_map_notes")))
                if owns_conversation_contract(req) else OWN_PROMPT)
    rendered = render_dag_messages(graph, read_ids, reasoning_for=model, audience=audience)
    context_messages = [
        *rendered,
        *continuation.prior_messages(),
    ]
    return AgentContext(
        system_prompt=durable_prompt,
        messages=context_messages,
        tools=tools,
        memory_prefetch="",
        # The turn's message already carries the note it was sent with
        # (render.py renders it from the node); adding it again would send it twice.
        run_note=None if (graph.nodes[anchor].metadata or {}).get("run_note") else req.run_note,
        # The rendered message carries the date it was sent with, if any.
        current_date="",
        runtime_contract=runtime_contract,
    )


def _auto_compact_new_turn(
    context_engine, req, *, db, on_event, agent_profile, model, tools,
    system_prompt, session, history, prep, audience="own_prompt",
    head_id=None, cancel_event=None,
):
    """Compact the session into a durable summary node when the new turn is over budget.

    ``head_id`` is the tip of the branch this turn runs on, the chain that
    is compacted; ``cancel_event`` is the turn's own, so a Stop ends the
    summary request instead of waiting for it.
    """
    # Auto-compact FIRST: the durable fix. It writes a summary node
    # into the DAG, so the shrink survives this turn (panel, next
    # turns, reload). Every turn that reads its own branch's history
    # compacts that branch, selected or not; the summary lands on the
    # chain ending at ``head_id`` and the session's head is left alone. A
    # background turn reads a bounded tail instead.
    if (req.history_override is None and not req.background
            and context_engine.should_auto_compact(prep)):
        try:
            # The branch follows its Agent onto a model whose window the
            # history no longer fits: the model the history was written
            # with summarises it (Codex's ModelDownshift).
            summarizer, trigger = _downshift_model(req, agent_profile), None
            if summarizer is not None:
                from openprogram.runtime.context.token_accounting.tokens import real_context_window
                if real_context_window(summarizer) <= real_context_window(model):
                    # Not a smaller window: the turn's model reads it as well.
                    summarizer = None
            if summarizer is not None:
                trigger = "model_downshift"

            async def _compact(with_model):
                return await context_engine.compact(
                    agent=agent_profile,
                    session_id=req.session_id,
                    model=with_model,
                    on_event=on_event,
                    user_initiated=False,
                    head_id=head_id,
                    cancel_event=cancel_event,
                    **({"trigger": trigger} if trigger else {}),
                )

            async def _compact_with_fallback():
                if summarizer is None:
                    return await _compact(model)
                try:
                    result = await _compact(summarizer)
                except Exception:  # noqa: BLE001 - the turn's model tries next
                    _log.info("downshift compaction with %s failed", getattr(summarizer, "id", summarizer),
                              exc_info=True)
                    result = None
                stopped = cancel_event is not None and cancel_event.is_set()
                if stopped or (result is not None and (result.summary_id or not result.error)):
                    return result
                # The earlier model is gone or failed (missing credentials,
                # withdrawn model): the turn's own model summarises once
                # (Codex's should_retry_with_current_model).
                return await _compact(model)

            loop = asyncio.new_event_loop()
            try:
                compact_res = loop.run_until_complete(_compact_with_fallback())
            finally:
                _shutdown_loop_clients(loop)
                loop.close()
            if compact_res.summary_id:
                # Re-load the post-compact view (summary + kept
                # tail) so the LLM call sees the shorter context.
                from openprogram.runtime.context.compaction.persistence import rendered_history
                history = rendered_history(db, req.session_id, head_id) or history
                prep = context_engine.prepare(
                    agent=agent_profile,
                    session=db.get_session(req.session_id) or session,
                    history=history,
                    model=model,
                    tools=tools,
                    system_prompt=system_prompt,
                    **({"audience": audience} if audience != "own_prompt" else {}),
                )
        except Exception as e:  # noqa: BLE001
            # Auto-compact must never crash a new user turn.
            on_event({"type": "chat_response",
                      "data": {"type": "compaction_failed",
                               "session_id": req.session_id,
                               "error": f"{type(e).__name__}: {e}",
                               "user_initiated": False}})
    return history, prep


def _downshift_model(req, agent_profile):
    """The previous turn's model when this turn's model changed because the
    branch follows its Agent (``turn_settings.model_changed_from``); ``None``
    otherwise, or when that model no longer resolves."""
    previous = (getattr(req, "turn_settings", None) or {}).get("model_changed_from")
    if not previous:
        return None
    from openprogram.runtime.conversation import turn_runtime as _dispatcher
    try:
        return _dispatcher._resolve_model(agent_profile, previous)
    except Exception:  # noqa: BLE001 - the new model summarises instead
        _log.debug("previous model %s does not resolve for a downshift compaction", previous, exc_info=True)
        return None


def _stored_turn_message(db, req, *, assistant_msg_id, history) -> dict:
    """This turn's user message as stored, with what it was sent with."""
    # ``process_user_turn`` stamped the recalled memory and the date on the
    # user node; the resume path must not perform another read/write.
    prefetch_history = history
    prefetch_head = assistant_msg_id or req.user_msg_id
    if prefetch_head:
        prefetch_history = db.get_branch(req.session_id, prefetch_head) or history
    for message in reversed(prefetch_history or []):
        if message.get("role") == "user":
            return message
    return {}



def _off_selection(db, req) -> bool:
    """Whether the turn's branch is no longer the selected one: the person
    switched branches while it ran. It keeps reading and writing its own."""
    branch = getattr(req, "branch_id", None)
    if not branch or not getattr(req, "user_msg_id", None):
        return False
    try:
        return (db.get_session(req.session_id) or {}).get("active_branch_id") not in (None, branch)
    except Exception:
        return False

_VIEW_NOTES = ("plan_mode", "goal", "todo_reminder")


def _view_notes(req, beside: list, view, *, owner: bool) -> list:
    """``beside`` with the notes decided from what the request's rendered
    history shows (``turn_context.turn_view``; None when the engine did not
    say, and every such note then goes as if none were in view).

    The date goes only when it differs from the last date in view. On the
    conversation's own turn, the plan-mode note (``plan_mode.note_for``),
    the Goal note (``goal.chat.goal_note``) and the todo reminder
    (``goal.chat.todo_reminder``). A message sent again decides them again
    from the same view, so it sends the same notes.
    """
    from openprogram.runtime.context.prompt_assembly import turn_context

    date = turn_context.entry_text(beside, "date")
    if date:
        beside = turn_context.put(beside, turn_context.ATTACHMENT, "date",
                                  turn_context.date_to_send(date, view))
    beside = [entry for entry in beside if entry.get("id") not in _VIEW_NOTES]
    if not owner or not getattr(req, "session_id", None):
        return beside
    text, kind = _plan_mode.note_for(in_plan_mode(req), view)
    if text:
        beside = turn_context.put(beside, turn_context.ATTACHMENT, "plan_mode", text, mode=kind)
    from openprogram.programs.tools.planning.goal import chat as goal_chat
    branch = getattr(req, "branch_id", None)
    try:
        note = goal_chat.goal_note(req.session_id, branch, (view or {}).get("goal"))
        todo = (view or {}).get("todo") or {}
        reminder = goal_chat.todo_reminder(req.session_id, branch, todo.get("turns_since_use"),
                                           todo.get("turns_since_reminder")) if view is not None else ""
    except Exception:  # noqa: BLE001 - a Goal store that cannot be read sends no note
        _log.warning("goal note not built for %s", req.session_id, exc_info=True)
        note, reminder = None, ""
    if note is not None:
        text, disclosure, full = note
        beside = turn_context.put(beside, turn_context.ATTACHMENT, "goal", text,
                                  disclosure=disclosure, full=full)
    if reminder:
        beside = turn_context.put(beside, turn_context.ATTACHMENT, "todo_reminder", reminder)
    return beside


def _new_turn_agent_context(
    req, history, on_event, *, db, agent_profile, model, tools,
    system_prompt, recordable_system_prompt, runtime_contract, assistant_msg_id,
    cancel_event=None, turn_compaction_out: Optional[dict] = None,
):
    """Route a new user turn's history through the context engine into an AgentContext.

    ``turn_compaction_out``, when given, receives ``"compact_turn"``: the
    turn's durable compaction between its requests (``turn_compaction.py``).
    """
    from openprogram.runtime.agent.loop.types import AgentContext

    # Route a new user turn through the context engine: applies
    # tool-result aging in-memory and computes the provider budget.
    from openprogram.runtime.context import resolve_engine_for
    _ctx_engine = resolve_engine_for(agent_profile)
    _ctx_engine.on_session_start(req.session_id)
    # The prompt is recorded for new turns only.  A continuation reuses
    # the checkpointed resolved prompt instead.
    from openprogram.runtime.context.prompt_assembly.system_prompt_node import record_system_prompt
    from openprogram.runtime.context.prompt_assembly import turn_context
    owner = owns_conversation_contract(req)
    if owner:
        # Only the conversation's own turn writes the branch's contract.
        record_system_prompt(db, req.session_id, recordable_system_prompt)
    session = db.get_session(req.session_id) or {}
    from openprogram.runtime.context import Context
    from openprogram.runtime.context.session_dag.tail import BACKGROUND_TAIL_TOKENS
    request_context = Context.current()
    if request_context is not None:
        history = request_context.filter_history(history)
    # Only the turns that need them pass these, so a registered engine that
    # predates them keeps serving every ordinary turn.
    bounded = {"tail_tokens": BACKGROUND_TAIL_TOKENS} if req.background else {}
    if req.history_override == []:
        bounded["clean_history"] = True
    if getattr(req, "side_branch", False) or _off_selection(db, req):
        # Beside the selected branch, or on a branch the person switched
        # away from mid-turn: render this turn's own branch.
        bounded["head_id"] = assistant_msg_id or req.user_msg_id
    # Section notes on earlier turns render only in the conversation's own
    # turn, built on its recorded prompt; the memory section's only while
    # the prompt has memory in it (memory on). Calls made inside the turn
    # render none: the audience is passed to this render alone.
    sections = getattr(req, "_sections", None) or {}
    # The memory section's notes stay in view when memory is turned off:
    # history is never taken back (the recorded prompt keeps its memory
    # section until the next compaction, ``turn_context.kept_sections``).
    notes_on = bool(owner)
    audience = turn_context.conversation_audience(notes_on) if owner else turn_context.OWN_PROMPT
    if audience != turn_context.OWN_PROMPT:
        bounded["audience"] = audience
    prep = _ctx_engine.prepare(
        agent=agent_profile,
        session=session,
        history=history,
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        **bounded,
    )
    budget = dict(
        db=db, on_event=on_event, agent_profile=agent_profile, model=model,
        tools=tools, system_prompt=system_prompt, session=session,
    )
    # The chain compacted is this turn's own: it ends at its reply
    # placeholder, or at its message when no placeholder was written.
    turn_tip = getattr(req, "user_msg_id", None)
    if assistant_msg_id and db.message_exists(req.session_id, assistant_msg_id):
        turn_tip = assistant_msg_id
    history, prep = _auto_compact_new_turn(
        _ctx_engine, req, history=history, prep=prep, audience=audience,
        head_id=turn_tip, cancel_event=cancel_event, **budget,
    )
    stored = _stored_turn_message(db, req, assistant_msg_id=assistant_msg_id, history=history)
    # A section changed since what this request shows the model (the system
    # prompt's section, or the last note of it in the rendered history): its
    # current text goes beside this message, stored on it so every later
    # request renders the same bytes.
    user_msg_id = stored.get("id") or req.user_msg_id
    reported = getattr(prep, "section_notes", None)
    entries = turn_context.entries(stored)
    # A message sent again (a retried attempt) keeps only the notes this
    # attempt sends; what happened before it (its date, a rewind) stays.
    beside = [entry for entry in entries if entry["kind"] != turn_context.SECTION]
    for ident, section in sections.items():
        if reported is not None:
            note = turn_context.note_to_send(ident, section, reported.get(ident))
        else:
            # An engine that does not say which notes its render shows: any
            # stored note it may show counts as what the model has.
            note = turn_context.note_to_send(ident, section, None, stored=turn_context.last_stored(
                history, ident, exclude=user_msg_id))
        beside = turn_context.put(beside, turn_context.SECTION, ident, note)
    catalog_now = getattr(req, "_deferred_catalog", None)
    if catalog_now is not None:
        # The deferred tools: all of them when no earlier note is in view
        # (the conversation's start, the first turn after a summary), then
        # only what was added and removed (``programs.catalog_note``).
        from openprogram.programs import catalog_note
        if reported is not None:
            catalog_before = turn_context.catalog_shown(reported)
        else:
            catalog_before = turn_context.catalog_stored(history, exclude=user_msg_id)
        beside = turn_context.put(beside, turn_context.SECTION, turn_context.CATALOG,
                                  catalog_note(catalog_before, catalog_now), catalog=catalog_now)
    beside = _view_notes(req, beside, getattr(prep, "turn_view", None), owner=owner)
    # ``memory_map_notes`` records whether this turn's requests render the
    # memory section's notes, so a resumed turn renders exactly what it sent.
    if user_msg_id and (beside != entries or bool(stored.get("memory_map_notes")) != notes_on):
        db.merge_node_metadata(req.session_id, user_msg_id, {
            **turn_context.node_metadata(beside), "memory_map_notes": notes_on})
    if turn_compaction_out is not None:
        from openprogram.runtime.conversation.turn_runtime.turn_compaction import turn_compaction_for
        turn_compaction_out["compact_turn"] = turn_compaction_for(
            req, engine=_ctx_engine, db=db, agent_profile=agent_profile, model=model,
            tools=tools, system_prompt=system_prompt, render_kwargs=bounded,
            turn_tip=turn_tip, prefix_len=len(prep.agent_messages), on_event=on_event,
            cancel_event=cancel_event,
        )
    return AgentContext(
        system_prompt=system_prompt,
        messages=prep.agent_messages,
        tools=tools,
        memory_prefetch=turn_context.entry_text(beside, "memory_prefetch"),
        run_note=req.run_note,
        # The date stored on the message; a message stored without one is
        # rendered without one, so none is added now.
        current_date=turn_context.entry_text(beside, "date"),
        # The rewind note and the section notes, between the date and the
        # recalled memory, as later renders of the message put them.
        turn_notes=turn_context.notes_text(beside, audience),
        runtime_contract=runtime_contract,
    )


def _restore_continuation_runtime(continuation, runtime_contract) -> None:
    """Check the live runtime still matches the checkpoint.

    Its deferred tools need no reloading: the resumed request reads them from
    the load records of the messages it resumes with.
    """
    from openprogram.runtime.agent.durable_steps import retained_function_names
    from openprogram.runtime.execution import default_store
    validate_runtime_contract(continuation.resolved_snapshot, runtime_contract, durable_function_names=retained_function_names(default_store(), continuation.checkpoint.execution_id))


def _steering_source(req, *, model, db, assistant_msg_id, execution_context):
    """Build the loop's steering callback over the execution's durable steer queue.

    Returns the callback and the map of delivered steering messages that the
    drain publishes when each one's message_start arrives.
    """
    canonical_execution = bool((execution_context or {}).get("canonical_execution"))
    durable_steer_inputs = (execution_context or {}).get("steer_inputs")
    durable_steer_consumed_ids = (execution_context or {}).get("steer_consumed_ids")

    delivered_steers: dict[int, dict] = {}

    def _steer_images(payload: Mapping) -> list:
        # The text already names every file by path; the model also sees the
        # pictures when it takes images, as with a sent message.
        if "image" not in (model.input or []):
            return []
        from openprogram.models.providers.types import ImageContent

        blocks = []
        for image in payload.get("images") or []:
            try:
                data = base64.b64encode(Path(image["path"]).read_bytes()).decode("ascii")
                blocks.append(ImageContent(data=data, mime_type=image.get("media_type") or "image/png"))
            except (OSError, KeyError, TypeError):
                _log.warning("steer image dropped for session %s", req.session_id, exc_info=True)
        return blocks

    async def _get_one_steering_message():
        # Runtime steering is available only to a canonical execution and is
        # consumed from its durable execution command queue at a safe point.
        # Session-local inboxes are not a second steering transport. A
        # helper's Job is a canonical execution of its own, so the person's
        # "Add instruction" and its spawner's send_message reach it here.
        if not canonical_execution:
            return []
        if not isinstance(durable_steer_inputs, list) or not durable_steer_inputs:
            return []
        item = durable_steer_inputs.pop(0)
        payload = item.get("payload") if isinstance(item, Mapping) else None
        text = payload.get("message") if isinstance(payload, Mapping) else None
        command_id = item.get("command_id") if isinstance(item, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            return []
        if isinstance(durable_steer_consumed_ids, set) and isinstance(command_id, str):
            durable_steer_consumed_ids.add(command_id)
        from openprogram.state.store.session.nodes import Call, ROLE_USER
        from openprogram.models.providers.types import TextContent, UserMessage
        from openprogram.state.store import SessionNodeWriter

        if isinstance(command_id, str):
            import hashlib
            message_id = hashlib.sha256(f"{req.session_id}:{command_id}".encode()).hexdigest()[:24]
        else:
            message_id = uuid.uuid4().hex[:12]
        timestamp = time.time()
        predecessor = (
            getattr(req, "_steering_tail_id", None)
            or req.user_msg_id
            or "ROOT"
        )
        # A steer another agent sent (send_message to a helper it started)
        # is agent traffic, not the person speaking.
        from_agent = payload.get("origin") == "agent"
        metadata = {
            "source": "agent" if from_agent else "web",
            "steering": True,
            "command_id": command_id,
            "agent_id": req.agent_id,
            "origin": "agent" if from_agent else "person",
        }
        if from_agent:
            metadata["message_origin"] = "agent_delivery"
        from openprogram.state.memory.policy import stamp
        metadata.update(stamp(req))
        try:
            from openprogram.state.authority import normalize_authority, stamp_schema

            metadata.update(normalize_authority(req))
            stamp_schema(metadata)
            persist = (execution_context or {}).get("persist_steer")
            delivery = (
                persist(command_id, message_id)
                if callable(persist) and isinstance(command_id, str)
                else contextlib.nullcontext()
            )
            message = UserMessage(content=[TextContent(text=text), *_steer_images(payload)],
                                  timestamp=int(timestamp * 1000))
            if len(message.content) > 1:
                # The pictures go back after the text in later requests
                # too (``store.session.media``).
                from openprogram.state.store.session.files.media import records_for_blocks
                try:
                    steer_media = records_for_blocks(db._session_dir(req.session_id), message.content)  # noqa: SLF001
                except Exception:  # noqa: BLE001 - the steer is delivered without the copy
                    _log.warning("steer media not kept for session %s", req.session_id, exc_info=True)
                    steer_media = []
                if steer_media:
                    metadata["media"] = steer_media
            record_steer = (execution_context or {}).get("record_steer")
            with delivery:
                writer = SessionNodeWriter(db, req.session_id, advance_head=False)
                # Replay the idempotent append even when the memory index has
                # the ID: an earlier history write may have failed after indexing.
                writer.append(Call(
                    id=message_id,
                    created_at=timestamp,
                    role=ROLE_USER,
                    output=text,
                    predecessor=predecessor,
                    metadata=metadata,
                ))
                writer.update(assistant_msg_id, predecessor=message_id)
                if not db.has_persisted_ancestor(req.session_id, message_id, assistant_msg_id):
                    raise RuntimeError("steering message is not persisted in the assistant branch")
                if callable(record_steer) and isinstance(command_id, str):
                    record_steer(command_id, message_id, message.model_dump(mode="json"))
        except Exception as exc:
            if isinstance(durable_steer_consumed_ids, set) and isinstance(command_id, str):
                durable_steer_consumed_ids.discard(command_id)
            if getattr(exc, "code", None) == "steer_withdrawn":
                # Taken back before delivery: nothing was written, and the
                # inputs queued behind it are still due at this boundary.
                return None
            # Retain the command for another safe point if either the user
            # message or its delivery receipt could not be persisted.
            if isinstance(durable_steer_inputs, list):
                durable_steer_inputs.insert(0, item)
            _log.warning(
                "failed to persist steering for session %s",
                req.session_id,
                exc_info=True,
            )
            return []
        req._steering_tail_id = message_id
        # The producer can run ahead of the event consumer. Publish only when
        # its message_start reaches the ordered event stream, after prior output.
        delivered_steers[id(message)] = {
            "type":"user_message", "session_id":req.session_id, "msg_id":message_id,
            "content":text, "source":metadata["source"], "steering":True,
            "assistant_msg_id":assistant_msg_id, "command_id":command_id,
            "timestamp":timestamp, "predecessor":predecessor,
        }
        return [message]

    async def _get_steering_messages():
        # All commands already queued at this boundary belong to the next
        # decision. Leaving one behind would request another answer on resume.
        messages = []
        while isinstance(durable_steer_inputs, list) and durable_steer_inputs:
            delivered = await _get_one_steering_message()
            if delivered is None:
                continue
            if not delivered:
                break
            messages.extend(delivered)
        return messages

    return _get_steering_messages, delivered_steers


def _recorded_prompt(req) -> Optional[str]:
    """The system prompt recorded on this turn's branch, up to its message."""
    if not getattr(req, "session_id", None):
        return None
    try:
        from openprogram.runtime.context import Context
        from openprogram.runtime.context.prompt_assembly.system_prompt_node import latest_recorded_prompt
        return latest_recorded_prompt(Context.turn_store(req.session_id), req.session_id,
                                      getattr(req, "user_msg_id", None) or None)
    except Exception:  # noqa: BLE001 - no record: the checkpoint keeps the text
        return None


def prompt_by_reference(snapshot: dict, req, recorded: dict) -> dict:
    """``snapshot`` as the checkpoint keeps it.

    The branch's record is the one authority for the conversation's prompt
    (Codex persists its ``base_instructions`` once; Hermes pins the prompt
    bytes per session): when the turn sent the prompt recorded on its
    branch, the checkpoint keeps only its hash (``system_prompt_sha256``)
    and a resume reads the text from the record (:func:`saved_prompt`). A
    turn with a prompt of its own keeps the text.
    """
    text = snapshot.get("system_prompt")
    if not isinstance(text, str) or not text or not owns_conversation_contract(req):
        return snapshot
    if "text" not in recorded:
        recorded["text"] = _recorded_prompt(req)
    if recorded["text"] != text:
        return snapshot
    from openprogram.runtime.execution.admission_input.runtime_contract import system_prompt_sha256
    kept = {key: value for key, value in snapshot.items() if key != "system_prompt"}
    kept["system_prompt_sha256"] = system_prompt_sha256(text)
    return kept


def saved_prompt(saved: Mapping, req) -> str:
    """The prompt a checkpoint names: its text, or the branch's recorded
    prompt when it keeps the hash only. A record that no longer matches
    refuses the resume."""
    text = saved.get("system_prompt")
    if isinstance(text, str):
        return text
    digest = saved.get("system_prompt_sha256")
    from openprogram.runtime.execution.admission_input.runtime_contract import system_prompt_sha256
    recorded = _recorded_prompt(req)
    if not isinstance(digest, str) or recorded is None or system_prompt_sha256(recorded) != digest:
        raise ValueError("Agent checkpoint system prompt no longer matches the conversation's record")
    return recorded


def _safe_point_hook_for(req, *, tools, assistant_msg_id, execution_context):
    """Build the loop's safe-point hook, or None when the execution has no safe-point callback."""
    safe_point_callback = (execution_context or {}).get("safe_point_hook")
    last_safe_point_snapshot: dict[str, object] = {}
    # The branch's recorded prompt, read once for the turn.
    recorded: dict[str, Optional[str]] = {}

    async def _safe_point_hook(kind: str, payload: dict) -> bool:
        nonlocal last_safe_point_snapshot
        if safe_point_callback is None:
            return False
        durable_payload = dict(payload)
        snapshot = durable_payload.get("resolved_snapshot")
        if isinstance(snapshot, dict):
            last_safe_point_snapshot = dict(snapshot)
        elif last_safe_point_snapshot:
            durable_payload["resolved_snapshot"] = dict(last_safe_point_snapshot)
        if isinstance(durable_payload.get("resolved_snapshot"), dict):
            durable_payload["resolved_snapshot"] = prompt_by_reference(with_current_location(
                durable_payload["resolved_snapshot"], req.session_id,
            ), req, recorded)
        durable_payload["turn"] = {
            "user_message_id": req.user_msg_id or "",
            "assistant_message_id": assistant_msg_id or "",
            "base_history_head_id": req.user_msg_id or "",
            "branch_id": "main",
        }
        result = safe_point_callback(kind, durable_payload)
        if inspect.isawaitable(result):
            result = await result
        payload.update(durable_payload)
        # AgentLoop treats a true result as an ownership hand-off.  Preserve
        # it verbatim so no later tool, persistence, or finalize stage runs.
        handed_off = bool(result)
        if handed_off and execution_context is not None:
            execution_context["safe_point_committed"] = True
        return handed_off

    return _safe_point_hook if safe_point_callback is not None else None


def _permission_context_transform(req, continuation):
    """Build the transform that appends the request context and the current permission mode."""
    async def _permission_context(messages, _cancel):
        from openprogram.models.providers.types import TextContent, UserMessage
        from openprogram.runtime.permissions import current_permission_request
        from openprogram.runtime.context import Context
        request_context = Context.current()
        if request_context is not None and continuation is None:
            messages = request_context.add_to_messages(messages)
        current = current_permission_request(req)
        version = getattr(current, "_permission_version", 0)
        if not version:
            return messages
        return [*messages, UserMessage(
            role="user", content=[TextContent(text=(
                f"[Runtime permission setting, owner-confirmed version {version}] "
                f"The current permission mode is {current.permission_mode}. "
                "Use this current mode instead of earlier mode descriptions. "
                "Limits on who you are, the owner's rules, and Sandbox when it is on still apply."
            ))], timestamp=0,
        )]

    return _permission_context


def _agent_loop_config(req, *, agent_profile, model, transform_context,
                       get_steering_messages, safe_point_hook):
    """Assemble the AgentLoopConfig from the request, the agent profile and the turn's callbacks."""
    from openprogram.runtime.agent.loop.types import AgentLoopConfig
    # _default_convert_to_llm filters out non-LLM messages (e.g. our
    # custom error / system entries) — agent.py already provides this.
    from openprogram.runtime.agent.loop.driver import _default_convert_to_llm
    from openprogram.models.providers.settings.fast import resolve_service_tier
    from openprogram.runtime.permissions.policy import nobody_answers

    turn_options = agent_profile.get('_agent_turn_options') or {}
    return AgentLoopConfig(
        model=model,
        max_iterations=turn_options.get('max_iterations'),
        tool_choice=turn_options.get('tool_choice'),
        parallel_tool_calls=turn_options.get('parallel_tool_calls'),
        convert_to_llm=_default_convert_to_llm,
        transform_context=transform_context,
        # Pass session_id so providers that support it
        # (openai_codex/openai_responses/azure) set prompt_cache_key on
        # every request. Without it OpenAI prompt cache can only match
        # the anonymous static prefix (~ instructions), so longer
        # conversations sit at ~10-20% hit rate even though the message
        # tail is identical turn-to-turn.
        session_id=req.session_id,
        # The branch's effort, else the one fallback order every entry
        # shares (Agent, then project, then the provider default).
        reasoning=reasoning_from_config(SessionRunConfig(
            thinking_effort=req.thinking_effort
            if req.thinking_effort is not None
            else fallback_value(req.session_id, "thinking_effort",
                                agent=agent_profile.get("thinking_effort"))[0],
        )),
        # Per-turn speed tier → SimpleStreamOptions.service_tier →
        # provider request body. Per-turn value wins; else the agent
        # profile's stored default; else None (provider default).
        service_tier=resolve_service_tier(model, (
            req.service_tier
            if req.service_tier is not None
            else agent_profile.get("service_tier")
        )),
        response_format=req.response_format,
        # Provider-side search, resolved with the tools in resolve_agent_runtime.
        web_search=True if getattr(req, "_provider_web_search", False) else None,
        get_steering_messages=get_steering_messages,
        safe_point_hook=safe_point_hook,
        # A turn nobody is behind has nobody to keep posted.
        progress_notes=not nobody_answers(req),
    )


#: A stop reaches the loop through ``wakeups`` at once; this check catches
#: only code that sets the Event without waking it.
_STOP_BACKSTOP_S = 0.5


def _wake_on_stop(cancel_event: threading.Event, loop_cancel: asyncio.Event):
    """Set ``loop_cancel`` on this loop the moment ``cancel_event`` is set.

    The callback is registered beside the Event (``run_control.wakeups``),
    never on it: request options holding the Event are deep-copied. Setting
    ``loop_cancel`` fires its own wakeups, which cancel the task reading the
    provider stream (``agent.loop.lifecycle``). No thread is started.
    Returns the function that unregisters it.
    """
    from openprogram.runtime.execution.run_control import wakeups

    loop = asyncio.get_running_loop()

    def wake() -> None:
        if not loop_cancel.is_set():
            loop_cancel.set()
            wakeups.fire(loop_cancel)

    handle = wakeups.register(cancel_event, wake, loop=loop)

    async def backstop() -> None:
        while not cancel_event.is_set():
            await asyncio.sleep(_STOP_BACKSTOP_S)
        wake()

    task = loop.create_task(backstop())
    if cancel_event.is_set():
        wake()

    def done() -> None:
        wakeups.unregister(handle)
        task.cancel()

    return done


def _start_agent_stream(req, context, config, continuation, loop_cancel, stream_fn):
    """Start agent_loop with the user's prompt, or resume the continuation's decision."""
    from openprogram.runtime.agent.loop import agent_loop, agent_loop_resume

    # Normal turns add their prompt once. A continuation already owns a
    # persisted user anchor and restores its provider decision directly.
    # plus UserMessage prompt added by agent_loop exactly once.
    # The old user_already_persisted branch used agent_loop_continue
    # with history that included the duplicated user_msg as both
    # the tail of context.messages AND the "current turn" prompt,
    # which broke OpenAI prompt cache because the prefix's last
    # item flipped between turns (user N's duplicate → user N's
    # assistant reply).
    from openprogram.models.providers.types import (
        ImageContent, TextContent, UserMessage,
    )
    if continuation is None:
        content_blocks: list = []
        if req.user_text:
            from openprogram.runtime.permissions.authority import render_model_input_from
            content_blocks.append(TextContent(
                text=render_model_input_from(req, req.user_text)
            ))
        for att in (req.attachments or []):
            if not isinstance(att, dict):
                continue
            if att.get("type") == "image":
                try:
                    content_blocks.append(ImageContent(
                        data=att.get("data") or "",
                        mime_type=att.get("media_type") or "image/png",
                    ))
                except Exception:
                    _log.warning("image attachment dropped for session %s",
                                 req.session_id, exc_info=True)
        if not content_blocks:
            content_blocks = [TextContent(text="")]
        prompt = UserMessage(
            content=content_blocks,
            timestamp=int(time.time() * 1000),
        )
        return agent_loop([prompt], context, config,
                          loop_cancel, stream_fn)
    return agent_loop_resume(
        continuation, context, config, loop_cancel, stream_fn,
    )


@dataclass
class _TurnTally:
    """What the drain accumulates from the event stream for the turn's result."""

    # The turn's flat text so far (``reply_text``): every response's blocks
    # in order, so passages a tool call, reasoning or a steering message
    # came between are a blank line apart.
    reply: ReplyText = field(default_factory=ReplyText)
    usage_total: dict = field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "provider_request_count": 0, "agent_iteration_count": 0,
        "context_tokens": 0,
    })
    tool_calls: list[dict] = field(default_factory=list)
    seen_tool_ids: set[str] = field(default_factory=set)
    # Capture tool_use inputs so we can rebuild the same
    # collapsible scaffold on reload. tool_execution_end events
    # don't carry the input args, so we stash them at start time.
    tool_inputs_by_id: dict[str, dict] = field(default_factory=dict)
    # The provider response streaming now (``open_response``) and the one
    # that ended but whose tools have not all returned (``ended_response``):
    # a Stop keeps what they hold (``_keep_stopped_response``).
    open_response: Any = None
    ended_response: Any = None
    # The text and reasoning of the response streaming now that were sent
    # on as stream frames, one entry per content block. A provider adds a
    # delta to its partial message before the loop reads the delta's event,
    # so the partial can hold text nobody was sent; a cut response keeps
    # this, never the partial.
    forwarded: list[dict] = field(default_factory=list)


_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _seed_tally_from_continuation(tally: _TurnTally, continuation, ordered_blocks_out) -> None:
    """Carry the turn's cards, tool calls and usage from before the pause into the tally.

    A turn that paused and went on is one reply: its usage, like its text,
    covers every provider response the turn got, each counted the way a live
    response is (``_record_turn_end``). The journal holds one decision per
    response; the last is the one the turn paused on. This is the reply
    row's own count; what a Goal or a budget is charged comes from the
    ledger's receipt of each request, written when that request ended, so
    nothing is charged again here.
    """
    tally.seen_tool_ids = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered_blocks_out,
        tool_calls=tally.tool_calls,
        reply=tally.reply,
    )
    journal = getattr(continuation, "journal", None)
    responses = [decision.message for decision in getattr(journal, "decisions", None) or ()]
    if not responses:
        responses = [continuation.assistant_message]
    tiers: list = []
    totals = dict.fromkeys(_TOKEN_KEYS, 0)
    for response in responses:
        usage = _extract_usage(response)
        tiers.extend(usage.get("service_tiers") or ())
        for key in _TOKEN_KEYS:
            totals[key] += int(usage.get(key) or 0)
    if tiers:
        tally.usage_total["service_tiers"] = tiers
    tally.usage_total.update(
        totals,
        provider_request_count=len(responses),
        agent_iteration_count=len(responses),
    )


def _raise_provider_error(msg) -> None:
    """Re-raise a stream-level provider failure as LLMError."""
    # Stream-level provider failure (HTTP 4xx/5xx
    # surfaced as an error event, not an exception).
    # Without this the turn "succeeds" with empty
    # text — a blank assistant bubble. Re-raise as
    # LLMError so the dispatcher's exception path
    # renders the red error bubble with taxonomy,
    # same as a synchronously-raised failure.
    from openprogram.models.providers.utils.failures.errors import (
        ErrorReason, LLMError,
    )
    _reason_val = getattr(msg, "error_reason", None)
    try:
        _reason = (ErrorReason(_reason_val)
                   if _reason_val else ErrorReason.UNKNOWN)
    except ValueError:
        _reason = ErrorReason.UNKNOWN
    raise LLMError(
        message=(getattr(msg, "error_message", "") or
                 "provider returned an error"),
        reason=_reason,
        retryable=bool(
            getattr(msg, "error_retryable", None) or False),
        retry_after_s=getattr(
            msg, "error_retry_after_s", None),
        provider=getattr(msg, "provider", None),
        model=getattr(msg, "model", None),
        failure=getattr(msg, "error_cause", None),
    )


def _record_turn_end(ev, tally: _TurnTally, *, req, on_event, ordered_blocks_out,
                     model=None) -> None:
    """Fold one provider call's message into the turn's text, usage and ordered cards.

    ``model`` is the Model the turn resolved; its context-usage report names
    it and its window, so occupancy shows the window compaction reads.
    """
    tally.usage_total["provider_request_count"] += 1
    tally.usage_total["agent_iteration_count"] += 1
    msg = getattr(ev, "message", None)
    if getattr(msg, "structured_output_mode", None) is not None:
        req.structured_output = getattr(msg, "structured_output", None)
        req.structured_output_mode = msg.structured_output_mode
        req.structured_output_attempt = getattr(
            msg, "structured_output_attempt", None
        )
    if getattr(msg, "stop_reason", None) == "error":
        _raise_provider_error(msg)
    text = _extract_text(msg)
    _add_response_text(tally, msg)
    if text:
        req.last_round_text = text
    usage = _extract_usage(msg)
    if usage.get("service_tiers"):
        tally.usage_total.setdefault("service_tiers", []).extend(usage["service_tiers"])
    for k in ("input_tokens", "output_tokens",
              "cache_read_tokens", "cache_write_tokens"):
        tally.usage_total[k] += usage.get(k, 0)
    # 当前上下文占用 ≈ 最后一次调用的 prompt 体积
    # （input + cache_read）。turn 内多次调用的 input
    # 之和会远超窗口，只能用于计费，不能用于占用率。
    prompt_tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_tokens", 0)
    )
    # Empty terminal events must not erase the last request.
    if prompt_tokens > 0:
        tally.usage_total["context_tokens"] = prompt_tokens
        tally.usage_total["request_input_estimate"] = usage.get("request_input_estimate", 0)
    if prompt_tokens > 0:
        report = {
            "type": "context_usage",
            "session_id": req.session_id,
            "msg_id": req.user_msg_id,
            "usage": usage,
        }
        if model is not None and getattr(model, "provider", None) and getattr(model, "id", None):
            from openprogram.runtime.context.token_accounting.tokens import known_context_window
            report["model"] = f"{model.provider}/{model.id}"
            report["context_window"] = known_context_window(model)
        on_event({"type": "chat_response", "data": report})
    # Build ordered blocks from msg.content so the
    # webui can render thinking / text / tool cards
    # interleaved in their original LLM emission
    # order. Without this the bubble shows all LLM
    # text first and then every tool card stacked at
    # the bottom — wrong when the LLM said something,
    # called a tool, then kept narrating.
    if ordered_blocks_out is not None and msg is not None:
        _append_response_blocks(msg, tally, ordered_blocks_out)


def _add_response_text(tally: _TurnTally, msg) -> None:
    """Add one provider response's blocks to the turn's flat text."""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        tally.reply.add({"type": "text", "text": content})
        return
    for blk in content or []:
        ordered = _ordered_block_from_content(blk)
        if ordered is not None:
            tally.reply.add(ordered)


def _append_response_blocks(msg, tally: _TurnTally, ordered_blocks_out: list[dict]) -> None:
    """Add one provider response's cards to the turn's ordered blocks."""
    try:
        # The first card of each provider response says so: a later
        # render sends this response back as the one message it was
        # (context/session_dag/render.py), its calls together and its text before them.
        round_start = True
        for blk in getattr(msg, "content", None) or []:
            ordered = _ordered_block_from_content(blk)
            if ordered is None:
                continue
            tool_id = ordered.get("tool_call_id")
            if (
                ordered.get("type") == "tool"
                and tool_id
                and str(tool_id) in tally.seen_tool_ids
            ):
                continue
            if round_start:
                ordered["round_start"] = True
                round_start = False
            ordered_blocks_out.append(ordered)
            if ordered.get("type") == "tool" and tool_id:
                tally.seen_tool_ids.add(str(tool_id))
    except Exception:
        # Provider block shapes vary; a normalisation miss
        # costs one rendered block, not the turn.
        _log.debug(
            "provider block normalisation failed",
            exc_info=True,
        )


def _keep_stopped_response(tally: _TurnTally, ordered_blocks_out: list[dict] | None) -> None:
    """What a Stop keeps of the response it cut (OpenCode ``to-llm-message``).

    Responses that ended are already in the ordered blocks, signatures and
    all. A response that ended while its tools ran goes in whole: its calls
    are complete and each gets a result (``persistence``). A response cut
    while streaming keeps the text that was sent on (``tally.forwarded``)
    as one last unsigned response (``partial``); its reasoning is shown but
    never sent (an unsigned thinking block is left out of a request,
    ``context.session_dag.render``) and a call whose arguments had not all arrived is
    dropped. The kept text joins the turn's text, so the reply's content,
    its result frame and its blocks say the same.
    """
    ended, cut = tally.ended_response, tally.open_response
    if ended is not None and getattr(ended, "stop_reason", None) not in ("aborted", "error"):
        _add_response_text(tally, ended)
        if ordered_blocks_out is not None:
            _append_response_blocks(ended, tally, ordered_blocks_out)
        return
    if cut is None and ended is None:
        return
    kept = [
        {"type": block["type"], "text": block["text"], "partial": True}
        for block in tally.forwarded if block["text"] and block["type"] in ("text", "thinking")
    ]
    tally.reply.extend(tally.forwarded)
    if kept and ordered_blocks_out is not None:
        kept[0]["round_start"] = True
        ordered_blocks_out.extend(kept)


def _note_forwarded(tally: _TurnTally, inner) -> None:
    """Record one delta of the open response that went out as a stream frame."""
    kind = {"text_delta": "text", "thinking_delta": "thinking"}.get(getattr(inner, "type", None))
    if kind is None:
        if getattr(inner, "type", None) == "retry" and getattr(inner, "restart", None):
            # The request goes out again; what it streamed is void.
            tally.forwarded.clear()
        elif getattr(inner, "type", None) == "servertool_start":
            # A search the provider runs was shown as a card: the text
            # after it is another passage (``reply_text``).
            tally.forwarded.append({"type": "tool", "index": getattr(inner, "content_index", None), "text": ""})
        return
    delta = getattr(inner, "delta", None)
    if not isinstance(delta, str) or not delta:
        return
    index = getattr(inner, "content_index", None)
    last = tally.forwarded[-1] if tally.forwarded else None
    if last is not None and last["type"] == kind and last["index"] == index:
        last["text"] += delta
    else:
        tally.forwarded.append({"type": kind, "index": index, "text": delta})


def _record_agent_event(ev, tally: _TurnTally, *, req, on_event, ordered_blocks_out,
                        model=None) -> None:
    """Record the side effects of one agent event that the turn's result needs."""
    # Side-effects we care about for the final result.
    # Approval is gated INSIDE the wrapped tool execute (see
    # _wrap_with_approval) — by the time tool_execution_start
    # fires, the user has already approved (or the wrapper
    # short-circuited with a denial result).
    if hasattr(ev, "type"):
        if ev.type == "tool_execution_start":
            _tid = getattr(ev, "tool_call_id", None)
            _args = getattr(ev, "args", None)
            if _tid is not None:
                tally.tool_inputs_by_id[_tid] = {
                    "tool": getattr(ev, "tool_name", None),
                    "input": json.dumps(_args, default=str)
                             if _args is not None else None,
                }
        if ev.type == "tool_execution_end":
            _tid = getattr(ev, "tool_call_id", None)
            _meta = tally.tool_inputs_by_id.get(_tid, {})
            # The card shows the tool's own output (``event_parsing.card_text``).
            from openprogram.runtime.conversation.event_parsing import card_text, stored_tool_result
            _shown, _truncated = card_text(
                stored_tool_result(getattr(ev, "result", "")),
                getattr(getattr(ev, "result", None), "details", None))
            _tc = {
                "id": _tid,
                "tool_call_id": _tid,
                "tool": getattr(ev, "tool_name", None) or _meta.get("tool"),
                "input": _meta.get("input"),
                "result": _shown,
                "is_error": bool(getattr(ev, "is_error", False)),
                "outcome": getattr(ev, "outcome", None),
            }
            if _truncated:
                _tc["full_output"] = _truncated
            tally.tool_calls.append(_tc)
        if ev.type == "message_update":
            # A request sent again after a transport failure is one more
            # provider request of this turn; its reply's turn_end counts one.
            _inner = getattr(ev, "assistant_message_event", None)
            if (getattr(_inner, "type", None) == "retry"
                    and getattr(_inner, "reason", None) in ("transport", "network_wait")):
                tally.usage_total["provider_request_count"] += 1
            # Another of the provider's credentials took over: the line the
            # chat shows over the reply is kept with it
            # (``persistence.persist_assistant`` stores ``credential_notices``).
            _switch = getattr(_inner, "credential", None)
            if (getattr(_inner, "type", None) == "retry" and getattr(_inner, "reason", None) == "credential"
                    and isinstance(_switch, dict) and _switch.get("to")):
                tally.usage_total.setdefault("credential_notices", []).append({
                    "to": str(_switch["to"]), "from": str(_switch.get("from") or ""),
                    **({"cause": str(_switch["cause"])} if _switch.get("cause") else {}),
                })
        if ev.type in ("message_start", "message_update", "message_end"):
            _msg = getattr(ev, "message", None)
            if getattr(_msg, "role", None) == "assistant":
                if ev.type == "message_end":
                    tally.open_response, tally.ended_response = None, _msg
                else:
                    tally.open_response = _msg
                    if ev.type == "message_start":
                        tally.forwarded = []
                    else:
                        _note_forwarded(tally, getattr(ev, "assistant_message_event", None))
        if ev.type == "turn_end":
            tally.ended_response = None
            _record_turn_end(
                ev, tally, req=req, on_event=on_event,
                ordered_blocks_out=ordered_blocks_out, model=model,
            )


async def _drain_agent_events(
    *, req, context, config, continuation, stream_fn, cancel_event,
    turn_over: threading.Event, on_event, ordered_blocks_out, delivered_steers,
) -> tuple[str, dict, list[dict]]:
    """Run the agent loop and forward each AgentEvent to on_event, tallying the result."""
    loop_cancel = asyncio.Event()
    stop_wake = _wake_on_stop(cancel_event, loop_cancel) if cancel_event is not None else None
    try:
        return await _drain_events(
            req=req, context=context, config=config, continuation=continuation,
            stream_fn=stream_fn, cancel_event=cancel_event, loop_cancel=loop_cancel,
            on_event=on_event, ordered_blocks_out=ordered_blocks_out,
            delivered_steers=delivered_steers,
        )
    finally:
        if stop_wake is not None:
            stop_wake()


async def _drain_events(
    *, req, context, config, continuation, stream_fn, cancel_event, loop_cancel,
    on_event, ordered_blocks_out, delivered_steers,
) -> tuple[str, dict, list[dict]]:
    ev_stream = _start_agent_stream(req, context, config, continuation, loop_cancel, stream_fn)

    tally = _TurnTally()
    if continuation is not None:
        _seed_tally_from_continuation(tally, continuation, ordered_blocks_out)

    # aclosing so an early exit (the LLMError raise below) runs the
    # generator's finally/aclose here, instead of leaving it for the
    # GC to schedule on a loop we are about to close.
    async with contextlib.aclosing(
            _aiter_event_stream(ev_stream)) as _events:
        async for ev in _events:
            if getattr(ev, "type", None) == "message_start":
                delivered = delivered_steers.pop(id(getattr(ev, "message", None)), None)
                if delivered is not None:
                    tally.reply.interrupt()
                    if ordered_blocks_out is not None:
                        ordered_blocks_out.append({"type":"steering", "message_id":delivered["msg_id"],
                                                   "text":delivered["content"], "timestamp":delivered["timestamp"]})
                    on_event({"type":"chat_response", "data":delivered})
            envelope = _agent_event_to_envelope(ev, req)
            if envelope is not None:
                on_event(envelope)
            _record_agent_event(
                ev, tally, req=req, on_event=on_event,
                ordered_blocks_out=ordered_blocks_out,
                model=getattr(config, "model", None),
            )

    if cancel_event is not None and cancel_event.is_set():
        _keep_stopped_response(tally, ordered_blocks_out)
    return tally.reply.text.strip(), tally.usage_total, tally.tool_calls


def run_loop_blocking(
    *,
    req: "TurnRequest",
    history: list[dict],
    on_event: "EventCallback",
    cancel_event: Optional[threading.Event],
    stream_fn=None,
    assistant_msg_id: Optional[str] = None,
    agentic_tool_names_out: Optional[set[str]] = None,
    ordered_blocks_out: Optional[list[dict]] = None,
    execution_context: Optional[dict] = None,
    continuation=None,
) -> tuple[str, dict, list[dict]]:
    """Build AgentContext, kick off agent_loop, drain its EventStream.

    Returns (final_text, usage, tool_calls).

    `ordered_blocks_out`, if provided, is mutated in place to hold the
    per-turn ordered block list (``[{"type":"thinking"|"text"|"tool",
    ...}, ...]``) reconstructed from the final AssistantMessage's
    content. Used by the webui to render LLM text / thinking / tool
    cards in the order they appeared, instead of stacking all tools
    at the bottom of the bubble.

    Runs synchronously inside a fresh asyncio loop so callers don't
    need to be async. Cancel via cancel_event flips an asyncio.Event
    inside the loop.

    `stream_fn` is the seam tests use to inject a fake provider —
    see tests/unit/agent/dispatcher/test_dispatcher_integration.py. None means use
    the default (real provider via stream_simple).
    """
    from openprogram.runtime.agent.turn_api import _current_turn_agent
    owner = _current_turn_agent.get()
    if stream_fn is None and owner is not None:
        stream_fn = owner._options.get('stream_fn')
    # Continuation already binds TurnBindings in process_agent_continuation.
    # Nested worktree here only covers resolve_agent_runtime when this
    # function is invoked without those bindings (tests and older callers).
    _worktree_token = None
    if continuation is not None:
        from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
        from openprogram.state.worktree.context import reset_worktree, set_worktree
        _worktree_token = set_worktree(
            runtime_location_for(
                req.session_id, use_context=False,
                branch_id=getattr(req, "branch_id", None),
            )["workdir"],
        )
    try:
        # Resolve agent profile → tools, system_prompt, model.
        agent_profile, tools, recordable_system_prompt, system_prompt, model, runtime_contract = resolve_agent_runtime(
            req, assistant_msg_id=assistant_msg_id, on_event=on_event,
            saved_runtime_contract=continuation.resolved_snapshot if continuation is not None else None,
        )
    finally:
        if _worktree_token is not None:
            reset_worktree(_worktree_token)
    if agentic_tool_names_out is not None:
        agentic_tool_names_out.update(
            tool.name for tool in tools if getattr(tool, "_is_agent_method", False)
        )
    # One assembler (dag/overview.md §7). The tool-runtime block, the Layer 6
    # deferred-tool catalog and the plan-mode reminder are registered
    # components now — the dispatcher no longer hand-appends anything, so the
    # string the engine budgets is the string that ships.
    #
    # We do NOT split ``tools`` for dispatch here — the agent_loop re-splits
    # before every provider call so newly-loaded deferred tools show up with
    # full schema on the next call. The catalog component only lists the
    # *initial* deferred names so the LLM can discover them from turn 1.
    from openprogram.runtime.context import Context
    db = Context.turn_store(req.session_id)
    turn_compaction: dict = {}
    if continuation is not None:
        context = _continuation_agent_context(
            req, continuation, db=db, model=model, tools=tools,
            runtime_contract=runtime_contract,
        )
    else:
        context = _new_turn_agent_context(
            req, history, on_event, db=db, agent_profile=agent_profile,
            model=model, tools=tools, system_prompt=system_prompt,
            recordable_system_prompt=recordable_system_prompt,
            runtime_contract=runtime_contract, assistant_msg_id=assistant_msg_id,
            cancel_event=cancel_event, turn_compaction_out=turn_compaction,
        )

    if continuation is not None:
        _restore_continuation_runtime(continuation, runtime_contract)

    get_steering_messages, delivered_steers = _steering_source(
        req, model=model, db=db, assistant_msg_id=assistant_msg_id,
        execution_context=execution_context,
    )
    config = _agent_loop_config(
        req, agent_profile=agent_profile, model=model,
        transform_context=_permission_context_transform(req, continuation),
        get_steering_messages=get_steering_messages,
        safe_point_hook=_safe_point_hook_for(
            req, tools=tools, assistant_msg_id=assistant_msg_id,
            execution_context=execution_context,
        ),
    )
    if config is not None:
        config.compact_turn = turn_compaction.get("compact_turn")

    # Async drain that forwards each AgentEvent → on_event envelope.
    # ``turn_over`` is set when the drain's loop is done.
    _turn_over = threading.Event()

    # Run the async drain in a fresh loop (we're in a thread).
    loop = asyncio.new_event_loop()
    try:
        from openprogram.host._platform.power import prevent_idle_sleep

        with prevent_idle_sleep():
            return loop.run_until_complete(_drain_agent_events(
                req=req, context=context, config=config, continuation=continuation,
                stream_fn=stream_fn, cancel_event=cancel_event, turn_over=_turn_over,
                on_event=on_event, ordered_blocks_out=ordered_blocks_out,
                delivered_steers=delivered_steers,
            ))
    finally:
        _turn_over.set()
        _close_turn_loop(loop)


#: How long what is left on a turn's loop gets to end before the loop closes.
_LEFT_TASKS_BOUND_S = 1.0
#: How long a task cancelled when that bound is spent gets to take its cancel.
_CUT_TASKS_WAIT_S = 0.2


async def _finish_turn_loop(left_by_turn: "set[asyncio.Task] | frozenset" = frozenset()) -> None:
    """End every task left on the turn's loop, then its async generators.

    The order is ``asyncio.run``'s, which a loop driven by hand has to keep
    itself: async generators are closed only once no task is running one.
    Closed under a task still unwinding (a stopped turn's model stream), a
    generator refuses ("aclose(): asynchronous generator is already running")
    and the task is destroyed pending with the loop.

    A stop does not wait for the provider's close, so the reads it left
    unwinding are finished first, on their own while they can; then the
    provider clients go and the tasks the turn left (``left_by_turn``) are
    cancelled.

    Ending those starts more: an abandoned generator is closed by a task the
    loop's finalizer hook schedules, one level of a wrapped stream at a time,
    and a cancelled task may leave the close it was waiting on running. So
    the loop is not finished in one pass. Whatever task appears is let run to
    its end, and the generators are shut down only when none is left, until
    two rounds in a row find nothing. All of it shares one bound; what is
    still running when it is spent is cancelled and named in a warning.
    """
    from openprogram.models.providers.utils.streams.event_stream import settle_unwinding
    from openprogram.models.providers.utils.http_transport.http_client import aclose_current_loop_clients

    loop = asyncio.get_running_loop()
    for step in (settle_unwinding, aclose_current_loop_clients):
        try:
            await step()
        except Exception:
            _log.debug("turn loop teardown step %s failed", step.__name__, exc_info=True)
    me = asyncio.current_task()
    told = {task for task in left_by_turn if not task.done()}
    for task in told:
        task.cancel()
    deadline = time.monotonic() + _LEFT_TASKS_BOUND_S
    quiet = 0
    while quiet < 2:
        # A close the finalizer hook scheduled becomes a task on this pass.
        await asyncio.sleep(0)
        left = {task for task in asyncio.all_tasks(loop) if task is not me}
        if not left:
            await loop.shutdown_asyncgens()
            quiet += 1
            continue
        quiet = 0
        now = time.monotonic()
        if now >= deadline + _CUT_TASKS_WAIT_S:
            _log.warning(
                "turn loop closed with %d task(s) still running: %s",
                len(left), ", ".join(sorted(repr(task) for task in left))[:2000],
            )
            return
        if now >= deadline:
            for task in left - told:
                task.cancel()
            told |= left
        done, _ = await asyncio.wait(
            left, timeout=(deadline if now < deadline else deadline + _CUT_TASKS_WAIT_S) - now,
        )
        for task in done:
            if not task.cancelled():
                task.exception()  # retrieved: the turn already has its outcome


def _close_turn_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Finish what a turn left on its loop, then close the loop, off the turn's path.

    The provider clients are keyed by the loop: they are closed on it, or
    their cache entries outlive it and count against the shared cache's limit
    for the rest of the process. Closing a Codex WebSocket is a round trip to
    the backend, for a kept socket and for the one a stopped turn was reading,
    and nothing of the turn depends on it, so the turn's reply and its final
    state do not wait for it: the loop, idle now, is finished and closed on a
    thread of its own (``_finish_turn_loop``). The thread is not a daemon, so
    a process that ends right after its last turn still closes the sockets first.
    """
    def _close() -> None:
        try:
            # Read before the loop runs again: only the turn's own tasks exist.
            loop.run_until_complete(_finish_turn_loop(asyncio.all_tasks(loop)))
        except Exception:
            _log.debug("turn loop teardown failed", exc_info=True)
        loop.close()

    threading.Thread(target=_close, name="openprogram-turn-loop-close").start()
