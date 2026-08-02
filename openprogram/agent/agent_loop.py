"""
Agent loop — mirrors packages/agent/src/agent-loop.ts

Core loop logic: agentLoop(), agentLoopContinue(), runLoop().
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, AsyncGenerator

from openprogram.providers import stream_simple as _default_stream_simple
from openprogram.providers.types import (
    AssistantMessage,
    Context,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.validation import validate_tool_arguments

from .event_bus import emit_safe, get_event_bus, make_event
from .tool_gate import ToolGateDenied, decide_tool_gate
from .types import (
    AgentContext,
    AgentEvent,
    AgentEventAgentEnd,
    AgentEventAgentStart,
    AgentEventMessageEnd,
    AgentEventMessageStart,
    AgentEventMessageUpdate,
    AgentEventToolEnd,
    AgentEventToolStart,
    AgentEventToolUpdate,
    AgentEventTurnEnd,
    AgentEventTurnStart,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    StreamFn,
)


def _latest_user_text(messages: list) -> str:
    """Walk back from the end and return the last user-role text.

    Memory prefetch uses this as the recall query for the upcoming
    turn. Empty string if no user message is present (e.g. on the
    first model warmup call).
    """
    for msg in reversed(messages):
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict):
                    if c.get("type") == "text" or "text" in c:
                        parts.append(str(c.get("text", "")))
                else:
                    text = getattr(c, "text", None)
                    if text:
                        parts.append(str(text))
            joined = " ".join(p for p in parts if p)
            if joined.strip():
                return joined.strip()
        return ""
    return ""


def _inject_memory_prefetch(llm_messages: list, block: str) -> bool:
    """Prepend ``block`` to the last user message's text, in place.

    dag/overview.md §7 — prefetched memory belongs to the turn that recalled
    it, not to the session-constant system prompt. Tool results carry role
    ``toolResult``, so the last ``role == "user"`` message is always the
    conversational turn. Returns True when a message was modified.
    """
    prefix = block.rstrip() + "\n\n"
    for msg in reversed(llm_messages or []):
        role = getattr(msg, "role", None) or (
            msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            _set_content(msg, prefix + content)
            return True
        if isinstance(content, list):
            for part in content:
                ptype = getattr(part, "type", None) or (
                    part.get("type") if isinstance(part, dict) else None)
                if ptype != "text":
                    continue
                if isinstance(part, dict):
                    part["text"] = prefix + str(part.get("text") or "")
                else:
                    part.text = prefix + (getattr(part, "text", "") or "")
                return True
            # Image/file-only turn: no text part to prefix — leave it alone
            # rather than inventing a block ordering the provider may reject.
        return False
    return False


def _set_content(msg, value) -> None:
    if isinstance(msg, dict):
        msg["content"] = value
    else:
        msg.content = value


def _memory_sync_turn(messages: list, final_message) -> None:
    """Best-effort post-turn write to journal memory.

    Cheap pattern matching only — heavier extraction lives in the
    session-end watcher.
    """
    try:
        from openprogram.memory.builtin import BuiltinMemoryProvider
    except Exception:
        return
    user_text = _latest_user_text(messages)
    if not user_text:
        return
    asst_text = ""
    content = getattr(final_message, "content", None) or []
    for c in content:
        if hasattr(c, "type") and c.type == "text":
            asst_text += getattr(c, "text", "") or ""
    try:
        BuiltinMemoryProvider().sync_turn(user_text, asst_text)
    except Exception:
        pass


def _create_agent_stream() -> EventStream[AgentEvent, list[AgentMessage]]:
    return EventStream(
        is_done=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages if e.type == "agent_end" else [],
    )


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent, list[AgentMessage]]:
    """
    Start an agent loop with new prompt messages.
    Mirrors agentLoop() in TypeScript.
    """
    ev_stream = _create_agent_stream()

    async def _run():
        try:
            new_messages: list[AgentMessage] = list(prompts)
            current_context = AgentContext(
                system_prompt=context.system_prompt,
                messages=list(context.messages) + list(prompts),
                tools=context.tools,
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())
            for prompt in prompts:
                ev_stream.push(AgentEventMessageStart(message=prompt))
                ev_stream.push(AgentEventMessageEnd(message=prompt))

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            # Ensure the stream is always terminated even if the loop crashes
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)
        except BaseException as e:
            # User-triggered CancelledError (BaseException subclass) — end the
            # stream cleanly so the chat dispatcher unblocks and the running_task
            # gets cleared. Without this branch the Task dies with an unretrieved
            # exception and the UI is stuck on the stop button.
            from openprogram.agentic_programming.function import (
                CancelledError as _AgenticCancelled,
            )
            if isinstance(e, _AgenticCancelled):
                if not ev_stream._result_event.is_set():
                    ev_stream.end(new_messages)
            else:
                raise

    asyncio.ensure_future(_run())
    return ev_stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent, list[AgentMessage]]:
    """
    Continue from the current context without adding a new message.
    Mirrors agentLoopContinue() in TypeScript.
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    last = context.messages[-1]
    if hasattr(last, "role") and last.role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    ev_stream = _create_agent_stream()

    async def _run():
        try:
            new_messages: list[AgentMessage] = []
            current_context = AgentContext(
                system_prompt=context.system_prompt,
                messages=list(context.messages),
                tools=context.tools,
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)

    asyncio.ensure_future(_run())
    return ev_stream


