"""CLI-backed runtime abstractions.

Port of openclaw's CliBackendConfig + watchdog defaults + CliBackendPlugin
contract. A CLI-backed runtime (Claude Code, Gemini CLI, Codex, ...) is
described by filling out a ``CliBackendConfig`` dataclass. A single shared
``CliRunner`` reads the config and handles subprocess lifecycle, stream
parsing, session resume, watchdog timeouts, and auth-epoch refresh — no
per-runtime subprocess code.

References:

- ``references/openclaw/src/config/types.agent-defaults.ts:83`` —
  ``CliBackendConfig`` TypeScript source of truth
- ``references/openclaw/src/agents/cli-watchdog-defaults.ts`` — default
  watchdog timings for fresh vs resumed runs
- ``references/openclaw/src/plugins/types.ts`` — ``CliBackendPlugin``
  interface (lifecycle hooks)
- ``references/openclaw/extensions/anthropic/cli-backend.ts`` — concrete
  Anthropic ``claude`` CLI wiring (the first runtime we'll port)

Names mirror openclaw's verbatim, in snake_case.
"""

from __future__ import annotations

from .config import (
    CliBackendConfig,
    JsonlDialect,
    LiveSession,
    OutputFormat,
    PromptInput,
    SessionMode,
    SystemPromptMode,
    SystemPromptWhen,
    ImageMode,
    ImagePathScope,
)
from .watchdog import (
    CLI_FRESH_WATCHDOG_DEFAULTS,
    CLI_RESUME_WATCHDOG_DEFAULTS,
    ReliabilityConfig,
    WatchdogConfig,
    WatchdogTiming,
)
from .plugin import CliBackendAuthEpochMode, CliBackendPlugin
from .runner import CliRunner, PreparedExecution, PrepareExecutionContext

__all__ = [
    # config
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
    # watchdog
    "CLI_FRESH_WATCHDOG_DEFAULTS",
    "CLI_RESUME_WATCHDOG_DEFAULTS",
    "ReliabilityConfig",
    "WatchdogConfig",
    "WatchdogTiming",
    # plugin
    "CliBackendAuthEpochMode",
    "CliBackendPlugin",
    # runner
    "CliRunner",
    "PreparedExecution",
    "PrepareExecutionContext",
]
