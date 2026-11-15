"""
AWS Bedrock Converse Stream API provider.

Streams responses from Bedrock models (Claude, etc.) with tool calling,
thinking/reasoning, and prompt caching support.

Mirrors amazon-bedrock.ts
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

from openprogram.models.providers.catalog.models import calculate_cost
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.models.providers.utils.json_parse import parse_streaming_json
from openprogram.models.providers.utils.sanitize_unicode import sanitize_surrogates
from openprogram.models.providers.shared.validate_modalities import validate_input_modalities

if TYPE_CHECKING:
    from openprogram.models.providers.types import (
        CacheRetention,
        Context,
        Model,
        SimpleStreamOptions,
        ThinkingLevel,
    )


def _new_usage() -> dict[str, Any]:
    return {
        "tokens_reported": False,
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "total_tokens": 0,
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    }


def _supports_prompt_caching(model: "Model") -> bool:
    cost = getattr(model, "cost", None)
    if cost and (getattr(cost, "cache_read", 0) or getattr(cost, "cache_write", 0)):
        return True
    mid = model.id.lower()
    if "claude" in mid and (mid.count("-4-") or mid.count("-4.")):
        return True
    if "claude-3-7-sonnet" in mid or "claude-3-5-haiku" in mid:
        return True
    return False


def _supports_adaptive_thinking(model_id: str) -> bool:
    return "opus-4-6" in model_id or "opus-4.6" in model_id


def _supports_thinking_signature(model_id: str) -> bool:
    mid = model_id.lower()
    return "anthropic.claude" in mid or "anthropic/claude" in mid


def _resolve_cache_retention(cache_retention: "CacheRetention | None") -> "CacheRetention":
    from openprogram.models.providers.settings.cache_spec import with_lifetime_setting
    if not cache_retention and os.environ.get("PI_CACHE_RETENTION") == "long":
        return "long"
    return with_lifetime_setting(cache_retention)


def _normalize_tool_call_id(id_: str, _model=None, _msg=None) -> str:
    # transform_messages 用 3 参签名 (id, model, msg) 调; 这里只用 id.
    import re
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", id_)
    return sanitized[:64]


def _map_thinking_level_to_effort(level: "ThinkingLevel | None") -> str:
    from openprogram.models.providers.settings.thinking_spec import translate_reasoning
    result = translate_reasoning("amazon-bedrock", "", level or "high")
    return result if isinstance(result, str) else "high"


def _build_output_config(response_format: Any) -> dict[str, Any]:
    import json

    schema = json.dumps(
        response_format.schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    definition = {"name": response_format.name, "schema": schema}
    if response_format.description is not None:
        definition["description"] = response_format.description
    return {
        "textFormat": {
            "type": "json_schema",
            "structure": {"jsonSchema": definition},
        }
    }


def stream_bedrock(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream responses from AWS Bedrock Converse Stream API."""
    from openprogram.host.security.safe_http import require_active_sdk_transport

    require_active_sdk_transport(
        "provider.amazon_bedrock.sdk",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    opts = options or {}
    ev_stream: EventStream = EventStream()

    validate_input_modalities(model, context)

    async def _run() -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "The optional Bedrock provider is unavailable in this runtime."
            )


        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": "bedrock-converse-stream",
            "provider": model.provider,
            "model": model.id,
            "usage": _new_usage(),
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }
        blocks: list[dict[str, Any]] = output["content"]

        region = (
            opts.get("region")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )

        # boto3 retry config: "standard" mode retries throttling /
        # 5xx with exponential backoff; default retries=3. Match
        # other providers' stream retry budget; override via
        # ``OPENPROGRAM_BEDROCK_MAX_RETRIES``.
        boto_config = _build_boto_retry_config()

        client_kwargs: dict[str, Any] = {"region_name": region, "config": boto_config}
        if opts.get("profile"):
            import boto3.session
            session = boto3.session.Session(profile_name=opts["profile"])
            client = session.client("bedrock-runtime", region_name=region, config=boto_config)
        else:
            if os.environ.get("AWS_BEDROCK_SKIP_AUTH") == "1":
                client_kwargs["aws_access_key_id"] = "dummy-access-key"
                client_kwargs["aws_secret_access_key"] = "dummy-secret-key"
            client = boto3.client("bedrock-runtime", **client_kwargs)

        cache_retention = _resolve_cache_retention(opts.get("cache_retention"))
        try:
            messages = _convert_messages_bedrock(context, model, cache_retention)
            system_blocks = _build_system_prompt_bedrock(context.system_prompt, model, cache_retention)
            inference_config: dict[str, Any] = {}
            if opts.get("max_tokens"):
                inference_config["maxTokens"] = opts["max_tokens"]
            if opts.get("temperature") is not None:
                inference_config["temperature"] = opts["temperature"]

            tool_config = _convert_tool_config_bedrock(
                getattr(context, "tools", None),
                opts.get("tool_choice"),
                model,
            )
            add_fields = _build_additional_model_request_fields(model, opts)

            request: dict[str, Any] = {
                "modelId": model.id,
                "messages": messages,
                "inferenceConfig": inference_config,
            }
            if system_blocks:
                request["system"] = system_blocks
            if tool_config:
                request["toolConfig"] = tool_config
            if add_fields:
                request["additionalModelRequestFields"] = add_fields
            if opts.get("response_format") is not None:
                request["outputConfig"] = _build_output_config(opts["response_format"])

            if opts.get("on_payload"):
                opts["on_payload"](request)
            from ...recording.capture import BEDROCK, capture_request
            capture_request(model, request, wire=BEDROCK)

            _sig = opts.get("signal")
            async with contextlib.aclosing(_converse_events(client, request)) as items:
                async for item in items:
                    # Caller cancel (Stop button) — polled per event; raising
                    # closes the boto3 event stream.
                    if _sig is not None and callable(getattr(_sig, "is_set", None)) and _sig.is_set():
                        from openprogram.models.providers.utils.errors import StreamAborted
                        raise StreamAborted("stream cancelled by caller signal")
                    if "messageStart" in item:
                        ev_stream.push({"type": "start", "partial": output})
                    elif "contentBlockStart" in item:
                        _handle_block_start_bedrock(item["contentBlockStart"], blocks, output, ev_stream)
                    elif "contentBlockDelta" in item:
                        _handle_block_delta_bedrock(item["contentBlockDelta"], blocks, output, ev_stream)
                    elif "contentBlockStop" in item:
                        _handle_block_stop_bedrock(item["contentBlockStop"], blocks, output, ev_stream)
                    elif "messageStop" in item:
                        output["stop_reason"] = _map_stop_reason_bedrock(item["messageStop"].get("stopReason"))
                    elif "metadata" in item:
                        _handle_metadata_bedrock(item["metadata"], model, output)
                    elif "internalServerException" in item:
                        raise RuntimeError(f"Internal server error: {item['internalServerException'].get('message', '')}")
                    elif "modelStreamErrorException" in item:
                        raise RuntimeError(f"Model stream error: {item['modelStreamErrorException'].get('message', '')}")
                    elif "validationException" in item:
                        raise RuntimeError(f"Validation error: {item['validationException'].get('message', '')}")
                    elif "throttlingException" in item:
                        raise RuntimeError(f"Throttling error: {item['throttlingException'].get('message', '')}")
                    elif "serviceUnavailableException" in item:
                        raise RuntimeError(f"Service unavailable: {item['serviceUnavailableException'].get('message', '')}")

            output["stop_reason"] = output.get("stop_reason", "stop")
            if output["stop_reason"] in ("error", "aborted"):
                raise RuntimeError("stream ended with error stop_reason")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for block in blocks:
                block.pop("index", None)
                block.pop("partial_json", None)
            # User cancel — finalize as "aborted" (anthropic's cancel
            # semantics), preserving whatever content already streamed.
            _sig_ = opts.get("signal")
            if _sig_ is not None and callable(getattr(_sig_, "is_set", None)) and _sig_.is_set():
                output["stop_reason"] = "aborted"
                output["error_message"] = str(exc)
                ev_stream.push({"type": "error", "reason": "aborted", "error": output})
                ev_stream.end(output)
                return
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            # ev_stream.fail() — see openai-codex / openai_responses for
            # the rationale (push-error + end pattern lets agent_loop
            # think the stream succeeded and busy-loop on retry).
            ev_stream.fail(exc)

    # Owned by the stream: closing it (a steer, a stop, the idle bound)
    # cancels the request instead of leaving it to wait on the socket.
    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


