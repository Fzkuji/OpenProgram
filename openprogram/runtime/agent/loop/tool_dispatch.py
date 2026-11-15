"""Tool dispatch: resolve, gate, execute and record the tool calls of one assistant message."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from openprogram.models.providers.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.models.providers.utils.validation import validate_tool_arguments

from openprogram.runtime.events import emit_safe, get_event_bus, make_event
from openprogram.runtime.events import decide_tool_gate
from openprogram.runtime.permissions import refusal
from openprogram.runtime.agent.loop.types import (
    AgentEvent,
    AgentEventMessageEnd,
    AgentEventMessageStart,
    AgentEventToolEnd,
    AgentEventToolStart,
    AgentEventToolUpdate,
    AgentMessage,
)
from openprogram.models.providers.types import AgentTool, AgentToolResult

from openprogram.runtime.agent.loop.guards import _SafePointStop
from openprogram.runtime.agent.loop.message_context import _durable_message
from openprogram.runtime.agent.loop.lifecycle import (
    _job_operation_timeout,
    _record_job_activity,
)


# In-flight tool executions, keyed by tool_call_id. Read by the worker's
# GET /api/running so the Running panel can show what the agent is
# executing right now (bash commands, code runs, sub-agents, …).
import threading as _threading
import logging

_log = logging.getLogger(__name__)

RUNNING_TOOL_CALLS: dict[str, dict] = {}
RUNNING_TOOL_CALLS_LOCK = _threading.Lock()


def _attach_nested_agents_md(tool_call: Any, result: AgentToolResult,
                             seen: set[str]) -> AgentToolResult:
    """Record the AGENTS.md files a file tool just reached (below the
    working directory or in another project) as the attachment sent after
    its result, unless ``seen`` (the paths the request's history already
    attaches, and those claimed earlier in this group of calls) holds them."""
    if result.is_error:
        return result
    try:
        from openprogram.runtime.context import agents_md
        paths, text = agents_md.nested_for(tool_call.name, tool_call.arguments, seen)
    except Exception:
        _log.debug("AGENTS.md lookup failed for %s", tool_call.name, exc_info=True)
        return result
    if not text:
        return result
    seen.update(paths)
    from openprogram.models.providers.shared.tool_attachments import with_attachment
    return result.model_copy(update={
        "details": with_attachment(result.details, text, paths=paths)})


def _rewrite_tool_result(tool_call: Any, result: AgentToolResult,
                         seen: set[str] | None = None) -> AgentToolResult:
    """The result the model reads, as ``tool.result`` rewriters (plugins)
    change it, in the form history stores (``_as_stored``), with the guide
    and the AGENTS.md files the call is due recorded as the attachment sent
    after it (``shared.tool_attachments``)."""
    from openprogram.programs._authoring import guides
    result = guides.attach(tool_call.name, tool_call.arguments, result)
    result = _attach_nested_agents_md(tool_call, result, set() if seen is None else seen)
    return _as_stored(tool_call, _transform_tool_result(tool_call, result))


#: Characters of a bounded result's original text the live tool card shows
#: (``event_parsing``): the card shows the tool's own output, not the note
#: written for the model.
PREVIEW_CHARS = 4_000


def _no_output(tool_call: Any, result: AgentToolResult) -> str:
    """What a result with neither text nor media says, as Claude Code
    writes it: an empty tool result is rejected by some providers and read
    as a call that never returned by others."""
    verb = "failed" if result.is_error else "completed"
    return f"({tool_call.name} {verb} with no output)"


def _bound_attachment(tool_call: Any, result: AgentToolResult) -> AgentToolResult:
    """``result`` with its attachment bounded to ``spill.result_cap()``."""
    from openprogram.models.providers.shared.tool_attachments import ATTACHMENT, record
    entry = record(result.details)
    if entry is None:
        return result
    from openprogram.programs._host.runtime import _persist_full_result
    from openprogram.state.store.session.spill import bound_text
    text = bound_text(entry["text"], lambda whole: _persist_full_result(str(tool_call.id), whole))
    if text == entry["text"]:
        return result
    return result.model_copy(update={"details": {**result.details, ATTACHMENT: {**entry, "text": text}}})


def _as_stored(tool_call: Any, result: AgentToolResult) -> AgentToolResult:
    """The result in the form its turn sends and history stores.

    History keeps a result as one text (``event_parsing.stored_tool_result``)
    and every later request renders it as one text block, so the turn sends
    that same block: its non-empty text blocks joined with ``\n``, as the
    store joins them, in the place of the first, with images and audio
    where they were. Each
    provider then sends the same bytes in the turn and after it, whether it
    sends blocks one by one (Anthropic) or joins them (``\n`` for
    Responses, a space for chat completions).

    This is the one place a result is cut: an over-cap text is bounded
    here, once, to ``spill.result_cap`` (or the smaller cap the tool
    declared, ``details["cap"]``), with the whole text saved to one file,
    so the turn and history read the same head, tail and path. The details
    then say where the whole text is (``output``) and keep the head of the
    original for the live card (``preview``). Every tool is bounded,
    ``tool_search`` too: the tools it loaded are read from its
    ``details["loads"]`` record, which bounding leaves alone, not from its
    text. A result with neither text nor media gets a sentence saying so.
    """
    result = _bound_attachment(tool_call, result)
    content = list(result.content or [])
    texts = [i for i, c in enumerate(content) if isinstance(c, TextContent)]
    text = "\n".join(content[i].text for i in texts if content[i].text)
    if not text:
        media = [c for c in content if not isinstance(c, TextContent)]
        if media:
            # Media alone: no empty text block, as history renders it.
            return result if len(media) == len(content) else result.model_copy(update={"content": media})
        return result.model_copy(update={"content": [TextContent(text=_no_output(tool_call, result))]})
    from openprogram.programs._host.execution import CAP_DETAIL
    from openprogram.programs._host.runtime import _persist_full_result
    from openprogram.state.store.session.spill import bound_text, result_cap
    details = dict(result.details) if isinstance(result.details, dict) else {}
    cap = result_cap(details.get(CAP_DETAIL))
    saved: list[Any] = []

    def _save(whole: str) -> Any:
        path = _persist_full_result(str(tool_call.id), whole)
        saved.append(path)
        return path

    bounded = bound_text(text, _save, cap)
    update: dict[str, Any] = {}
    if bounded != text:
        details["output"] = {
            "full_path": str(saved[0]) if saved else None,
            "total_lines": text.count("\n") + 1,
            "total_chars": len(text),
            "cap": cap,
            "unit": "weight",
        }
        details["preview"] = text[:PREVIEW_CHARS]
        update["details"] = details
    if len(texts) == 1 and content[texts[0]].text == bounded and not update:
        return result
    joined = [c for i, c in enumerate(content) if i not in texts[1:]]
    joined[texts[0]] = TextContent(text=bounded)
    update["content"] = joined
    return result.model_copy(update=update)


def _transform_tool_result(tool_call: Any, result: AgentToolResult) -> AgentToolResult:
    """Let ``tool.result`` rewriters (plugins) change the text. Without a
    rewriter the result passes through untouched; a rewritten text
    replaces the text blocks and keeps images and audio."""
    bus = get_event_bus()
    if not bus.has_transforms("tool.result"):
        return result
    text = "".join(
        c.text for c in (result.content or [])
        if hasattr(c, "text") and isinstance(c.text, str)
    )
    event = make_event("tool.result", "tool", {
        "tool": tool_call.name, "tool_call_id": tool_call.id,
        "args": tool_call.arguments, "is_error": result.is_error,
    })
    rewritten = bus.emit_transform(event, text)
    if not isinstance(rewritten, str) or rewritten == text:
        return result
    others = [c for c in (result.content or []) if not isinstance(c, TextContent)]
    return result.model_copy(update={"content": [TextContent(text=rewritten), *others]})


def _tool_call_label(name: str, args: Any) -> str:
    """Human-readable one-liner for a tool call (UI display only)."""
    if isinstance(args, dict):
        for key in ("description", "command", "prompt", "query", "path"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:200]
    return name


class _SkipExecute(Exception):
    """Internal: repeat-fail trip already built a tool result."""


def _tool_repeat_key(name: str, args: Any) -> str:
    from openprogram.runtime.agent.loop.failure_policy import repeat_key
    return repeat_key(name, args)


_TOOL_NAME_ALIASES = {"Bash": "bash", "Read": "read", "ToolSearch": "tool_search"}


def _resolve_called_tool(tools: list[AgentTool] | None, call: ToolCall) -> tuple[AgentTool | None, dict]:
    """Resolve observed Claude spellings only within the turn's allowed tools."""
    available = {tool.name: tool for tool in (tools or [])}
    tool = available.get(call.name)
    if tool is None:
        tool = available.get(_TOOL_NAME_ALIASES.get(call.name, ""))
    args = dict(call.arguments)
    if tool is not None and tool.name == "tool_search":
        if call.name == "ToolSearch" and "query" in args and "select" not in args:
            args["select"] = args.pop("query")
        if args.get("max_results", object()) is None:
            args.pop("max_results")
    return tool, args


