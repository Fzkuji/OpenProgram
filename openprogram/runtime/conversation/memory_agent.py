"""Synchronous memory-writer adapter over OpenProgram's normal Agent.

The memory store (``openprogram.state.memory``) asks for a writer through
``state.memory.writer_agent``; ``openprogram.runtime`` registers
:class:`OpenProgramAgent` there when it loads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from openprogram.models.usage import usage_scope
from openprogram.models.usage.event import CALL_KIND_MEMORY
from openprogram.runtime.agent.loop.session import AgentSession
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import (
    AssistantMessage,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

from openprogram.state.memory.agent_runtime.claude_code import AgentExecutionError, AgentResult

logger = logging.getLogger(__name__)


def _default_model(override: str | None) -> Model:
    from openprogram.runtime.conversation.model_tools import (
        load_agent_profile,
        resolve_model,
    )
    from openprogram.runtime.agent_registry import manager
    from openprogram.models.providers.streaming.background_llm import background_model

    spec = manager.get_default()
    profile = load_agent_profile(spec.id if spec is not None else "main")
    own = profile.get("model")
    if isinstance(own, dict):
        own = own.get("id") or own.get("model")
    if not override and not own:
        # No model of the Agent's own: write on the last used model.
        pair = background_model()
        override = f"{pair[0]}/{pair[1]}" if pair else None
    return resolve_model(profile, override)


def _fallback_models() -> list[Model]:
    """Models to write on when the chosen one cannot be used at all.

    The default Agent's own model, then the last used one: both are
    models this install is known to reach, which a hand-set writer model
    is not.
    """
    from openprogram.runtime.conversation.model_tools import (
        load_agent_profile,
        resolve_model,
    )
    from openprogram.runtime.agent_registry import manager
    from openprogram.models.providers.streaming.background_llm import background_model

    candidates: list[Model] = []
    try:
        spec = manager.get_default()
        profile = load_agent_profile(spec.id if spec is not None else "main")
    except Exception:  # noqa: BLE001 - no fallback is not a failure
        return candidates
    pair = background_model()
    overrides = [None] if profile.get("model") else []
    if pair:
        overrides.append(f"{pair[0]}/{pair[1]}")
    for override in overrides:
        try:
            candidates.append(resolve_model(profile, override))
        except Exception:  # noqa: BLE001
            continue
    return candidates


# Failures that say this model cannot serve this account at all: a
# rejected credential, a plan without access, a model the provider does
# not offer. Waiting does not fix them; another model does.
_UNUSABLE_MODEL = frozenset({
    "auth", "authentication", "authz", "authorization",
    "invalid", "invalid_request",
})


def _model_unusable(exc: AgentExecutionError) -> bool:
    reason = str(getattr(exc.reason, "value", exc.reason) or "").lower()
    if reason in _UNUSABLE_MODEL:
        return True
    # An exhausted usage plan is reported as a rate limit nobody should
    # wait out.
    return reason == "rate_limit" and exc.retryable is False


def _same(left: Model, right: Model) -> bool:
    return (left.provider, left.id) == (right.provider, right.id)


_JSON_TYPES = {
    "string": str,
    "boolean": bool,
    "array": list,
    "object": dict,
    "integer": int,
    "number": (int, float),
}
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Reply with exactly one JSON object and nothing else: no prose, "
        "no code fence. It must match this JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )


def _structured(text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON object a reply carries, if it matches the schema's shape.

    The model is asked for bare JSON, but a fenced block or a sentence
    around the object is common enough to accept. A reply that does not
    parse, or misses a required field, is None; the caller asks again.
    """
    candidates = [text.strip()]
    candidates += _FENCED.findall(text)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and _matches(value, schema):
            return value
    return None


def _matches(value: dict[str, Any], schema: dict[str, Any]) -> bool:
    properties = schema.get("properties") or {}
    for key in schema.get("required") or ():
        if key not in value:
            return False
    for key, spec in properties.items():
        if key not in value or not isinstance(spec, dict):
            continue
        expected = _JSON_TYPES.get(str(spec.get("type")))
        if expected is None:
            continue
        item = value[key]
        if expected is not bool and isinstance(item, bool):
            return False
        if not isinstance(item, expected):
            return False
    return True


def _native_tool(definition: Any) -> AgentTool:
    """Adapt the existing managed MCP tool without duplicating its policy."""

    async def execute(call_id, arguments, cancel, on_update):
        result = await definition.handler(arguments)
        blocks = result.get("content") or []
        text = "\n".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if result.get("is_error"):
            raise RuntimeError(text or f"{definition.name} failed")
        return AgentToolResult(content=[TextContent(text=text or "(no output)")])

    return AgentTool(
        name=definition.name,
        description=definition.description,
        parameters=definition.input_schema,
        label=definition.name,
        execute=execute,
    )


