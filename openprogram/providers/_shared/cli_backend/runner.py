"""CliRunner — one shared subprocess runner for every CLI backend.

1b: minimal non-live-session path.

- Builds argv from ``CliBackendConfig`` + per-call overrides
  (model_id, system_prompt, image_paths, session resume)
- Spawns a fresh subprocess per call
- Reads stdout via the parser picked by ``parsers.parser_for(config)``
- Yields ``CliEvent`` values, then ``Done`` (or ``Error``) once the
  process exits

Not yet implemented (later sub-phases):

- Session-id capture + resume_args injection (1c)
- Watchdog timeouts with fresh vs resume timings (1d)
- Live-session (``live_session="claude-stdio"``) long-running mode (1e)
- ``prepare_execution`` async hook + ``cleanup`` (wire-up only so far)
- ``text_transforms`` input/output rewrites
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import AsyncIterator, Iterable, Optional

from .config import CliBackendConfig
from .events import CliEvent, Done, Error
from .parsers import LineParser, parser_for
from .plugin import (
    CliBackendPlugin,
    PreparedExecution,
    PrepareExecutionContext,
)


class CliRunner:
    """Generic subprocess runner driven by a ``CliBackendPlugin``.

    Each ``run()`` call spawns a fresh CLI process (1b). Later phases
    add live-session reuse, watchdog, session resume, etc.
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
        # Session id captured from the previous run — 1c fills this.
        self._session_id: Optional[str] = None
        self._live_proc: Optional[asyncio.subprocess.Process] = None
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
        auth_profile_id: Optional[str] = None,
    ) -> AsyncIterator[CliEvent]:
        """Run one turn against the CLI and yield events.

        ``resume=True`` is accepted but ignored in 1b — session resume
        lands in 1c. Pass it anyway; the signature is stable.
        """
        cfg = self._config
        call_start = time.monotonic()

        # Let the plugin stage any pre-run env / cleanup (async or sync).
        prepared: Optional[PreparedExecution] = None
        if self.plugin.prepare_execution is not None:
            ctx = PrepareExecutionContext(
                workspace_dir=self.workspace_dir,
                provider=self.plugin.id,
                model_id=model_id,
                auth_profile_id=auth_profile_id,
            )
            maybe = self.plugin.prepare_execution(ctx)
            if inspect.isawaitable(maybe):
                prepared = await maybe
            else:
                prepared = maybe  # type: ignore[assignment]

        argv = self._build_argv(
            prompt=prompt,
            model_id=model_id,
            system_prompt=system_prompt,
            image_paths=tuple(image_paths),
            resume=resume,
        )
        env = self._build_env(prepared)

        parser: LineParser = parser_for(cfg)

        # Spawn.
        try:
            # stdin for ``input="stdin"``; discard for ``input="arg"``.
            stdin_mode: int | None = (
                asyncio.subprocess.PIPE if cfg.input == "stdin" else asyncio.subprocess.DEVNULL
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin_mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
                env=env,
            )
        except FileNotFoundError as e:
            yield Error(
                message=f"CLI not found: {argv[0]}",
                recoverable=False,
                kind="FileNotFoundError",
            )
            await self._run_cleanup(prepared)
            return
        except OSError as e:
            yield Error(message=str(e), recoverable=False, kind=type(e).__name__)
            await self._run_cleanup(prepared)
            return

        # Feed stdin if needed.
        if cfg.input == "stdin" and proc.stdin is not None:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

        # Read stdout.
        assert proc.stdout is not None
        try:
            if cfg.output == "jsonl":
                async for line_bytes in proc.stdout:
                    line = line_bytes.decode("utf-8", errors="replace")
                    for ev in parser(line, call_start):
                        yield ev
            elif cfg.output == "text":
                async for line_bytes in proc.stdout:
                    line = line_bytes.decode("utf-8", errors="replace")
                    for ev in parser(line, call_start):
                        yield ev
            elif cfg.output == "json":
                blob = (await proc.stdout.read()).decode("utf-8", errors="replace")
                for ev in parser(blob, call_start):
                    yield ev
            else:  # defensive — unknown format treated as text
                async for line_bytes in proc.stdout:
                    line = line_bytes.decode("utf-8", errors="replace")
                    for ev in parser(line, call_start):
                        yield ev
        except asyncio.CancelledError:
            proc.kill()
            await self._run_cleanup(prepared)
            raise

        # Wait for exit + collect stderr.
        returncode = await proc.wait()
        stderr_bytes = b""
        if proc.stderr is not None:
            try:
                stderr_bytes = await proc.stderr.read()
            except Exception:  # noqa: BLE001 — tail read is best-effort
                pass

        duration_ms = int((time.monotonic() - call_start) * 1000)

        if returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            yield Error(
                message=stderr_text or f"CLI exited with code {returncode}",
                recoverable=False,
                kind=f"ExitCode({returncode})",
            )
        else:
            yield Done(duration_ms=duration_ms, num_turns=1)

        await self._run_cleanup(prepared)

    async def close(self) -> None:
        """Tear down any long-running live-session process.

        1b: no-op (no live sessions yet). 1e fills this in.
        """
        return None

    def bump_auth_epoch(self) -> None:
        """Invalidate current live process / resume state."""
        self._auth_epoch += 1

    # --- internals ----------------------------------------------------

    def _build_argv(
        self,
        *,
        prompt: str,
        model_id: str,
        system_prompt: Optional[str],
        image_paths: tuple[str, ...],
        resume: bool,
    ) -> list[str]:
        cfg = self._config
        argv: list[str] = [cfg.command]

        # ``session_args`` always apply when session mode is active.
        if cfg.session_args and cfg.session_mode != "none" and self._session_id:
            argv.extend(self._fill_session(cfg.session_args))

        # ``resume_args`` apply only on resume runs.
        if resume and cfg.resume_args and self._session_id:
            argv.extend(self._fill_session(cfg.resume_args))

        # Model.
        cli_model = (cfg.model_aliases or {}).get(model_id, model_id)
        if cfg.model_arg and cli_model:
            argv.extend([cfg.model_arg, cli_model])

        # Session id as a standalone arg (``session_arg``, like ``--session-id``).
        if cfg.session_arg and cfg.session_mode == "always":
            sid = self._session_id
            if sid is None:
                # 1c will persist+rotate; for 1b we generate a fresh uuid so
                # the CLI has something stable to key its own state on.
                import uuid
                sid = uuid.uuid4().hex
                self._session_id = sid
            argv.extend([cfg.session_arg, sid])

        # System prompt (free-form text flag — ``system_prompt_arg``).
        if system_prompt and cfg.system_prompt_arg:
            argv.extend([cfg.system_prompt_arg, system_prompt])

        # Images.
        if image_paths and cfg.image_arg:
            if cfg.image_mode == "repeat":
                for p in image_paths:
                    argv.extend([cfg.image_arg, p])
            else:  # "list"
                argv.extend([cfg.image_arg, ",".join(image_paths)])

        # Base args from config come AFTER model/session/system so that
        # the backend author can anchor them at the tail if needed.
        if cfg.args:
            argv.extend(cfg.args)

        # Prompt as a positional arg when input=arg.
        if cfg.input == "arg":
            # Auto-switch to stdin above ``max_prompt_arg_chars`` —
            # caller sees this transparently (we just don't append the arg).
            if cfg.max_prompt_arg_chars is None or len(prompt) <= cfg.max_prompt_arg_chars:
                argv.append(prompt)

        return argv

    def _fill_session(self, args: tuple[str, ...]) -> list[str]:
        sid = self._session_id or ""
        return [a.replace("{sessionId}", sid) for a in args]

    def _build_env(self, prepared: Optional[PreparedExecution]) -> dict[str, str]:
        cfg = self._config
        env = dict(os.environ)
        for key in cfg.clear_env or ():
            env.pop(key, None)
        if cfg.env:
            env.update(cfg.env)
        if prepared is not None:
            for key in prepared.clear_env or ():
                env.pop(key, None)
            if prepared.env:
                env.update(prepared.env)
        return env

    async def _run_cleanup(self, prepared: Optional[PreparedExecution]) -> None:
        if prepared is None or prepared.cleanup is None:
            return
        try:
            maybe = prepared.cleanup()
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass


__all__ = [
    "CliRunner",
    "PreparedExecution",
    "PrepareExecutionContext",
]