async def _converse_events(client: Any, request: dict[str, Any]):
    """Yield the ``ConverseStream`` events of ``request``, read on a thread of their own.

    boto3 is synchronous: its request and each event-stream read block. Run
    on the event loop, one stalled read froze every session in the process
    until botocore's own read timeout, and neither the stream idle bound nor
    a cancel could act. Here the request and the reads run on a daemon
    thread that hands each event to the loop, so the loop only awaits.
    Closing this generator (the bound, a stop, a steer) releases the event
    stream and a read still in flight (:func:`_release`); whatever the
    thread ends with afterwards is dropped.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    closed = threading.Event()
    opened: dict[str, Any] = {}

    def deliver(kind: str, value: Any) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))
        except RuntimeError:  # the loop is gone; nobody is reading
            closed.set()

    def read() -> None:
        try:
            stream = client.converse_stream(**request)["stream"]
            opened["stream"] = stream
            if closed.is_set():
                stream.close()
                return
            for item in stream:
                if closed.is_set():
                    return
                deliver("item", item)
            deliver("end", None)
        except BaseException as exc:  # noqa: BLE001 -- handed to the loop
            if not closed.is_set():
                deliver("error", exc)

    threading.Thread(target=read, name="bedrock-converse-stream", daemon=True).start()
    try:
        while True:
            kind, value = await queue.get()
            if kind == "item":
                yield value
            elif kind == "end":
                return
            else:
                raise value
    finally:
        closed.set()
        stream = opened.get("stream")
        if stream is not None:
            _release(stream)


def _release(stream: Any) -> None:
    """Close a botocore event stream without blocking the event loop.

    ``close()`` waits for the buffered reader a stalled read holds, so it
    runs on its own thread; shutting the socket down first ends that read at
    once instead of at botocore's read timeout.
    """
    raw = getattr(stream, "_raw_stream", None)  # urllib3 HTTPResponse
    sock = getattr(getattr(raw, "connection", None), "sock", None)
    if sock is None:
        # A response that closes its connection keeps the socket only in
        # its file object: http.client HTTPResponse -> BufferedReader -> SocketIO.
        fp = getattr(getattr(raw, "_fp", None), "fp", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)

    def close() -> None:
        with contextlib.suppress(Exception):
            stream.close()

    threading.Thread(target=close, name="bedrock-converse-stream-close", daemon=True).start()


def _build_boto_retry_config():
    from botocore.config import Config as _BotoConfig

    boto_max_retries = int(os.environ.get("OPENPROGRAM_BEDROCK_MAX_RETRIES", "3"))
    from ...streaming.budget import provider_retry_attempts

    boto_total_attempts = provider_retry_attempts(boto_max_retries + 1)
    return _BotoConfig(
        retries={"total_max_attempts": boto_total_attempts, "mode": "standard"},
    )


def stream_simple_bedrock(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for Bedrock streaming."""
    from openprogram.models.providers.shared.simple_options import adjust_max_tokens_for_thinking, build_base_options, clamp_reasoning

    base = build_base_options(model, options)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    if base.response_format is not None:
        base_dict["response_format"] = base.response_format
    reasoning = getattr(options, "reasoning", None) if options else None

    if not reasoning:
        base_dict.pop("reasoning", None)
        return stream_bedrock(model, context, base_dict)

    model_id = model.id
    if "anthropic.claude" in model_id or "anthropic/claude" in model_id:
        if _supports_adaptive_thinking(model_id):
            return stream_bedrock(model, context, {**base_dict, "reasoning": reasoning})

        adj_max, adj_budget = adjust_max_tokens_for_thinking(
            base_dict.get("max_tokens", 0) or 0,
            model.max_tokens,
            reasoning,
            getattr(options, "thinking_budgets", None),
        )
        level_key = clamp_reasoning(reasoning) or "low"
        budgets = {**(getattr(options, "thinking_budgets", None) or {}), level_key: adj_budget}
        return stream_bedrock(model, context, {
            **base_dict,
            "max_tokens": adj_max,
            "reasoning": reasoning,
            "thinking_budgets": budgets,
        })

    return stream_bedrock(model, context, {**base_dict, "reasoning": reasoning})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_messages_bedrock(
    context: "Context",
    model: "Model",
    cache_retention: "CacheRetention",
) -> list[dict[str, Any]]:
    from openprogram.models.providers.shared.transform_messages import transform_messages

    result: list[dict[str, Any]] = []
    transformed = transform_messages(context.messages, model, _normalize_tool_call_id)

    i = 0
    while i < len(transformed):
        msg = transformed[i]
        role = getattr(msg, "role", None)

        if role == "user":
            content_val = msg.content
            if isinstance(content_val, str):
                bedrock_content = [{"text": sanitize_surrogates(content_val)}]
            else:
                bedrock_content = []
                for c in content_val:
                    c_type = getattr(c, "type", None)
                    if c_type == "text":
                        bedrock_content.append({"text": sanitize_surrogates(c.text)})
                    elif c_type == "image":
                        bedrock_content.append({"image": _create_image_block_bedrock(c.mime_type, c.data)})
            result.append({"role": "user", "content": bedrock_content})

        elif role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            for c in msg.content:
                c_type = getattr(c, "type", None)
                if c_type == "text":
                    txt = getattr(c, "text", "") or ""
                    if not txt.strip():
                        continue
                    content_blocks.append({"text": sanitize_surrogates(txt)})
                elif c_type == "toolCall":
                    content_blocks.append({
                        "toolUse": {
                            "toolUseId": c.id,
                            "name": c.name,
                            "input": getattr(c, "arguments", {}) or {},
                        }
                    })
                elif c_type == "thinking":
                    thinking = getattr(c, "thinking", "") or ""
                    if not thinking.strip():
                        continue
                    if _supports_thinking_signature(model.id):
                        content_blocks.append({
                            "reasoningContent": {
                                "reasoningText": {
                                    "text": sanitize_surrogates(thinking),
                                    "signature": getattr(c, "thinking_signature", None),
                                }
                            }
                        })
                    else:
                        content_blocks.append({
                            "reasoningContent": {
                                "reasoningText": {"text": sanitize_surrogates(thinking)}
                            }
                        })
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})

        elif role == "toolResult":
            tool_results: list[dict[str, Any]] = []

            def _make_tool_result(tr_msg: Any) -> dict[str, Any]:
                content_items = []
                for c in tr_msg.content:
                    c_type = getattr(c, "type", None)
                    if c_type == "image":
                        content_items.append({"image": _create_image_block_bedrock(c.mime_type, c.data)})
                    else:
                        content_items.append({"text": sanitize_surrogates(c.text)})
                return {
                    "toolResult": {
                        "toolUseId": tr_msg.tool_call_id,
                        "content": content_items,
                        "status": "error" if tr_msg.is_error else "success",
                    }
                }

            tool_results.append(_make_tool_result(msg))
            j = i + 1
            while j < len(transformed) and getattr(transformed[j], "role", None) == "toolResult":
                tool_results.append(_make_tool_result(transformed[j]))
                j += 1
            # The instructions those calls brought along go after the
            # results, in the same user message (``tool_attachments``).
            from openprogram.models.providers.shared.tool_attachments import AttachmentMessage
            while j < len(transformed) and isinstance(transformed[j], AttachmentMessage):
                tool_results.extend({"text": sanitize_surrogates(c.text)}
                                    for c in transformed[j].content
                                    if getattr(c, "type", None) == "text" and c.text.strip())
                j += 1
            i = j - 1
            result.append({"role": "user", "content": tool_results})

        i += 1

    # Add cache point to last user message for supported models
    if cache_retention != "none" and _supports_prompt_caching(model) and result:
        last = result[-1]
        if last.get("role") == "user":
            cache_point: dict[str, Any] = {"type": "default"}
            if cache_retention == "long":
                cache_point["ttl"] = "ONE_HOUR"
            last["content"].append({"cachePoint": cache_point})

    return result