def _turn_records(messages: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            records.extend({
                "tool": block.name,
                "arguments": {
                    key: str(value)[:200]
                    for key, value in block.arguments.items()
                },
            } for block in message.content if isinstance(block, ToolCall))
        elif isinstance(message, ToolResultMessage):
            text = "\n".join(
                block.text for block in message.content
                if isinstance(block, TextContent)
            )
            records.append({"result": text[:300], "is_error": message.is_error})
    return records


class OpenProgramAgent:
    """Run a detached AgentSession on the default Agent's model, else the last used one."""

    def __init__(
        self,
        model: str | None = None,
        *,
        stream_fn: Any | None = None,
    ) -> None:
        try:
            self.model = _default_model(model)
        except Exception:
            # A writer model that no longer resolves is a stale setting,
            # not a reason to stop writing memory.
            if not model:
                raise
            fallback = next(iter(_fallback_models()), None)
            if fallback is None:
                raise
            logger.warning(
                "memory writer model %s is unavailable; writing on %s/%s",
                model, fallback.provider, fallback.id,
            )
            self.model = fallback
        self._stream_fn = stream_fn

    def run(self, **kwargs: Any) -> AgentResult:
        """Run on the chosen model; if it cannot be used at all, on the next.

        Only a failure before any tool ran moves to another model, so a
        retry never starts over a half-edited workspace. The model that
        worked stays chosen for the rest of this writer's runs.
        """
        tried = [self.model]
        while True:
            try:
                return self._run_once(**kwargs)
            except AgentExecutionError as exc:
                if exc.turns or not _model_unusable(exc):
                    raise
                fallback = next((
                    candidate for candidate in _fallback_models()
                    if not any(_same(candidate, seen) for seen in tried)
                ), None)
                if fallback is None:
                    raise
                logger.warning(
                    "memory writer on %s/%s failed (%s); retrying on %s/%s",
                    self.model.provider, self.model.id, exc,
                    fallback.provider, fallback.id,
                )
                tried.append(fallback)
                self.model = fallback

    def _run_once(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: str | Path,
        tools: list[Any] | None = None,
        max_turns: int = 20,
        max_budget_usd: float | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> AgentResult:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if max_budget_usd is not None:
            raise ValueError(
                "max_budget_usd is not supported by the OpenProgram writer"
            )
        if output_schema is not None:
            system_prompt = f"{system_prompt}\n\n{_schema_instruction(output_schema)}"
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"agent working directory does not exist: {resolved_cwd}")

        started = time.monotonic()
        session = AgentSession(
            self.model,
            tools=[_native_tool(tool) for tool in tools or []],
            system_prompt=system_prompt,
            thinking_level="off",
            max_iterations=max_turns,
            stream_fn=self._stream_fn,
        )
        try:
            # Every memory write, organize and verify pass comes through here.
            with usage_scope(call_kind=CALL_KIND_MEMORY):
                final = asyncio.run(session.run(prompt))
            messages = session.messages
        finally:
            session.close()

        turns = _turn_records(messages)
        if final is None:
            raise AgentExecutionError(
                "OpenProgram Agent ended without an assistant result",
                turns, system_prompt=system_prompt, prompt=prompt,
            )
        if final.stop_reason == "error":
            raise AgentExecutionError(
                final.error_message or "OpenProgram Agent failed",
                turns,
                system_prompt=system_prompt,
                prompt=prompt,
                retryable=final.error_retryable,
                reason=final.error_reason,
            )

        assistants = [
            message for message in messages
            if isinstance(message, AssistantMessage)
        ]
        text = "\n".join(
            block.text for block in final.content
            if isinstance(block, TextContent)
        ).strip()
        input_tokens = sum(message.usage.input for message in assistants)
        output_tokens = sum(message.usage.output for message in assistants)
        cache_creation = sum(message.usage.cache_write for message in assistants)
        cache_read = sum(message.usage.cache_read for message in assistants)
        cost = sum(message.usage.cost.total for message in assistants)
        duration_ms = int((time.monotonic() - started) * 1000)
        return AgentResult(
            text=text,
            structured_output=(
                _structured(text, output_schema)
                if output_schema is not None else None
            ),
            num_turns=len(assistants),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            anthropic_equivalent_cost_usd=cost or None,
            duration_ms=duration_ms,
            duration_api_ms=duration_ms,
            stop_reason=final.stop_reason,
            session_id="memory-writer",
            turns=turns,
            system_prompt=system_prompt,
            prompt=prompt,
            reply=text,
        )
