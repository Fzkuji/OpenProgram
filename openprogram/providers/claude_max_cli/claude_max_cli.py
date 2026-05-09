"""
Claude Max CLI provider.

Spawns `claude --print --output-format stream-json` as a subprocess and
converts its JSON stream into OpenProgram's standard AssistantMessageEvent
stream. Requires the Claude Code CLI to be installed and authenticated
via `claude auth login`.

Mirrors the approach of claude-max-api-proxy (npm: claude-max-api-proxy).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.utils.event_stream import EventStream

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

# Maps OpenProgram model IDs to claude CLI --model aliases
_MODEL_ALIAS: dict[str, str] = {
    "claude-opus-4": "claude-opus-4-5",
    "claude-opus-4-5": "claude-opus-4-5",
    "claude-sonnet-4": "claude-sonnet-4-5",
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-haiku-4": "claude-haiku-4-5",
    "claude-haiku-4-5": "claude-haiku-4-5",
}

_DEFAULT_TIMEOUT = 300.0  # seconds


def _model_alias(model_id: str) -> str:
    return _MODEL_ALIAS.get(model_id, model_id)


def _messages_to_prompt(context: "Context") -> str:
    """Flatten context messages into a single prompt string for the CLI."""
    parts: list[str] = []

    if context.system_prompt:
        parts.append(f"<system>\n{context.system_prompt}\n</system>")

    for msg in context.messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                parts.append(msg.content)
            else:
                parts.append(" ".join(
                    block.text for block in msg.content if hasattr(block, "text")
                ))
        elif msg.role == "assistant":
            text = " ".join(
                block.text for block in msg.content
                if hasattr(block, "text") and block.type == "text"
            )
            if text:
                parts.append(f"<previous_response>\n{text}\n</previous_response>")

    return "\n\n".join(parts).strip()


def stream_claude_max_cli(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from Claude CLI subprocess using Max/Pro subscription."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": model.api,
            "provider": model.provider,
            "model": model.id,
            "usage": {
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "total_tokens": 0,
                "cost": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0},
            },
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }

        try:
            prompt = _messages_to_prompt(context)
            alias = _model_alias(model.id)
            args = [
                "claude",
                "--print",
                "--output-format", "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--model", alias,
                "--no-session-persistence",
                prompt,
            ]

            timeout = opts.get("timeout", _DEFAULT_TIMEOUT)

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            ev_stream.push({"type": "start", "partial": output})

            current_block: dict[str, Any] | None = None

            def block_idx() -> int:
                return len(output["content"]) - 1

            async def _read_stdout() -> None:
                nonlocal current_block
                assert proc.stdout
                buffer = ""
                async for raw in proc.stdout:
                    buffer += raw.decode(errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        msg_type = msg.get("type", "")

                        # assistant message with streaming content delta
                        if msg_type == "content_block_start":
                            block = msg.get("content_block", {})
                            btype = block.get("type")
                            if btype == "text":
                                current_block = {"type": "text", "text": ""}
                                output["content"].append(current_block)
                                ev_stream.push({"type": "text_start", "content_index": block_idx(), "partial": output})
                            elif btype == "thinking":
                                current_block = {"type": "thinking", "thinking": ""}
                                output["content"].append(current_block)
                                ev_stream.push({"type": "thinking_start", "content_index": block_idx(), "partial": output})

                        elif msg_type == "content_block_delta":
                            delta_obj = msg.get("delta", {})
                            dtype = delta_obj.get("type")
                            if dtype == "text_delta" and current_block and current_block.get("type") == "text":
                                delta = delta_obj.get("text", "")
                                current_block["text"] += delta
                                ev_stream.push({"type": "text_delta", "content_index": block_idx(), "delta": delta, "partial": output})
                            elif dtype == "thinking_delta" and current_block and current_block.get("type") == "thinking":
                                delta = delta_obj.get("thinking", "")
                                current_block["thinking"] += delta
                                ev_stream.push({"type": "thinking_delta", "content_index": block_idx(), "delta": delta, "partial": output})

                        elif msg_type == "content_block_stop":
                            if current_block:
                                if current_block.get("type") == "text":
                                    ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                                elif current_block.get("type") == "thinking":
                                    ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})
                                current_block = None

                        elif msg_type == "message_delta":
                            delta = msg.get("delta", {})
                            if delta.get("stop_reason"):
                                output["stop_reason"] = _map_stop_reason(delta["stop_reason"])
                            usage = msg.get("usage", {})
                            if usage:
                                output["usage"]["output"] = usage.get("output_tokens", 0)

                        elif msg_type == "message_start":
                            inner = msg.get("message", {})
                            usage = inner.get("usage", {})
                            if usage:
                                output["usage"]["input"] = usage.get("input_tokens", 0)
                                output["usage"]["cache_read"] = usage.get("cache_read_input_tokens", 0)
                                output["usage"]["cache_write"] = usage.get("cache_creation_input_tokens", 0)

                        # stream-json result block (final summary)
                        elif msg_type == "result":
                            stats = msg.get("usage", {})
                            if stats:
                                output["usage"]["input"] = stats.get("input_tokens", output["usage"]["input"])
                                output["usage"]["output"] = stats.get("output_tokens", output["usage"]["output"])
                                output["usage"]["cache_read"] = stats.get("cache_read_input_tokens", output["usage"]["cache_read"])
                                output["usage"]["cache_write"] = stats.get("cache_creation_input_tokens", output["usage"]["cache_write"])
                            output["usage"]["total_tokens"] = (
                                output["usage"]["input"] + output["usage"]["output"]
                            )

            try:
                await asyncio.wait_for(_read_stdout(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"Claude CLI timed out after {timeout}s")

            await proc.wait()
            if proc.returncode not in (0, None):
                stderr_bytes = b""
                if proc.stderr:
                    stderr_bytes = await proc.stderr.read()
                raise RuntimeError(
                    f"Claude CLI exited with code {proc.returncode}: {stderr_bytes.decode(errors='replace')[:300]}"
                )

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            ev_stream.push({"type": "error", "reason": "error", "error": output})
            ev_stream.end(output)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_claude_max_cli(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    opts: dict[str, Any] = {}
    if options:
        if options.temperature is not None:
            opts["temperature"] = options.temperature
        if options.max_tokens is not None:
            opts["max_tokens"] = options.max_tokens
    return stream_claude_max_cli(model, context, opts)


def _map_stop_reason(reason: str) -> str:
    return {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "toolUse",
        "stop_sequence": "stop",
    }.get(reason, "stop")
