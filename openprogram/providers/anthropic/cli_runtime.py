"""ClaudeCodeRuntime — thin ``Runtime`` adapter over ``CliRunner``.

Replaces the 662-line legacy implementation at
``openprogram/legacy_providers/claude_code.py``. Subprocess lifecycle,
watchdog, session resume, and live-session reuse live in the generic
``CliRunner`` under ``openprogram/providers/_shared/cli_backend/``. This
file owns only the Runtime-shape adapter:

- ``_call(content, model, response_format) -> str`` — sync ``Runtime`` API
  on top of the async ``CliRunner.run()`` event stream
- audio / video / file block filtering (CLI doesn't accept these)
- context-aware ``/compact`` trigger based on ``Usage.context_window``
- ``list_models()`` reading the curated ``claude_models.json`` registry

Public class name stays ``ClaudeCodeRuntime`` so existing consumers
(``legacy_providers/__init__.py`` registry, WebUI) keep working.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import warnings
from pathlib import Path
from typing import Any, Optional

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers._shared.cli_backend import (
    CliRunner,
    CompactBoundary,
    Done,
    Error,
    SessionInfo,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
)

from .cli_backend import CLAUDE_CODE_PLUGIN


class ClaudeCodeRuntime(Runtime):
    """Runtime routing LLM calls through a persistent Claude Code CLI.

    Uses the Claude Code **subscription** (no API key). Requires the
    ``claude`` CLI to be installed and logged in.

    Args:
        model: Model id passed via ``--model`` (default
            ``claude-sonnet-4-6``). Must match an entry in
            ``claude_models.json``.
        timeout: Per-call overall timeout in seconds (default 600).
            Feeds ``CliRunner.overall_timeout_ms``; watchdog timings
            derive from this.
        cli_path: Override for the ``claude`` binary; auto-detected via
            ``shutil.which`` when omitted.
        session_id: Kept for API compatibility. Claude Code manages its
            own conversation state inside the persistent process — the
            runtime just sets ``has_session`` based on whether the
            argument is non-None.
        max_turns_per_process: Respawn the CLI after this many completed
            turns (default 100). Bounds accumulated CLI-side context.
        compact_ratio: When the accumulated input tokens for the next
            turn would exceed ``compact_ratio * context_window``, send
            ``/compact`` before the real prompt. Default 0.8.
        compact_cap_tokens: Upper bound on the compact trigger in
            absolute tokens — for huge context windows (>500K) we cap
            rather than letting a long tail of cache reads accumulate.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout: int = 600,
        cli_path: Optional[str] = None,
        session_id: Optional[str] = "auto",
        max_turns_per_process: int = 100,
        compact_ratio: float = 0.8,
        compact_cap_tokens: int = 500_000,
        skills: "bool | list[str] | None" = None,
    ):
        super().__init__(model=model, skills=skills)
        self.has_session = session_id is not None
        self.timeout = timeout
        self._compact_ratio = compact_ratio
        self._compact_cap_tokens = compact_cap_tokens

        # Resolve the CLI binary eagerly so a missing install surfaces
        # here rather than when the first turn tries to spawn.
        resolved_cli = cli_path or shutil.which("claude")
        if resolved_cli is None:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install and log in:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "  claude login"
            )
        self.cli_path = resolved_cli

        # Plugin is a module-level frozen default; customize per-instance
        # only when we need to override ``command`` or ``max_turns``.
        plugin = CLAUDE_CODE_PLUGIN
        if (
            resolved_cli != plugin.config.command
            or max_turns_per_process != plugin.config.max_turns_per_process
        ):
            from dataclasses import replace
            plugin = replace(
                plugin,
                config=replace(
                    plugin.config,
                    command=resolved_cli,
                    max_turns_per_process=max_turns_per_process,
                ),
            )
        self._plugin = plugin

        # Dedicated event loop + thread so sync ``_call()`` can drive the
        # async ``CliRunner`` without colliding with any caller-owned
        # loop (WebUI, agentic_programming, etc.). One thread per runtime
        # instance; daemon so it doesn't block interpreter exit if the
        # caller forgets ``close()``.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop_forever, daemon=True, name="claude-code-runner",
        )
        self._loop_thread.start()

        self._runner = CliRunner(
            plugin=plugin,
            workspace_dir=str(Path.cwd()),
            overall_timeout_ms=timeout * 1000,
        )

        # Context-aware compact state. ``_context_window_tokens`` is
        # discovered from the first turn's ``Usage.context_window`` — we
        # don't know the real window until the CLI tells us.
        self._last_context_tokens: int = 0
        self._context_window_tokens: Optional[int] = None
        # CLI's authoritative model id after any internal routing
        # (e.g. Claude Code's haiku helper vs primary model).
        self._resolved_model_id: Optional[str] = None

    # --- loop plumbing -------------------------------------------------

    def _run_loop_forever(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def _run_coro_sync(self, coro):
        """Submit an awaitable to the runtime's dedicated loop and wait."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    # --- model registry ------------------------------------------------

    def list_models(self) -> list[str]:
        """Selectable Claude Code model ids from the curated registry."""
        from openprogram.legacy_providers.claude_models import list_model_ids
        return list_model_ids()

    # --- Runtime.exec entry point --------------------------------------

    def _call(
        self,
        content: list[dict],
        model: str = "claude-sonnet-4-6",
        response_format: Optional[dict] = None,
    ) -> str:
        prompt, image_paths = self._prepare_content(content, response_format)
        return self._run_coro_sync(
            self._async_call(prompt, image_paths, model)
        )

    async def _async_call(
        self,
        prompt: str,
        image_paths: tuple[str, ...],
        model: str,
    ) -> str:
        # Context-aware pre-turn compact. The CLI doesn't tell us its
        # window until after the first turn, so this is a no-op on call 1.
        if self._should_compact():
            await self._compact_turn(model)

        return await self._drain_turn(
            prompt, image_paths=image_paths, model=model,
        )

    async def _drain_turn(
        self,
        prompt: str,
        *,
        image_paths: tuple[str, ...],
        model: str,
    ) -> str:
        """Run one turn and return the concatenated assistant text."""
        text_chunks: list[str] = []
        # Per-turn: when a compact_boundary lands, its ``post_tokens`` is
        # the authoritative post-compact session size. The Usage that
        # follows reports the compact's own consumption (often near-zero
        # or a cumulative number that no longer describes the live
        # context), so we skip the usage-driven context-size update on
        # turns that compacted.
        compacted_this_turn = False
        async for ev in self._runner.run(
            prompt,
            model_id=model,
            image_paths=image_paths,
            thinking_level=self.thinking_level,
        ):
            compacted_this_turn = self._handle_event(
                ev, text_chunks, compacted_this_turn
            )
            if isinstance(ev, Error):
                raise RuntimeError(f"Claude Code CLI error: {ev.message}")
        return "".join(text_chunks)

    def _handle_event(
        self, ev, text_chunks: list[str], compacted_this_turn: bool,
    ) -> bool:
        if isinstance(ev, TextDelta):
            text_chunks.append(ev.text)
            self._emit_stream_event({
                "type": "text",
                "elapsed": ev.elapsed_ms / 1000.0,
                "text": ev.text[:200],
            })
        elif isinstance(ev, ToolCall):
            self._emit_stream_event({
                "type": "tool_use",
                "elapsed": ev.elapsed_ms / 1000.0,
                "tool": ev.name,
                "input": str(ev.input)[:100],
            })
        elif isinstance(ev, ToolResult):
            # Forward a redacted preview so the CLI can render the call's
            # output under its tool row. Capping at 2 KB avoids flooding
            # the stream when something dumps a megabyte of bytes.
            try:
                content = getattr(ev, "content", None) or getattr(ev, "output", None) or ""
                preview = str(content)[:2000]
                self._emit_stream_event({
                    "type": "tool_result",
                    "elapsed": getattr(ev, "elapsed_ms", 0) / 1000.0,
                    "tool": getattr(ev, "name", "") or getattr(ev, "tool_name", ""),
                    "is_error": bool(getattr(ev, "is_error", False)),
                    "result": preview,
                })
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(ev, SessionInfo):
            # Record the primary model id for UI display. ``session_id``
            # is informational — we don't resume by it (session_mode="none").
            if ev.model_id:
                self._resolved_model_id = ev.model_id
        elif isinstance(ev, Usage):
            self.last_usage = {
                "input_tokens": ev.input_tokens,
                "output_tokens": ev.output_tokens,
                "cache_read": ev.cache_read,
                "cache_create": ev.cache_create,
            }
            if ev.context_window:
                self._context_window_tokens = ev.context_window
            # ``turn_input_tokens`` is the authoritative per-turn size;
            # ``Usage.input_tokens`` is already normalized to total. On a
            # compacted turn the compact_boundary already set the right
            # value, so don't clobber it here.
            if not compacted_this_turn:
                self._last_context_tokens = (
                    ev.turn_input_tokens or ev.input_tokens
                )
        elif isinstance(ev, CompactBoundary):
            if ev.post_tokens is not None:
                self._last_context_tokens = int(ev.post_tokens)
            compacted_this_turn = True
        elif isinstance(ev, Done):
            pass
        return compacted_this_turn

    def _emit_stream_event(self, payload: dict) -> None:
        if self.on_stream is None:
            return
        try:
            self.on_stream(payload)
        except Exception:  # noqa: BLE001 — streaming callback must not break the turn
            pass

    # --- content prep --------------------------------------------------

    def _prepare_content(
        self,
        content: list[dict],
        response_format: Optional[dict],
    ) -> tuple[str, tuple[str, ...]]:
        """Filter unsupported blocks and split into (prompt_text, image_paths).

        The plugin's ``build_turn_envelope`` hook takes a plain prompt
        string plus image paths and produces the stream-json envelope.
        Anything that isn't text or image is dropped with a warning —
        the legacy runtime did the same.
        """
        text_parts: list[str] = []
        image_paths: list[str] = []
        for block in content:
            btype = block.get("type", "text")
            if btype == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif btype == "image":
                path = block.get("path")
                if path:
                    image_paths.append(path)
                elif "data" in block:
                    # Legacy supported inline base64 blocks; the runner's
                    # envelope builder only knows paths, so stage the data
                    # to a temp file so we can reuse the same code path.
                    import tempfile
                    suffix = (
                        "." + (block.get("media_type", "image/png").split("/")[-1])
                    )
                    fd, tmp = tempfile.mkstemp(suffix=suffix)
                    import os as _os
                    import base64 as _b64
                    with _os.fdopen(fd, "wb") as f:
                        f.write(_b64.b64decode(block["data"]))
                    image_paths.append(tmp)
            elif btype == "audio":
                warnings.warn(
                    "ClaudeCodeRuntime does not support audio content blocks. "
                    "Audio block will be skipped. Use AnthropicRuntime API "
                    "directly for full multimodal support.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "video":
                warnings.warn(
                    "ClaudeCodeRuntime does not support video content blocks. "
                    "Video block will be skipped. Consider using GeminiRuntime "
                    "for video.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "file":
                warnings.warn(
                    "ClaudeCodeRuntime does not support file/PDF content blocks. "
                    "File block will be skipped. Use AnthropicRuntime API "
                    "directly for PDF support.",
                    UserWarning,
                    stacklevel=3,
                )
            elif "text" in block:  # legacy fallback: treat as text
                text_parts.append(block["text"])

        prompt = "\n".join(text_parts)
        if response_format is not None:
            prompt += (
                "\n\nRespond with ONLY valid JSON matching: "
                + json.dumps(response_format)
            )
        return prompt, tuple(image_paths)

    # --- context-aware compact -----------------------------------------

    def _compact_threshold_tokens(self) -> Optional[int]:
        """Token count that triggers ``/compact``.

        Returns ``None`` until the CLI has reported its context window
        (first turn hasn't run yet). Below 500K windows use the ratio;
        above that, cap in absolute tokens so a long tail of cache reads
        can't defer compacting indefinitely.
        """
        w = self._context_window_tokens
        if w is None:
            return None
        if w <= self._compact_cap_tokens:
            return int(w * self._compact_ratio)
        return self._compact_cap_tokens

    def _should_compact(self) -> bool:
        thr = self._compact_threshold_tokens()
        if thr is None:
            return False
        return self._last_context_tokens >= thr

    async def _compact_turn(self, model: str) -> None:
        """Send ``/compact`` to the CLI and drain the response.

        Claude Code expects the trigger wrapped as a stream-json user
        message (the same envelope the plugin builds for normal turns),
        so this just runs ``/compact`` as a one-turn prompt rather than
        going through ``CliRunner.compact()``'s raw-line path.
        """
        try:
            await self._drain_turn("/compact", image_paths=(), model=model)
        except Exception:  # noqa: BLE001 — best-effort; fall through to the real turn
            pass

    def compact(self, threshold_tokens: Optional[int] = None) -> bool:
        """Explicit compact trigger — callable from program code.

        Returns True if compact ran, False if skipped due to threshold.
        """
        if (
            threshold_tokens is not None
            and self._last_context_tokens < threshold_tokens
        ):
            return False
        self._run_coro_sync(self._compact_turn(self.model))
        return True

    # --- process restart -----------------------------------------------

    def _restart_process(self) -> None:
        """Force the live CLI subprocess to exit so the next turn respawns it.

        Needed when an argv-level setting changes between turns (e.g.
        ``thinking_level`` → ``--settings defaultEffortLevel``), because
        those flags are only applied at process spawn. Cheap — ``_teardown_live``
        is a no-op when no live proc exists.
        """
        if self._closed or self._loop.is_closed():
            return
        self._run_coro_sync(self._runner._teardown_live())

    # --- lifecycle -----------------------------------------------------

    def close(self):
        """Kill the persistent CLI and stop the runtime's event loop.

        Idempotent — calling twice (e.g. explicit close + __del__) is
        harmless. The event loop only stops once.
        """
        if self._closed:
            return
        if not self._loop.is_closed():
            try:
                self._run_coro_sync(self._runner.close())
            except Exception:  # noqa: BLE001
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
        super().close()


__all__ = ["ClaudeCodeRuntime"]