def _build_system_prompt_bedrock(
    system_prompt: str | None,
    model: "Model",
    cache_retention: "CacheRetention",
) -> list[dict[str, Any]] | None:
    if not system_prompt:
        return None
    blocks: list[dict[str, Any]] = [{"text": sanitize_surrogates(system_prompt)}]
    if cache_retention != "none" and _supports_prompt_caching(model):
        cache_point: dict[str, Any] = {"type": "default"}
        if cache_retention == "long":
            cache_point["ttl"] = "ONE_HOUR"
        blocks.append({"cachePoint": cache_point})
    return blocks


def _create_image_block_bedrock(mime_type: str, data: str) -> dict[str, Any]:
    fmt_map = {
        "image/jpeg": "jpeg", "image/jpg": "jpeg",
        "image/png": "png", "image/gif": "gif",
        "image/webp": "webp",
    }
    fmt = fmt_map.get(mime_type.lower(), "jpeg")
    return {"format": fmt, "source": {"bytes": data}}


def _convert_tool_config_bedrock(
    tools: list | None,
    tool_choice: Any,
    model: "Model | None" = None,
) -> dict[str, Any] | None:
    if not tools or tool_choice == "none":
        return None
    # Route through the unified dialect layer for a uniform call site.
    # Bedrock resolves to passthrough today (it's not in the strict
    # family); when Bedrock-hosted Claude strict is wanted, it's a
    # one-line change in ``schema.dialect_for`` rather than here.
    from openprogram.models.providers.schema import normalize_for
    api = getattr(model, "api", None)
    mid = getattr(model, "id", None)
    bedrock_tools = [
        {
            "toolSpec": {
                "name": t.name,
                "description": t.description,
                "inputSchema": {"json": normalize_for(api, t.parameters, mid)},
            }
        }
        for t in tools
    ]
    config: dict[str, Any] = {"tools": bedrock_tools}
    if tool_choice == "auto" or tool_choice is None:
        config["toolChoice"] = {"auto": {}}
    elif tool_choice == "any":
        config["toolChoice"] = {"any": {}}
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
        config["toolChoice"] = {"tool": {"name": tool_choice["name"]}}
    return config


