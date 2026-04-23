"""CliRunner — the one shared subprocess runner for every CLI backend.

Skeleton only (Phase 0). Real subprocess + JSONL parser + watchdog +
session-resume + live-session implementation lands in Phase 1 when we
migrate ``ClaudeCodeRuntime``.

This file defines the callable surface consumers will see:

- ``CliRunner(plugin=..., ...).run(...)`` — async iterator of
  normalized events (text deltas, tool calls, usage, done, error)
- ``PrepareExecutionContext`` / ``PreparedExecution`` — re-exported from
  ``.plugin`` here because some callers want them together with the
  runner type.

Method signatures are chosen so Phase 1 can fill them in without
changing any import or call site.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Optional

from .config import CliBackendConfig
from .plugin import (
    CliBackendPlugin,
    PreparedExecution,
    PrepareExecutionContext,
)


@dataclass(frozen=True)
class CliEvent:
    """One normalized event emitted by a CLI run.

    Phase 0 keeps this deliberately minimal — Phase 1 will add a proper
    discriminated shape (text_delta / tool_call_start / tool_call_end /
    thinking_delta / usage / done / error) once we know which fields all
    four CLIs actually produce.
    """

    type: str
    data: dict


class CliRunner:
    """Generic subprocess runner for CLI-backed runtimes.

    Given a ``CliBackendPlugin`` (which carries a ``CliBackendConfig``),
    the runner handles:

    - Resolving command + args from config + per-call overrides
    - Spawning the subprocess with correct env / cleared env / cwd
    - Choosing ``input=arg`` vs ``input=stdin`` based on config + prompt length
    - Parsing ``output`` as ``json`` / ``text`` / ``jsonl`` (with the right
      ``jsonl_dialect`` for Claude stream-json, etc.)
    - Watchdog with fresh vs resume timings (see ``watchdog.py``)
    - Session-id extraction + persistence for resume
    - ``live_session="claude-stdio"`` long-running mode (reuse one process)
    - Running ``plugin.prepare_execution`` before launch and ``cleanup``
      after
    - Applying ``plugin.text_transforms`` on inbound/outbound text
    - Triggering restart when the auth epoch changes mid-run

    Subclasses are not expected — everything customizable lives in the
    ``CliBackendPlugin`` object. This is an intentional difference from
    our old ``ClaudeCodeRuntime``-style class hierarchy.
    """

    def __init__(
        self,
        plugin: CliBackendPlugin,
        *,
        workspace_dir: str,
        overall_timeout_ms: int = 600_000,
    ) -> None:
        self.plugin = plugin
        self.workspace_dir = workspace_dir
        self.overall_timeout_ms = overall_timeout_ms
        self._config: CliBackendConfig = plugin.config
        # Session id captured from the previous run, if any. Runner owns
        # the in-memory copy; persistence layer (to disk) is separate.
        self._session_id: Optional[str] = None
        # Live-session process handle, when ``config.live_session`` is set.
        self._live_proc: Optional[asyncio.subprocess.Process] = None
        # Auth-epoch token — bumped externally to force a restart.
        self._auth_epoch: int = 0

    # --- public entry points -----------------------------------------

    async def run(
        self,
        prompt: str,
        *,
        model_id: str,
        system_prompt: Optional[str] = None,
        image_paths: Iterable[str] = (),
        resume: bool = False,
    ) -> AsyncIterator[CliEvent]:
        """Run one turn against the CLI and yield events.

        Phase 0: not implemented. Raises ``NotImplementedError`` so
        callers get a clear failure if they wire this up too early.

        Phase 1 fills this in for ``ClaudeCodeRuntime`` first.
        """
        raise NotImplementedError(
            "CliRunner.run is a Phase-0 skeleton. "
            "Implement in Phase 1 (task #57) when migrating ClaudeCodeRuntime."
        )
        # unreachable; keeps the signature AsyncIterator
        yield  # type: ignore[unreachable]

    async def close(self) -> None:
        """Tear down any long-running live-session process.

        Phase 0: no-op. Phase 1 will kill ``_live_proc`` and await its
        exit with a short grace period.
        """
        return None

    # --- hooks callers override rarely --------------------------------

    def bump_auth_epoch(self) -> None:
        """Invalidate the current live process / resume state.

        Called when the auth layer refreshes credentials. Phase 1: next
        ``run()`` call should notice the epoch bump and spawn fresh.
        """
        self._auth_epoch += 1


__all__ = [
    "CliEvent",
    "CliRunner",
    "PreparedExecution",
    "PrepareExecutionContext",
]