def _with_schema_hint(error: Exception, tool: Any, loaded: Any) -> Exception:
    """A failed argument check of a deferred tool the request had not loaded
    says the schema was not sent and how to load it (Claude Code's
    ``buildSchemaNotSentHint``); any other failure is left as it is. The
    call itself is never held back: valid arguments run (Codex)."""
    from openprogram.programs._host.runtime import (
        Tier, _request_loaded, schema_not_sent_hint, tool_tier,
    )

    names = set(loaded) if loaded is not None else set(_request_loaded.get())
    if tool is None or tool_tier(tool) is not Tier.DEFERRED or tool.name in names:
        return error
    return ValueError(f"{error}\n\n{schema_not_sent_hint(tool.name)}")


async def _execute_tool_calls(*args: Any, context_window: int | None = None,
                              tool_output_weight: int | None = None,
                              history: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Execute tool calls from an assistant message (``_dispatch_tool_calls``).

    ``context_window`` and ``tool_output_weight`` describe the model the
    results go to: each stored result is bounded to its
    ``tool_output_weight`` and at most a share of its window
    (``spill.result_cap``), and a paging tool's page shrinks with it.
    ``history`` is the request's messages: the AGENTS.md files they already
    attach are not attached again (``shared.tool_attachments``).
    """
    from openprogram.models.providers.shared.tool_attachments import attached_paths
    from openprogram.state.store.session import spill as _spill

    loaded = None
    if history is not None:
        from openprogram.models.providers.shared.tool_loads import loaded_records

        loaded = loaded_records(history)
    window_token = _spill.set_window(context_window, tool_output_weight)
    try:
        return await _dispatch_tool_calls(*args, attached=attached_paths(history), loaded=loaded, **kwargs)
    finally:
        _spill.reset_window(window_token)


async def _dispatch_tool_calls(
    tools: list[AgentTool] | None,
    assistant_message: AssistantMessage,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    get_steering_messages: Any | None = None,
    repeat_failures: dict[str, int] | None = None,
    safe_point_hook: Any | None = None,
    *,
    start_index: int = 0,
    round_id: str | None = None,
    attached: set[str] | None = None,
    loaded: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Execute tool calls from an assistant message.
    Mirrors executeToolCalls() in TypeScript.

    ``attached`` is the AGENTS.md paths the request already attaches; the
    group's calls claim the ones they attach in it, so two calls reaching
    the same directory attach its file once.

    ``loaded`` is what the request's messages loaded
    (``tool_loads.loaded_records``); without it, the last request's
    loaded names (``runtime._request_loaded``).
    """
    if attached is None:
        attached = set()
    from openprogram.runtime.agent.durable_steps import FunctionSuspended

    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCall)]
    results: list[ToolResultMessage] = []
    steering_messages: list[AgentMessage] | None = None
    stop_at_safe_point = False
    if repeat_failures is None:
        repeat_failures = {}

    # This dispatcher executes the list serially. It deliberately emits no
    # parallel group; a future concurrent dispatcher must provide an explicit
    # group id instead of making the UI infer concurrency from adjacency.
    import hashlib
    if round_id is None:
        round_id = "msg_" + hashlib.sha256(
            json.dumps(
                _durable_message(assistant_message),
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]

    def _occurrence_id(index: int, call: ToolCall) -> str:
        payload = {
            "round": round_id,
            "index": index,
            "tool_call_id": str(call.id),
            "tool_name": call.name,
            "arguments": call.arguments,
        }
        return "occ_" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:24]

    for index, tool_call in enumerate(tool_calls[start_index:], start=start_index):
        tool, call_args = _resolve_called_tool(tools, tool_call)
        if tool is None and (loaded or {}).get(tool_call.name):
            # Loaded earlier, gone now (its server disconnected): the call
            # says so instead of "not found".
            from openprogram.programs._host.runtime import unavailable_tool
            tool = unavailable_tool(tool_call.name, loaded[tool_call.name])
        occurrence_id = _occurrence_id(index, tool_call)
        dag_expose = getattr(tool, "_dag_expose", None) or "full"
        from openprogram.models.providers.types import Tool as AiTool
        validated_args = call_args
        validation_error = None
        if tool is not None:
            try:
                validated_args = validate_tool_arguments(AiTool(
                    name=tool.name, description=tool.description, parameters=tool.parameters,
                ), tool_call.model_copy(update={"arguments": call_args}))
            except Exception as exc:
                validation_error = exc
        fail_key = _tool_repeat_key(tool_call.name, validated_args)
        streak = repeat_failures.get(fail_key, 0)

        ev_stream.push(AgentEventToolStart(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
            occurrence_id=occurrence_id,
            expose=dag_expose,
        ))
        preflight = getattr(tool, "_permission_preflight", None)
        if callable(preflight) and streak < 2 and validation_error is None:
            import hashlib
            operation_id = hashlib.sha256(json.dumps(
                _durable_message(assistant_message), sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            await preflight(tool_call.id, validated_args, operation_id=operation_id)
        if safe_point_hook is not None:
            interaction_manifest = getattr(tool, "_interaction_manifest", None)
            pre_wait = (
                interaction_manifest(tool_call.id, validated_args)
                if callable(interaction_manifest) and validation_error is None else None
            )
            safe_payload = {
                "tool_call_id": str(tool_call.id), "tool_name": tool_call.name,
                "arguments": validated_args, "next_tool_index": index,
                "pre_wait": pre_wait,
            }
            stop_at_safe_point = bool(await safe_point_hook("tool.before", safe_payload))
            if stop_at_safe_point:
                break
            if tool is not None:
                object.__setattr__(
                    tool, "_preapproved_wait_id",
                    safe_payload.get("preapproved_wait_id"),
                )

        # 事件层：tool.before 一份事件，观察（异步总线）+ 问询（同步 gate）共用。
        # plugin 的 tool.before handler 就是 gate 订阅者（plugins/hooks.py）。
        before_ev = make_event("tool.before", "agent",
                               {"tool": tool_call.name,
                                "tool_call_id": tool_call.id,
                                "args": tool_call.arguments})
        get_event_bus().emit(before_ev)  # never raises: handlers are isolated
        gate_denial = decide_tool_gate(before_ev)

        # session/execution 由执行控制面的 contextvar 提供：session_id 让
        # /api/running 能按会话分组和跳转，execution_id 区分前台轮次和后台分支。
        from openprogram.runtime.execution.run_control import (
            get_current_execution_id, get_current_session_id,
        )
        call_started = time.monotonic()
        partial_output: list[str] = [""]
        with RUNNING_TOOL_CALLS_LOCK:
            RUNNING_TOOL_CALLS[tool_call.id] = {
                "tool_name": tool_call.name,
                "label": _tool_call_label(tool_call.name, tool_call.arguments),
                "started_at": time.time(),
                "session_id": get_current_session_id(),
                "execution_id": get_current_execution_id(),
            }

        result: AgentToolResult
        skipped_repeat = streak >= 2
        from openprogram.runtime.permissions.execution import ToolExecution, current_execution, mark_started

        async def notify_started():
            if safe_point_hook is not None:
                if await safe_point_hook("tool.started", {"tool_call_id": str(tool_call.id)}):
                    raise _SafePointStop()

        execution = ToolExecution(notify_started)
        if skipped_repeat:
            n = streak + 1
            from openprogram.runtime.agent.loop.failure_policy import record_failure
            record_failure(repeat_failures, fail_key, n)
            result = AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=(
                        f"你已连续 {n} 次重复同一失败调用，"
                        "请改变方法或向用户说明阻碍"
                    ),
                )],
                details={},
                is_error=True,
            )
        execution_token = current_execution.set(execution)
        occurrence_token = None
        identity_consumed_token = None
        try:
            if skipped_repeat:
                raise _SkipExecute()
            if gate_denial is not None:
                raise refusal.hook(tool_call.name, tool_call.arguments, gate_denial)
            if not tool:
                raise ValueError(f"Tool {tool_call.name} not found")

            if validation_error is not None:
                raise _with_schema_hint(validation_error, tool, loaded)

            def on_update(partial_result: AgentToolResult) -> None:
                _record_job_activity("tool_progress")
                # What the call had produced so far stays in its result if
                # the turn is stopped (``stopped_tool_text``).
                partial_output[0] = "".join(
                    c.text for c in (getattr(partial_result, "content", None) or [])
                    if isinstance(getattr(c, "text", None), str)
                )
                ev_stream.push(AgentEventToolUpdate(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                    partial_result=partial_result,
                ))

            if not getattr(tool, "_permission_managed", False):
                await mark_started()
            _record_job_activity("operation_start")
            timeout = _job_operation_timeout(None)
            preapproved_wait_id = getattr(tool, "_preapproved_wait_id", None)
            try:
                from openprogram.programs._host.runtime import (
                    _current_tool_call_occurrence_id,
                    _tool_call_identity_consumed,
                )

                occurrence_token = _current_tool_call_occurrence_id.set(occurrence_id)
                identity_consumed_token = _tool_call_identity_consumed.set(False)
            except Exception:
                occurrence_token = None
            if preapproved_wait_id:
                from openprogram.runtime.agent.engine_link import preapproved_wait
                with preapproved_wait(preapproved_wait_id):
                    operation = tool.execute(
                        tool_call.id, validated_args, cancel_event, on_update,
                    )
                    result = (
                        await operation
                        if timeout is None
                        else await asyncio.wait_for(operation, timeout=timeout)
                    )
            else:
                operation = tool.execute(
                    tool_call.id, validated_args, cancel_event, on_update,
                )
                result = (
                    await operation
                    if timeout is None
                    else await asyncio.wait_for(operation, timeout=timeout)
                )
        except _SafePointStop:
            stop_at_safe_point = True
            break
        except FunctionSuspended:
            if safe_point_hook is None:
                raise
            stop_at_safe_point = bool(await safe_point_hook("tool.suspended", {
                "tool_call_id": str(tool_call.id), "tool_name": tool_call.name,
                "next_tool_index": index, "repeat_failures": dict(repeat_failures),
                "tool_call_ids": [str(call.id) for call in tool_calls],
            }))
            if not stop_at_safe_point:
                raise
            break
        except _SkipExecute:
            pass
        except refusal.Refused as refused:
            from openprogram.runtime.agent.turn_request import get_turn_request
            result = refused.result(mode=getattr(get_turn_request(), "permission_mode", None))
        except Exception as e:
            result = AgentToolResult(
                content=[TextContent(type="text", text=str(e))],
                details={},
                is_error=True,
            )
        except BaseException as e:
            # User-triggered cancel (openprogram.runtime.agent.call_state.CancelledError
            # is a BaseException so user-written `except Exception` inside tool bodies
            # cannot swallow it). Push a tool_end event so the UI sees the call
            # closed, then re-raise to abort the agent loop. The outer _run handler
            # ends the event stream gracefully so the chat dispatcher unblocks and
            # `running_task` is cleared — without this the stop button keeps showing
            # because the asyncio Task is killed by an "unretrieved" BaseException
            # and the stream never terminates.
            # The model reads what stopped the call and how long it ran
            # (Codex ``aborted by user after {secs}s``), after any output
            # it had already produced (OpenCode).
            from openprogram.runtime.execution.run_control.stop_reasons import (
                WORKER, current_stop_reason, stopped_tool_text,
            )
            reason = WORKER if str(e) == "worker_stopping" else current_stop_reason()
            result = AgentToolResult(
                content=[TextContent(type="text", text=stopped_tool_text(
                    reason, time.monotonic() - call_started, partial_output[0],
                ))],
                details={"stopped": {"reason_code": reason}},
                is_error=True,
            )
            ev_stream.push(AgentEventToolEnd(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
                is_error=True,
                outcome="cancelled",
                occurrence_id=occurrence_id,
                expose=dag_expose,
            ))
            raise
        finally:
            if occurrence_token is not None:
                try:
                    from openprogram.programs._host.runtime import _current_tool_call_occurrence_id

                    _current_tool_call_occurrence_id.reset(occurrence_token)
                except (ValueError, RuntimeError):
                    # ContextVar.reset raises ValueError for a token from another
                    # context and RuntimeError for one already spent.
                    _log.debug("occurrence id token already spent or foreign", exc_info=True)
            if identity_consumed_token is not None:
                try:
                    from openprogram.programs._host.runtime import _tool_call_identity_consumed

                    _tool_call_identity_consumed.reset(identity_consumed_token)
                except (ValueError, RuntimeError):
                    # ContextVar.reset raises ValueError for a token from another
                    # context and RuntimeError for one already spent.
                    _log.debug("identity-consumed token already spent or foreign", exc_info=True)
            current_execution.reset(execution_token)
            with RUNNING_TOOL_CALLS_LOCK:
                RUNNING_TOOL_CALLS.pop(tool_call.id, None)

        outcome = "not_started" if not execution.started else ("failed" if result.is_error else "completed")
        result = _rewrite_tool_result(tool_call, result, attached)
        if not skipped_repeat:
            if result.is_error and not execution.automatic_refusal:
                from openprogram.runtime.agent.loop.failure_policy import record_failure
                record_failure(repeat_failures, fail_key, streak + 1)
            else:
                repeat_failures.pop(fail_key, None)

        ev_stream.push(AgentEventToolEnd(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            is_error=result.is_error,
            outcome=outcome,
            occurrence_id=occurrence_id,
            expose=dag_expose,
        ))
        emit_safe("tool.after", "tool", {
            "tool": tool_call.name,
            "tool_call_id": tool_call.id,
            "is_error": result.is_error,
            # Only the text channel of the result — binary attachments
            # can be huge and rarely useful for subscribers.
            "result_text": "".join(
                c.text for c in (result.content or [])
                if hasattr(c, "text") and isinstance(c.text, str)
            ),
        })

        tool_result_msg = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=result.content,
            details=result.details,
            is_error=result.is_error,
            timestamp=int(time.time() * 1000),
        )
        if safe_point_hook is not None:
            stop_at_safe_point = bool(await safe_point_hook("tool.after", {
                "tool_call_id": str(tool_call.id), "tool_name": tool_call.name,
                "result": _durable_message(tool_result_msg),
                "is_error": bool(result.is_error),
                "outcome": outcome, "execution_started": execution.started,
                "next_tool_index": index + 1,
                "repeat_failures": dict(repeat_failures),
                "tool_call_ids": [str(call.id) for call in tool_calls],
            }))
        results.append(tool_result_msg)
        ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
        ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

        if stop_at_safe_point:
            break

    # Finish the already-issued tool batch before adding input to the next
    # model decision. Steering does not cancel or fabricate skipped tool results.
    # A pause owns its checkpoint; leave its pending input for continuation.
    if not stop_at_safe_point and get_steering_messages:
        steering_messages = await get_steering_messages() or None

    return {
        "tool_results": results, "steering_messages": steering_messages,
        "stop_at_safe_point": stop_at_safe_point,
    }