def _build_additional_model_request_fields(
    model: "Model",
    opts: dict[str, Any],
) -> dict[str, Any] | None:
    reasoning = opts.get("reasoning")
    if not reasoning:
        return None
    model_id = model.id
    if "anthropic.claude" not in model_id and "anthropic/claude" not in model_id:
        return None

    if _supports_adaptive_thinking(model_id):
        effort = _map_thinking_level_to_effort(reasoning)
        return {"thinking": {"type": "adaptive", "effort": effort}}
    else:
        from openprogram.models.providers.shared.simple_options import adjust_max_tokens_for_thinking, clamp_reasoning
        budgets = opts.get("thinking_budgets") or {}
        max_t = opts.get("max_tokens", 0) or 0
        _adj_max, budget = adjust_max_tokens_for_thinking(
            max_t, model.max_tokens, reasoning, budgets
        )
        level_key = clamp_reasoning(reasoning) or "low"
        final_budget = budgets.get(level_key, budget)
        return {"thinking": {"type": "enabled", "budget_tokens": final_budget}}


def _handle_block_start_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    index = event.get("contentBlockIndex", 0)
    start = event.get("start", {})
    if start.get("toolUse"):
        block = {
            "type": "toolCall",
            "id": start["toolUse"].get("toolUseId", ""),
            "name": start["toolUse"].get("name", ""),
            "arguments": {},
            "partial_json": "",
            "index": index,
        }
        output["content"].append(block)
        ev_stream.push({"type": "toolcall_start", "content_index": len(blocks) - 1, "partial": output})


