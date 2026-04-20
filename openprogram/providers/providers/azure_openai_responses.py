"""
Azure OpenAI Responses API provider.

Wraps Azure-specific deployment configuration and authentication.

Mirrors azure-openai-responses.ts
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import supports_xhigh
from openprogram.providers.providers.openai_responses_shared import (
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from openprogram.providers.providers.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

_DEFAULT_API_VERSION = "v1"
_AZURE_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-codex", "opencode", "azure-openai-responses"})


def stream_azure_openai_responses(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from Azure OpenAI Responses API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        try:
            from openai import AzureOpenAI
        except ImportError:
            raise ImportError("openai is required: pip install openai")

        deployment_name = _resolve_deployment_name(model, opts)

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": "azure-openai-responses",
            "provider": model.provider,
            "model": model.id,
            "usage": {
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "total_tokens": 0,
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
            },
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }

        try:
            from openprogram.providers.env_api_keys import get_env_api_key
            api_key = opts.get("api_key") or get_env_api_key(model.provider) or os.environ.get("AZURE_OPENAI_API_KEY", "")
            if not api_key:
                raise ValueError("Azure OpenAI API key is required.")

            client = _create_client(model, api_key, opts)
            params = _build_params(model, context, opts, deployment_name)

            if opts.get("on_payload"):
                opts["on_payload"](params)

            openai_stream = client.responses.create(**params, stream=True)
            ev_stream.push({"type": "start", "partial": output})

            await process_responses_stream(openai_stream, output, ev_stream, model)

            if output["stop_reason"] in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for b in output["content"]:
                if isinstance(b, dict):
                    b.pop("index", None)
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            ev_stream.push({"type": "error", "reason": "error", "error": output})
            ev_stream.end(output)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_azure_openai_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for Azure OpenAI Responses API streaming."""
    from openprogram.providers.env_api_keys import get_env_api_key
    api_key = (getattr(options, "api_key", None) if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None
    reasoning_effort = reasoning if supports_xhigh(model) else clamp_reasoning(reasoning)

    return stream_azure_openai_responses(model, context, {**base_dict, "reasoning_effort": reasoning_effort})


def _parse_deployment_name_map(value: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not value:
        return result
    for entry in value.split(","):
        parts = entry.strip().split("=", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            result[parts[0].strip()] = parts[1].strip()
    return result


def _resolve_deployment_name(model: "Model", opts: dict[str, Any]) -> str:
    if opts.get("azure_deployment_name"):
        return opts["azure_deployment_name"]
    name_map = _parse_deployment_name_map(os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP"))
    return name_map.get(model.id, model.id)


def _resolve_azure_config(model: "Model", opts: dict[str, Any]) -> tuple[str, str]:
    api_version = opts.get("azure_api_version") or os.environ.get("AZURE_OPENAI_API_VERSION") or _DEFAULT_API_VERSION

    base_url = (
        (opts.get("azure_base_url") or "").strip()
        or (os.environ.get("AZURE_OPENAI_BASE_URL") or "").strip()
    )
    resource_name = opts.get("azure_resource_name") or os.environ.get("AZURE_OPENAI_RESOURCE_NAME")

    if not base_url and resource_name:
        base_url = f"https://{resource_name}.openai.azure.com/openai/v1"
    if not base_url and getattr(model, "base_url", None):
        base_url = model.base_url

    if not base_url:
        raise ValueError(
            "Azure OpenAI base URL is required. "
            "Set AZURE_OPENAI_BASE_URL or AZURE_OPENAI_RESOURCE_NAME."
        )

    return base_url.rstrip("/"), api_version


def _create_client(model: "Model", api_key: str, opts: dict[str, Any]) -> Any:
    from openai import AzureOpenAI
    base_url, api_version = _resolve_azure_config(model, opts)
    headers = {**(getattr(model, "headers", None) or {}), **(opts.get("headers") or {})}
    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        base_url=base_url,
        default_headers=headers if headers else None,
    )


def _build_params(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
    deployment_name: str,
) -> dict[str, Any]:
    messages = convert_responses_messages(model, context, _AZURE_TOOL_CALL_PROVIDERS)
    params: dict[str, Any] = {
        "model": deployment_name,
        "input": messages,
        "stream": True,
    }
    if opts.get("session_id"):
        params["prompt_cache_key"] = opts["session_id"]
    if opts.get("max_tokens"):
        params["max_output_tokens"] = opts["max_tokens"]
    if opts.get("temperature") is not None:
        params["temperature"] = opts["temperature"]

    tools = getattr(context, "tools", None)
    if tools:
        params["tools"] = convert_responses_tools(tools)

    reasoning_effort = opts.get("reasoning_effort")
    reasoning_summary = opts.get("reasoning_summary")
    if getattr(model, "reasoning", False) and (reasoning_effort or reasoning_summary):
        params["reasoning"] = {
            "effort": reasoning_effort or "medium",
            "summary": reasoning_summary or "auto",
        }
        params["include"] = ["reasoning.encrypted_content"]

    return params