async def _run_loop(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    stream_fn: StreamFn | None,
) -> None:
    """
    Main loop logic — mirrors runLoop() in TypeScript.
    """
    first_turn = True
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages()

    # Hard cap on the inner tool-call loop so a model that keeps asking
    # for "one more tool call" can't churn the runtime forever. 50 is
    # plenty for a real task; anything beyond that is the model spinning.
    # A caller-set ``config.max_iterations`` (exec's ``max_iterations=``)
    # tightens the cap — it can never raise it past the hard limit.
    MAX_INNER_ITERATIONS = 50
    iteration_cap = MAX_INNER_ITERATIONS
    if config.max_iterations is not None:
        iteration_cap = max(1, min(MAX_INNER_ITERATIONS, config.max_iterations))
    inner_iterations = 0

    while True:
        # Turn boundary — pin the provider tools array for every call made
        # below. Tools that ``tool_search`` loads mid-turn stay out of the
        # array until the next boundary so the cached prefix (rooted on the
        # tools array) survives the turn; they are callable immediately via
        # the schema tool_search returns. See tool-toggle-management.md §6.
        from openprogram.functions import freeze_turn_tools
        freeze_turn_tools(list(current_context.tools or []))

        has_more_tool_calls = True
        steering_after_tools: list[AgentMessage] | None = None

        while has_more_tool_calls or len(pending_messages) > 0:
            inner_iterations += 1
            if inner_iterations > iteration_cap:
                # End the stream cleanly with whatever we've got. The
                # consumer (dispatcher / cli_chat) treats a normal
                # stream end as a successful turn — no more, no less.
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return
            if not first_turn:
                ev_stream.push(AgentEventTurnStart())
            else:
                first_turn = False

            # Inject pending messages
            if pending_messages:
                for msg in pending_messages:
                    ev_stream.push(AgentEventMessageStart(message=msg))
                    ev_stream.push(AgentEventMessageEnd(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # Stream assistant response
            message = await _stream_assistant_response(
                current_context, config, cancel_event, ev_stream, stream_fn
            )
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                emit_safe("turn.ended", "agent", {"reason": message.stop_reason})
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            # Check for tool calls
            tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
            has_more_tool_calls = len(tool_calls) > 0

            tool_results: list[ToolResultMessage] = []
            if has_more_tool_calls:
                execution = await _execute_tool_calls(
                    current_context.tools,
                    message,
                    cancel_event,
                    ev_stream,
                    config.get_steering_messages,
                )
                tool_results.extend(execution["tool_results"])
                steering_after_tools = execution.get("steering_messages")

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            ev_stream.push(AgentEventTurnEnd(message=message, tool_results=tool_results))
            emit_safe("turn.ended", "agent", {"tool_results": len(tool_results)})

            if steering_after_tools:
                pending_messages = steering_after_tools
                steering_after_tools = None
            else:
                pending_messages = []
                if config.get_steering_messages:
                    pending_messages = await config.get_steering_messages()

        # Check for follow-up messages
        follow_up_messages: list[AgentMessage] = []
        if config.get_follow_up_messages:
            follow_up_messages = await config.get_follow_up_messages()

        if follow_up_messages:
            pending_messages = follow_up_messages
            continue

        break

    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
    ev_stream.end(new_messages)


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    stream_fn: StreamFn | None,
) -> AssistantMessage:
    """
    Stream an assistant response from the LLM.
    Mirrors streamAssistantResponse() in TypeScript.
    """
    messages = context.messages

    # Apply context transform if configured
    if config.transform_context:
        messages = await config.transform_context(messages, cancel_event)

    # Convert to LLM-compatible messages
    convert = config.convert_to_llm
    if inspect.iscoroutinefunction(convert):
        llm_messages = await convert(messages)
    else:
        result = convert(messages)
        if inspect.isawaitable(result):
            llm_messages = await result
        else:
            llm_messages = result

    # Per-turn memory prefetch — extract the latest user message and ask the
    # memory subsystem for relevant snippets. The result is already fenced as
    # <memory-context>; it renders as a PREFIX BLOCK INSIDE the current user
    # message (dag/overview.md §7), never on the system prompt. Prefetch
    # changes with every new user input, so appending it to the system prompt
    # invalidated the provider's cached prefix for the ENTIRE history on every
    # turn — the single largest source of avoidable input cost. In the user
    # turn it only ever invalidates the tail it sits in.
    prefetch_block = context.memory_prefetch
    if prefetch_block is None:
        prefetch_block = ""
        latest_user_text = _latest_user_text(messages)
        if latest_user_text:
            try:
                from openprogram.memory.builtin import BuiltinMemoryProvider
                prefetch_block = BuiltinMemoryProvider().prefetch(latest_user_text)
            except Exception:
                prefetch_block = ""

    sys_prompt = context.system_prompt or None
    if prefetch_block:
        _inject_memory_prefetch(llm_messages, prefetch_block)

    # Build LLM context
    # Layer 6 (Claude Code shouldDefer): split the tools list into the
    # provider array. The split reads the turn-frozen set installed by
    # ``freeze_turn_tools`` at the turn boundary, so this returns the
    # SAME array on every call within a turn — the cached prefix rooted
    # on the tools array survives a mid-turn ``tool_search``.
    from openprogram.functions import split_tools_for_dispatch
    _provider_tools, _ = split_tools_for_dispatch(
        list(context.tools or [])
    )
    llm_context = Context(
        system_prompt=sys_prompt,
        messages=llm_messages,
        tools=_provider_tools,
    )

    fn = stream_fn or _default_stream_simple

    # Provider/model failover — DEFAULT OFF. resolve_fallback_models() returns
    # [] unless OPENPROGRAM_FALLBACK_MODELS is set, in which case the default
    # stream fn is wrapped to try those models on a failover-worthy pre-content
    # failure. Only the default fn is wrapped (a caller-supplied stream_fn is
    # left untouched); wrapped in try/except so failover can never break the
    # normal path.
    if stream_fn is None:
        try:
            from openprogram.providers.utils.failover import (
                resolve_fallback_models,
                failover_stream_fn,
            )
            _fallbacks = resolve_fallback_models(config.model)
            if _fallbacks:
                fn = failover_stream_fn(fn, _fallbacks)
        except Exception:
            pass

    # Resolve API key
    resolved_api_key = config.api_key
    if config.get_api_key:
        key_result = config.get_api_key(config.model.provider)
        if inspect.isawaitable(key_result):
            key_result = await key_result
        resolved_api_key = key_result or resolved_api_key

    from openprogram.providers import SimpleStreamOptions
    stream_opts = SimpleStreamOptions(
        reasoning=config.reasoning,
        thinking_budgets=config.thinking_budgets,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        signal=cancel_event,
        api_key=resolved_api_key,
        transport=config.transport,
        cache_retention=config.cache_retention,
        session_id=config.session_id,
        on_payload=config.on_payload,
        headers=config.headers,
        max_retry_delay_ms=config.max_retry_delay_ms,
        metadata=config.metadata,
        service_tier=config.service_tier,
        tool_choice=config.tool_choice,
        parallel_tool_calls=config.parallel_tool_calls,
        web_search=config.web_search,
    )

    partial_message: AssistantMessage | None = None
    added_partial = False

    response_stream = fn(config.model, llm_context, stream_opts)

    async for event in response_stream:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            ev_stream.push(AgentEventMessageStart(message=partial_message))
            emit_safe("model.response_started", "agent")

        elif event.type in (
            "text_start", "text_delta", "text_end",
            "thinking_start", "thinking_delta", "thinking_end",
            "toolcall_start", "toolcall_delta", "toolcall_end",
        ):
            if partial_message is not None:
                partial_message = event.partial
                context.messages[-1] = partial_message
                ev_stream.push(AgentEventMessageUpdate(
                    message=partial_message,
                    assistant_message_event=event,
                ))

        elif event.type in ("done", "error"):
            final_message = event.message if event.type == "done" else event.error
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                ev_stream.push(AgentEventMessageStart(message=final_message))
            ev_stream.push(AgentEventMessageEnd(message=final_message))
            emit_safe("model.response_completed", "agent",
                      {"is_error": event.type == "error"})
            if event.type == "done":
                _memory_sync_turn(messages, final_message)
            return final_message

    # Fallback: return partial if no done/error event
    if partial_message:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Request was aborted")
        return partial_message

    raise RuntimeError("Stream ended without a final message")


# Bash checkpoint: snapshot cwd state before/after to catch file mutations

_BASH_LIKE_TOOLS = frozenset({"bash"})


# Directories never worth walking for agent-authored edits: VCS
# internals, dependency trees, build output, caches. Skipping them is
# what keeps the recursive scan cheap in a real project.
_SCAN_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next",
    "dist", "build", "target", ".gradle", ".idea", ".cache", "vendor",
    ".terraform", "site-packages", ".openprogram",
})
# Bounds so a bash run inside a huge tree can't stall the turn. Depth 6
# reaches normal source layouts; the file cap is a hard stop.
_SCAN_MAX_DEPTH = 6
_SCAN_MAX_FILES = 20000


