"""Isolated synchronous adapter for the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query as sdk_query,
)


class AgentExecutionError(RuntimeError):
    """Claude Code could not complete an agent trajectory.

    ``turns`` carries whatever the trajectory managed before it failed,
    which is the only record of what a run that hit its turn limit spent
    those turns on.
    """

    def __init__(
        self,
        message: str,
        turns: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        prompt: str = "",
    ):
        super().__init__(message)
        self.turns = turns or []
        self.system_prompt = system_prompt
        self.prompt = prompt


# Enough of a tool call to see what a turn attempted and whether it worked,
# without carrying whole file contents into the log.
_ARGUMENT_PREVIEW = 200
_RESULT_PREVIEW = 300


def _turn_records(message: Any) -> list[dict[str, Any]]:
    """Tool calls and their results, as flat records.

    The built-in file tools never reach the MCP layer, so without this a
    trajectory that spent sixty turns retrying an Edit leaves no trace of
    what it was retrying.
    """
    records: list[dict[str, Any]] = []
    for block in getattr(message, "content", None) or []:
        name = getattr(block, "name", None)
        if name:
            arguments = getattr(block, "input", None) or {}
            records.append({
                "tool": name,
                "arguments": {
                    key: str(value)[:_ARGUMENT_PREVIEW]
                    for key, value in arguments.items()
                },
            })
            continue
        if getattr(block, "tool_use_id", None) is None:
            continue
        content = getattr(block, "content", None)
        text = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False, default=str
        )
        records.append({
            "result": text[:_RESULT_PREVIEW],
            "is_error": bool(getattr(block, "is_error", False)),
        })
    return records


@dataclass(frozen=True)
class ClaudeCodeConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    cli_path: str | None = None
    # Run as the user rather than as a separate tenant: no endpoint, no key,
    # no model. Background memory writing is work the user is already paying
    # for, so asking them to provision a second credential to get it is a
    # worse deal than the feature is worth.
    inherit_auth: bool = False

    def __post_init__(self) -> None:
        if not self.inherit_auth:
            if not self.base_url.strip():
                raise ValueError("base_url is required")
            if not self.api_key:
                raise ValueError("api_key is required")
            if not self.model.strip():
                raise ValueError("model is required")
        if self.cli_path is not None and not self.cli_path.strip():
            raise ValueError("cli_path must not be empty")

    @classmethod
    def inherited(
        cls, *, model: str | None = None, cli_path: str | None = None
    ) -> "ClaudeCodeConfig":
        """Use whatever login and model the user's own CLI already has."""
        return cls(
            model=model or "", cli_path=cli_path, inherit_auth=True
        )


@dataclass(frozen=True)
class AgentResult:
    text: str
    structured_output: Any
    num_turns: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    # Priced by the SDK against Anthropic's own rate table. When the run is
    # routed elsewhere via ANTHROPIC_BASE_URL this is not the amount billed;
    # it is only "what this token volume would cost on Anthropic".
    anthropic_equivalent_cost_usd: float | None
    duration_ms: int
    duration_api_ms: int
    stop_reason: str | None
    session_id: str
    # Every tool call and result, in order. Built-in file tools bypass the
    # MCP layer, so this is the only record of what the turns did.
    turns: list[dict[str, Any]] = field(default_factory=list)
    # Exactly what the model was sent. Reading a trajectory back means
    # knowing what it was answering, not only what it then did.
    system_prompt: str = ""
    prompt: str = ""
    reply: str = """"""


QueryFunction = Callable[..., AsyncIterator[Any]]