def _handle_block_delta_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    content_block_index = event.get("contentBlockIndex", 0)
    delta = event.get("delta", {})

    idx = next((i for i, b in enumerate(blocks) if b.get("index") == content_block_index), -1)
    block = blocks[idx] if idx >= 0 else None

    if "text" in delta and delta["text"] is not None:
        if block is None:
            new_block: dict[str, Any] = {"type": "text", "text": "", "index": content_block_index}
            output["content"].append(new_block)
            idx = len(blocks) - 1
            block = blocks[idx]
            ev_stream.push({"type": "text_start", "content_index": idx, "partial": output})
        if block and block.get("type") == "text":
            block["text"] = block.get("text", "") + delta["text"]
            ev_stream.push({"type": "text_delta", "content_index": idx, "delta": delta["text"], "partial": output})

    elif delta.get("toolUse") and block and block.get("type") == "toolCall":
        block["partial_json"] = (block.get("partial_json") or "") + (delta["toolUse"].get("input") or "")
        block["arguments"] = parse_streaming_json(block["partial_json"])
        ev_stream.push({"type": "toolcall_delta", "content_index": idx, "delta": delta["toolUse"].get("input", ""), "partial": output})

    elif delta.get("reasoningContent"):
        if block is None:
            new_block = {"type": "thinking", "thinking": "", "thinking_signature": "", "index": content_block_index}
            output["content"].append(new_block)
            idx = len(blocks) - 1
            block = blocks[idx]
            ev_stream.push({"type": "thinking_start", "content_index": idx, "partial": output})
        if block and block.get("type") == "thinking":
            rc = delta["reasoningContent"]
            if rc.get("text"):
                block["thinking"] = block.get("thinking", "") + rc["text"]
                ev_stream.push({"type": "thinking_delta", "content_index": idx, "delta": rc["text"], "partial": output})
            if rc.get("signature"):
                block["thinking_signature"] = (block.get("thinking_signature") or "") + rc["signature"]