def _walk_scan(root: str):
    """Yield (path, (mtime_ns, size)) for files under ``root``.

    Recursive but bounded: skips dot-entries and `_SCAN_SKIP_DIRS`, stops
    at `_SCAN_MAX_DEPTH` and `_SCAN_MAX_FILES`. Shared by the before and
    after passes so both see exactly the same file set — if they diverged,
    the diff would report phantom changes.
    """
    import os

    seen = 0
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _SCAN_SKIP_DIRS or depth >= _SCAN_MAX_DEPTH:
                        continue
                    stack.append((entry.path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    yield entry.path, (st.st_mtime_ns, st.st_size)
                    seen += 1
                    if seen >= _SCAN_MAX_FILES:
                        return
            except OSError:
                continue


# Per-file cap on the pre-command content staging. A bash turn in a
# tree full of large binaries would otherwise copy gigabytes to stage
# files it will probably never touch.
_STAGE_MAX_BYTES = 5 * 1024 * 1024


class _BashPreState:
    """Pre-command view of the tree: stat map + staged copies of contents.

    The stat map alone can only say WHICH files changed; restoring them
    needs the bytes as they were BEFORE the command ran, which is why
    every candidate is copied to ``stage_dir`` up front. Files over
    `_STAGE_MAX_BYTES` are still stat-tracked (so the change is noticed)
    but not staged — see `staged` for which ones have bytes.
    """

    __slots__ = ("stats", "stage_dir", "staged")

    def __init__(self, stats: dict, stage_dir: str, staged: dict[str, str]):
        self.stats = stats
        self.stage_dir = stage_dir
        self.staged = staged

    def cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.stage_dir, ignore_errors=True)


def _snapshot_cwd(tool_name: str) -> _BashPreState | None:
    """For bash-like tools, record cwd file stats AND stage their contents.

    Walks subdirectories (bounded — see `_walk_scan`), because bash can
    write anywhere in the tree, not just the top level. Each scanned file
    is copied into a temp staging dir so that after the command runs we
    can checkpoint the *pre-command* bytes; checkpointing from the live
    path at that point would archive the already-modified content and
    make Undo a silent no-op.

    Returns None for non-bash tools (they have their own per-file backup).
    """
    if tool_name not in _BASH_LIKE_TOOLS:
        return None
    try:
        import logging
        import os
        import shutil
        import tempfile
        from openprogram.worktree.context import current_worktree_path

        cwd = current_worktree_path() or os.getcwd()
        stats = dict(_walk_scan(cwd))
        stage_dir = tempfile.mkdtemp(prefix="op-bash-ckpt-")
        staged: dict[str, str] = {}
        skipped = 0
        for i, (path, (_mtime, size)) in enumerate(stats.items()):
            if size > _STAGE_MAX_BYTES:
                skipped += 1
                continue
            dst = os.path.join(stage_dir, f"{i:06d}")
            try:
                shutil.copy2(path, dst)
                staged[path] = dst
            except OSError:
                continue
        if skipped:
            logging.getLogger(__name__).debug(
                "bash checkpoint: %d file(s) over %d bytes not staged; "
                "their pre-command contents are unrecoverable",
                skipped, _STAGE_MAX_BYTES,
            )
        return _BashPreState(stats, stage_dir, staged)
    except Exception:
        return None


def _checkpoint_changed_files(
    tool_name: str,
    pre: "_BashPreState | None",
) -> None:
    """Compare post-execution file state to *pre* and checkpoint any changes.

    Backups are written from the staged pre-command copy, not from the
    live path — by now the command has already rewritten it.
    """
    if pre is None or tool_name not in _BASH_LIKE_TOOLS:
        return
    try:
        import os
        from openprogram.worktree.context import current_worktree_path
        from openprogram.store.snapshot.checkpoint.helpers import checkpoint_before_edit

        cwd = current_worktree_path() or os.getcwd()
        post = dict(_walk_scan(cwd))
        for path, stat in post.items():
            prev = pre.stats.get(path)
            if prev is not None and prev == stat:
                continue
            # Modified file → back up the staged pre-image. Otherwise the
            # file did not exist pre-command (or was too big to stage), so
            # point at a path that cannot exist: the checkpoint then records
            # pre_existing=False and Undo deletes the file, which is right
            # for a creation and the only safe answer for an unstaged one.
            src = pre.staged.get(path) or os.path.join(pre.stage_dir, "__absent__")
            checkpoint_before_edit(path, src)
        # ponytail: bash-only deletions stay unrecoverable. Restoring them
        # would mean checkpointing every staged file, not just the changed
        # ones — a full pre-turn copy of the tree in the manifest. Add when
        # deletion-undo is actually asked for.
    except Exception:
        pass
    finally:
        try:
            pre.cleanup()
        except Exception:
            pass


async def _execute_tool_calls(
    tools: list[AgentTool] | None,
    assistant_message: AssistantMessage,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    get_steering_messages: Any | None = None,
) -> dict[str, Any]:
    """
    Execute tool calls from an assistant message.
    Mirrors executeToolCalls() in TypeScript.
    """
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCall)]
    results: list[ToolResultMessage] = []
    steering_messages: list[AgentMessage] | None = None

    from openprogram.context.cache_aware_microcompact import increment_tool_calls
    increment_tool_calls(len(tool_calls))

    for index, tool_call in enumerate(tool_calls):
        tool = next((t for t in (tools or []) if t.name == tool_call.name), None)

        ev_stream.push(AgentEventToolStart(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
        ))

        # Plugin hook: fire tool.before_use so plugins can observe
        # (and, future-work, veto / mutate) tool calls. Failures
        # absorbed by dispatch_hook.
        try:
            from openprogram.plugins.hooks import dispatch_hook, HookEvent
            dispatch_hook(HookEvent.TOOL_BEFORE_USE, {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "args": tool_call.arguments,
            })
        except Exception:
            pass

        # 事件层：tool.before 一份事件，观察（异步总线）+ 问询（同步 gate）共用。
        before_ev = make_event("tool.before", "agent",
                               {"tool": tool_call.name, "args": tool_call.arguments})
        try:
            get_event_bus().emit(before_ev)
        except Exception:
            pass
        gate_denial = decide_tool_gate(before_ev)

        result: AgentToolResult
        is_error = False

        try:
            if gate_denial is not None:
                raise ToolGateDenied(f"Tool call blocked: {gate_denial}")
            if not tool:
                raise ValueError(f"Tool {tool_call.name} not found")

            # Build a Tool-compatible object for validation
            from openprogram.providers.types import Tool as AiTool, ToolCall as AiToolCall
            ai_tool = AiTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
            validated_args = validate_tool_arguments(ai_tool, tool_call)

            def on_update(partial_result: AgentToolResult) -> None:
                ev_stream.push(AgentEventToolUpdate(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                    partial_result=partial_result,
                ))

            pre_snapshot = _snapshot_cwd(tool_call.name)
            try:
                result = await tool.execute(tool_call.id, validated_args, cancel_event, on_update)
            except BaseException:
                # A raising / cancelled bash may still have written files,
                # so checkpoint before re-raising — and either way this is
                # what frees the staging dir.
                _checkpoint_changed_files(tool_call.name, pre_snapshot)
                raise
            _checkpoint_changed_files(tool_call.name, pre_snapshot)
        except Exception as e:
            result = AgentToolResult(
                content=[TextContent(type="text", text=str(e))],
                details={},
            )
            is_error = True
        except BaseException as e:
            # User-triggered cancel (openprogram.agentic_programming.function.CancelledError
            # is a BaseException so user-written `except Exception` inside tool bodies
            # cannot swallow it). Push a tool_end event so the UI sees the call
            # closed, then re-raise to abort the agent loop. The outer _run handler
            # ends the event stream gracefully so the chat dispatcher unblocks and
            # `running_task` is cleared — without this the stop button keeps showing
            # because the asyncio Task is killed by an "unretrieved" BaseException
            # and the stream never terminates.
            result = AgentToolResult(
                content=[TextContent(type="text", text=f"Cancelled: {e}")],
                details={},
            )
            ev_stream.push(AgentEventToolEnd(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
                is_error=True,
            ))
            raise

        ev_stream.push(AgentEventToolEnd(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            is_error=is_error,
        ))
        emit_safe("tool.after", "tool",
                  {"tool": tool_call.name, "is_error": is_error})

        try:
            from openprogram.plugins.hooks import dispatch_hook, HookEvent
            dispatch_hook(HookEvent.TOOL_AFTER_USE, {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "is_error": is_error,
                # We expose only the text channel of the result —
                # binary attachments can be huge and rarely useful
                # for hooks.
                "result_text": "".join(
                    c.text for c in (result.content or [])
                    if hasattr(c, "text") and isinstance(c.text, str)
                ),
            })
        except Exception:
            pass

        tool_result_msg = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=result.content,
            details=result.details,
            is_error=is_error,
            timestamp=int(time.time() * 1000),
        )
        results.append(tool_result_msg)
        ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
        ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

        # Check for steering messages after each tool execution
        if get_steering_messages:
            steering = await get_steering_messages()
            if steering:
                steering_messages = steering
                # Skip remaining tool calls
                remaining = tool_calls[index + 1:]
                for skipped in remaining:
                    results.append(_skip_tool_call(skipped, ev_stream))
                break

    return {"tool_results": results, "steering_messages": steering_messages}


def _skip_tool_call(
    tool_call: ToolCall,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
) -> ToolResultMessage:
    """Create a skipped tool result. Mirrors skipToolCall() in TypeScript."""
    result = AgentToolResult(
        content=[TextContent(type="text", text="Skipped due to queued user message.")],
        details={},
    )

    ev_stream.push(AgentEventToolStart(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        args=tool_call.arguments,
    ))
    ev_stream.push(AgentEventToolEnd(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result=result,
        is_error=True,
    ))

    tool_result_msg = ToolResultMessage(
        role="toolResult",
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=result.content,
        details={},
        is_error=True,
        timestamp=int(time.time() * 1000),
    )
    ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
    ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

    return tool_result_msg