class ClaudeCodeAgent:
    """Run one non-persistent Claude Code process per trajectory."""

    def __init__(
        self,
        config: ClaudeCodeConfig,
        *,
        query_fn: QueryFunction | None = None,
    ) -> None:
        self.config = config
        self._query = query_fn or sdk_query

    def _redact(self, text: str) -> str:
        """Hide the key in an error message, if there is a key to hide.

        `str.replace("", x)` inserts x between every character, so an
        inherited run with no key of its own would shred the very message
        someone is trying to read.
        """
        if not self.config.api_key:
            return text
        return text.replace(self.config.api_key, "[redacted]")

    def run(
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
        if max_budget_usd is not None and max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        # Resolve before building the coroutine: an exception raised while
        # evaluating these arguments would leave _run() created but never
        # awaited, which surfaces as a RuntimeWarning far from its cause.
        resolved_cwd = Path(cwd).resolve()
        return asyncio.run(self._run(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=resolved_cwd,
            tools=tools or [],
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            output_schema=output_schema,
        ))

    async def _run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        tools: list[Any],
        max_turns: int,
        max_budget_usd: float | None,
        output_schema: dict[str, Any] | None,
    ) -> AgentResult:
        if not cwd.is_dir():
            raise ValueError(f"agent working directory does not exist: {cwd}")
        server_name = "agent_memory"
        mcp_servers = {}
        # The workspace is a scratch directory, so the built-in file tools are
        # safe here and are what the model is trained to reach for. Editing
        # through them beats scripting the same change in a shell heredoc.
        builtin_tools = ["Read", "Edit", "Write", "Grep", "Glob"]
        allowed_tools = list(builtin_tools)
        if tools:
            mcp_servers[server_name] = create_sdk_mcp_server(
                server_name, tools=tools
            )
            allowed_tools += [
                f"mcp__{server_name}__{definition.name}"
                for definition in tools
            ]

        with tempfile.TemporaryDirectory(
            prefix="agent-memory-claude-config-"
        ) as config_root:
            options = ClaudeAgentOptions(
                # `tools` is what puts schemas on the wire; `allowed_tools` only
                # filters what may run. Leaving this empty left weaker models
                # with the MCP shell as their sole visible tool, so they wrote
                # tool names into shell commands instead of calling the tools.
                tools=builtin_tools,
                allowed_tools=allowed_tools,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                permission_mode="dontAsk",
                # Empty means "whatever the CLI would pick", which is what an
                # inherited run wants.
                model=self.config.model or None,
                thinking={"type": "disabled"},
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                cwd=cwd,
                cli_path=self.config.cli_path,
                setting_sources=[],
                # Overriding these is what isolates a benchmark run from the
                # user's own login. An inherited run wants the opposite, and
                # the CLI reads its own config only when nothing is set here.
                # Empty, never None: the SDK spreads this mapping over
                # os.environ (`{**inherited_env, **options.env}`), so None
                # raises before the CLI is ever spawned, and `{}` is what
                # "add nothing to the inherited environment" looks like.
                env={} if self.config.inherit_auth else {
                    "ANTHROPIC_BASE_URL": self.config.base_url,
                    "ANTHROPIC_API_KEY": self.config.api_key,
                    "ANTHROPIC_AUTH_TOKEN": "",
                    "CLAUDE_CONFIG_DIR": config_root,
                },
                extra_args={
                    "bare": None,
                    "no-session-persistence": None,
                    "strict-mcp-config": None,
                },
                output_format=(
                    {"type": "json_schema", "schema": output_schema}
                    if output_schema is not None
                    else None
                ),
            )
            texts: list[str] = []
            turns: list[dict[str, Any]] = []
            final: ResultMessage | None = None
            try:
                async for message in self._query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        texts.extend(
                            block.text
                            for block in message.content
                            if isinstance(block, TextBlock)
                        )
                    turns.extend(_turn_records(message))
                    if isinstance(message, ResultMessage):
                        final = message
            except Exception as exc:
                if final is None:
                    message = self._redact(str(exc))
                    raise AgentExecutionError(
                        message, turns,
                        system_prompt=system_prompt, prompt=prompt,
                    ) from exc

            if final is None:
                raise AgentExecutionError(
                    "Claude Code ended without a result message", turns,
                    system_prompt=system_prompt, prompt=prompt,
                )
            if final.is_error:
                details = "; ".join(final.errors or []) or (
                    final.result or final.subtype
                )
                if getattr(final, "api_error_status", None) is not None:
                    details += f"; API status {final.api_error_status}"
                details = self._redact(details)
                raise AgentExecutionError(
                    details, turns,
                    system_prompt=system_prompt, prompt=prompt,
                )
            usage = final.usage or {}
            return AgentResult(
                text=(final.result or "\n".join(texts)).strip(),
                structured_output=final.structured_output,
                num_turns=int(final.num_turns),
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                cache_creation_input_tokens=int(
                    usage.get("cache_creation_input_tokens", 0) or 0
                ),
                cache_read_input_tokens=int(
                    usage.get("cache_read_input_tokens", 0) or 0
                ),
                anthropic_equivalent_cost_usd=final.total_cost_usd,
                duration_ms=int(final.duration_ms),
                duration_api_ms=int(final.duration_api_ms),
                stop_reason=final.stop_reason,
                session_id=final.session_id,
                turns=turns,
                system_prompt=system_prompt,
                prompt=prompt,
                reply="\n".join(texts),
            )