def _handle_block_stop_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    content_block_index = event.get("contentBlockIndex", 0)
    idx = next((i for i, b in enumerate(blocks) if b.get("index") == content_block_index), -1)
    if idx < 0:
        return
    block = blocks[idx]
    block.pop("index", None)

    btype = block.get("type")
    if btype == "text":
        ev_stream.push({"type": "text_end", "content_index": idx, "content": block.get("text", ""), "partial": output})
    elif btype == "thinking":
        ev_stream.push({"type": "thinking_end", "content_index": idx, "content": block.get("thinking", ""), "partial": output})
    elif btype == "toolCall":
        block["arguments"] = parse_streaming_json(block.get("partial_json", ""))
        block.pop("partial_json", None)
        ev_stream.push({"type": "toolcall_end", "content_index": idx, "tool_call": block, "partial": output})


def _handle_metadata_bedrock(
    event: dict[str, Any],
    model: "Model",
    output: dict[str, Any],
) -> None:
    usage = event.get("usage", {})
    if usage:
        output["usage"]["tokens_reported"] = all(usage.get(key) is not None for key in ("inputTokens", "outputTokens"))
        output["usage"]["input"] = usage.get("inputTokens", 0) or 0
        output["usage"]["output"] = usage.get("outputTokens", 0) or 0
        output["usage"]["cache_read"] = usage.get("cacheReadInputTokens", 0) or 0
        output["usage"]["cache_write"] = usage.get("cacheWriteInputTokens", 0) or 0
        total = usage.get("totalTokens", 0) or output["usage"]["input"] + output["usage"]["output"]
        output["usage"]["total_tokens"] = total
        calculate_cost(model, output["usage"])


def _map_stop_reason_bedrock(reason: str | None) -> str:
    mapping = {
        "end_turn": "stop",
        "tool_use": "toolUse",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "guardrail_intervened": "error",
        "content_filtered": "error",
    }
    return mapping.get(reason or "", "stop")
