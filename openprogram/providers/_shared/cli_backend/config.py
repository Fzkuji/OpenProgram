"""CliBackendConfig — describes how to wrap a coding-agent CLI as a runtime.

Python port of openclaw's ``CliBackendConfig`` TypeScript type at
``references/openclaw/src/config/types.agent-defaults.ts:83``.

Every field has the same meaning as the upstream source; TypeScript
optional (``foo?: T``) maps to ``foo: T | None = None`` and camelCase
maps to snake_case. Literal unions map to ``Literal["a", "b"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .watchdog import ReliabilityConfig


OutputFormat = Literal["json", "text", "jsonl"]
JsonlDialect = Literal["claude-stream-json"]
LiveSession = Literal["claude-stdio"]
PromptInput = Literal["arg", "stdin"]
SessionMode = Literal["always", "existing", "none"]
SystemPromptMode = Literal["append", "replace"]
SystemPromptWhen = Literal["first", "always", "never"]
ImageMode = Literal["repeat", "list"]
ImagePathScope = Literal["temp", "workspace"]


@dataclass(frozen=True)
class CliBackendConfig:
    """How to invoke a CLI backend.

    One filled-out instance per supported CLI. See
    ``extensions/anthropic/cli-backend.ts`` for the canonical example.
    """

    # --- process ---

    command: str
    """CLI command to execute. Absolute path or a name on PATH."""

    args: tuple[str, ...] = ()
    """Base args prepended to every invocation."""

    env: Optional[dict[str, str]] = None
    """Extra env vars injected into the child process."""

    clear_env: Optional[tuple[str, ...]] = None
    """Env vars explicitly unset before launch."""

    serialize: bool = False
    """If True, only one instance of this CLI runs at a time."""

    # --- input / output ---

    input: PromptInput = "arg"
    """Whether the prompt is passed as a CLI arg or written to stdin."""

    max_prompt_arg_chars: Optional[int] = None
    """In ``input=arg`` mode, auto-switch to stdin above this length."""

    output: OutputFormat = "json"
    """How to parse stdout on a fresh run."""

    resume_output: Optional[OutputFormat] = None
    """Override ``output`` when resuming a session (rare)."""

    jsonl_dialect: Optional[JsonlDialect] = None
    """JSONL event dialect for CLIs with provider-specific stream formats."""

    live_session: Optional[LiveSession] = None
    """Keep one CLI process alive across turns instead of respawning."""

    # --- model / session ---

    model_arg: Optional[str] = None
    """Flag used to pass the model id (e.g. ``--model``)."""

    model_aliases: Optional[dict[str, str]] = None
    """Map our model id → the CLI's model id."""

    session_arg: Optional[str] = None
    """Flag used to pass the session id (e.g. ``--session-id``)."""

    session_args: Optional[tuple[str, ...]] = None
    """Extra args with ``{sessionId}`` placeholder, always applied."""

    resume_args: Optional[tuple[str, ...]] = None
    """Alternate args with ``{sessionId}`` applied only on resume."""

    session_mode: SessionMode = "none"
    """When to pass session ids."""

    session_id_fields: tuple[str, ...] = ()
    """JSONL keys to read session id from, in priority order."""

    # --- system prompt ---

    system_prompt_arg: Optional[str] = None
    """Flag used to pass the system prompt text."""

    system_prompt_file_config_arg: Optional[str] = None
    """Config-override flag used to point at a system-prompt file (e.g. ``-c``)."""

    system_prompt_file_config_key: Optional[str] = None
    """Config-override key used inside that flag's value."""

    system_prompt_mode: SystemPromptMode = "append"
    """Whether to append to the CLI's baseline prompt or replace it."""

    system_prompt_when: SystemPromptWhen = "first"
    """On which turns to pass the system prompt."""

    # --- images ---

    image_arg: Optional[str] = None
    """Flag used to pass image paths."""

    image_mode: ImageMode = "repeat"
    """Repeat the flag per image, or pass one comma/space list."""

    image_path_scope: ImagePathScope = "temp"
    """Whether staged image files live in temp or inside the workspace."""

    # --- reliability ---

    reliability: Optional[ReliabilityConfig] = None
    """Per-backend timeout / watchdog tuning. Defaults applied by runner."""


__all__ = [
    "CliBackendConfig",
    "JsonlDialect",
    "LiveSession",
    "OutputFormat",
    "PromptInput",
    "SessionMode",
    "SystemPromptMode",
    "SystemPromptWhen",
    "ImageMode",
    "ImagePathScope",
]
