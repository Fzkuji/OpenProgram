"""CliRunner — one shared subprocess runner for every CLI backend.

Progress:

- 1a: ``CliEvent`` union
- 1b: minimal one-shot subprocess path, argv/env builder, parser dispatch
- 1c: session-id capture + disk persistence + resume
- 1d: watchdog (no-output stall → kill + recoverable Error)
- 1e: live-session mode (``live_session="claude-stdio"``)
- 1f-α: live-session policy (turn-count respawn, ``compact()``), plugin
  hooks for envelope + thinking-level argv
- 1f-β: ``text_transforms.input`` rewrites prompt + system prompt + live
  envelope; ``text_transforms.output`` rewrites streamed ``TextDelta``
  text (tool metadata is left untouched)

Still TODO:

- Provider-side ``CliBackendPlugin`` for Anthropic
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional

from .config import CliBackendConfig
from .events import CliEvent, Done, Error, SessionInfo, TextDelta, Usage
from .parsers import LineParser, parser_for
from .plugin import (
    CliBackendPlugin,
    PluginTextReplacement,
    PreparedExecution,
    PrepareExecutionContext,
)
from .watchdog import (
    CLI_FRESH_WATCHDOG_DEFAULTS,
    CLI_RESUME_WATCHDOG_DEFAULTS,
    CLI_WATCHDOG_MIN_TIMEOUT_MS,
    WatchdogTiming,
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
        session_state_path: Optional[str] = None,
    ) -> None:
        self.plugin = plugin
        self.workspace_dir = workspace_dir
        self.overall_timeout_ms = overall_timeout_ms
        self._config: CliBackendConfig = plugin.config
        self._live_proc: Optional[asyncio.subprocess.Process] = None
        self._live_prepared: Optional[PreparedExecution] = None
        self._live_turn_count: int = 0
        self._auth_epoch: int = 0
        # Session id persisted across ``run()`` calls. Stored on disk so
        # restarts resume into the CLI's own session instead of orphaning it.
        self._session_state_path: Path = Path(
            session_state_path
            or str(Path(workspace_dir) / ".openprogram" / "cli_session.json")
        )
        self._session_id: Optional[str] = self._load_session_id()

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
        thinking_level: Optional[str] = None,
    ) -> AsyncIterator[CliEvent]:
        """Run one turn against the CLI and yield events.

        Two modes, chosen by ``cfg.live_session``:

        - ``None`` (default) — one-shot: spawn fresh, pipe prompt in,
          parse stdout until exit, yield Done/Error from exit code.
        - ``"claude-stdio"`` — persistent: spawn once, keep alive across
          turns. Each call writes a stream-json ``user`` message to
          stdin and reads events until the ``result`` terminator arrives.
        """
        cfg = self._config
        if cfg.live_session is not None:
            async for ev in self._run_live(
                prompt=prompt,
                model_id=model_id,
                system_prompt=system_prompt,
                image_paths=tuple(image_paths),
                resume=resume,
                auth_profile_id=auth_profile_id,
                thinking_level=thinking_level,
            ):
                yield ev
            return

        async for ev in self._run_oneshot(
            prompt=prompt,
            model_id=model_id,
            system_prompt=system_prompt,
            image_paths=tuple(image_paths),
            resume=resume,
            auth_profile_id=auth_profile_id,
            thinking_level=thinking_level,
        ):
            yield ev

    async def _run_oneshot(
        self,
        *,
        prompt: str,
        model_id: str,
        system_prompt: Optional[str],
        image_paths: tuple[str, ...],
        resume: bool,
        auth_profile_id: Optional[str],
        thinking_level: Optional[str],
    ) -> AsyncIterator[CliEvent]:
        cfg = self._config
        call_start = time.monotonic()
        prompt = self._apply_input_transforms(prompt)
        system_prompt = self._apply_input_transforms(system_prompt)

        prepared = await self._call_prepare_execution(model_id, auth_profile_id)

        argv = self._build_argv(
            prompt=prompt,
            model_id=model_id,
            system_prompt=system_prompt,
            image_paths=image_paths,
            resume=resume,
            thinking_level=thinking_level,
        )
        env = self._build_env(prepared)

        try:
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
                start_new_session=True,
            )
        except FileNotFoundError:
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

        if cfg.input == "stdin" and proc.stdin is not None:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

        stalled = False
        try:
            async for ev, reason in self._stream_events(
                proc, call_start=call_start, resume=resume,
                turn_terminator=None,
            ):
                if reason == "stall":
                    stalled = True
                    break
                if reason == "eof":
                    break
                yield ev
        except asyncio.CancelledError:
            self._kill_tree(proc)
            await proc.wait()
            await self._run_cleanup(prepared)
            raise

        if stalled:
            self._kill_tree(proc)
            await proc.wait()
            yield Error(
                message=f"CLI produced no output for {self._compute_watchdog_ms(resume=resume)}ms",
                recoverable=True,
                kind="WatchdogStall",
            )
            await self._run_cleanup(prepared)
            return

        returncode = await proc.wait()
        stderr_bytes = b""
        if proc.stderr is not None:
            try:
                stderr_bytes = await proc.stderr.read()
            except Exception:  # noqa: BLE001
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

    async def _run_live(
        self,
        *,
        prompt: str,
        model_id: str,
        system_prompt: Optional[str],
        image_paths: tuple[str, ...],
        resume: bool,
        auth_profile_id: Optional[str],
        thinking_level: Optional[str],
    ) -> AsyncIterator[CliEvent]:
        """Persistent-process mode. Reuses ``self._live_proc`` across calls.

        The CLI stays resident; each call writes one stream-json ``user``
        message on stdin and reads events until a ``result`` message is
        seen. ``Done`` is synthesized at that boundary — the process
        stays alive waiting for the next prompt.

        Respawns when ``cfg.max_turns_per_process`` is reached, so
        long-running sessions can't accumulate unbounded CLI-side state.
        """
        cfg = self._config
        call_start = time.monotonic()
        prompt = self._apply_input_transforms(prompt)
        system_prompt = self._apply_input_transforms(system_prompt)

        # Respawn when turn limit hit (before reading ``_live_proc`` below).
        if (
            cfg.max_turns_per_process is not None
            and self._live_turn_count >= cfg.max_turns_per_process
        ):
            await self._teardown_live()

        # Spawn on first use (or after close / auth bump).
        if self._live_proc is None or self._live_proc.returncode is not None:
            prepared = await self._call_prepare_execution(model_id, auth_profile_id)
            # For live mode, session/resume args are baked into the spawn;
            # later turns reuse the same process regardless of ``resume``.
            argv = self._build_argv(
                prompt="",  # prompt arrives via stdin per-turn
                model_id=model_id,
                system_prompt=system_prompt,
                image_paths=image_paths,
                resume=resume,
                thinking_level=thinking_level,
            )
            env = self._build_env(prepared)
            try:
                self._live_proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workspace_dir,
                    env=env,
                    start_new_session=True,
                )
            except FileNotFoundError:
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
            self._live_prepared = prepared
            self._live_turn_count = 0

        proc = self._live_proc

        # Write the turn's prompt as a stream-json ``user`` envelope.
        if proc.stdin is not None:
            envelope = self._build_live_prompt_envelope(prompt, image_paths)
            try:
                proc.stdin.write(envelope.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                # Process died under us — surface as recoverable so caller
                # can retry with a fresh spawn.
                await self._teardown_live()
                yield Error(
                    message="Live CLI process exited unexpectedly",
                    recoverable=True,
                    kind="LiveProcessGone",
                )
                return

        # Read events until the turn terminator (``result`` message).
        stalled = False
        try:
            async for ev, reason in self._stream_events(
                proc, call_start=call_start, resume=resume,
                turn_terminator="result",
            ):
                if reason == "stall":
                    stalled = True
                    break
                if reason == "eof":
                    # Live CLI closed stdout mid-turn — treat as recoverable.
                    await self._teardown_live()
                    yield Error(
                        message="Live CLI closed stdout unexpectedly",
                        recoverable=True,
                        kind="LiveProcessGone",
                    )
                    return
                yield ev
                if reason == "terminator":
                    break
        except asyncio.CancelledError:
            await self._teardown_live()
            raise

        if stalled:
            await self._teardown_live()
            yield Error(
                message=f"CLI produced no output for {self._compute_watchdog_ms(resume=resume)}ms",
                recoverable=True,
                kind="WatchdogStall",
            )
            return

        # Turn completed cleanly — count it and yield Done.
        self._live_turn_count += 1
        duration_ms = int((time.monotonic() - call_start) * 1000)
        yield Done(duration_ms=duration_ms, num_turns=1)

    def _build_live_prompt_envelope(
        self, prompt: str, image_paths: tuple[str, ...]
    ) -> str:
        """stream-json envelope for a user prompt in live-session mode.

        Plugin owns the shape because content-block layouts differ across
        CLIs (Claude inlines base64 images, Gemini CLI doesn't support
        images via stdin at all). Falls back to a text-only envelope when
        the plugin provides no builder.

        Returned string is written verbatim to stdin, so the builder is
        responsible for the trailing newline.
        """
        hook = self.plugin.build_turn_envelope
        if hook is not None:
            return hook(prompt, image_paths)
        return json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }) + "\n"

    async def _stream_events(
        self,
        proc: asyncio.subprocess.Process,
        *,
        call_start: float,
        resume: bool,
        turn_terminator: Optional[str],
    ) -> AsyncIterator[tuple[Optional[CliEvent], str]]:
        """Read parser events from ``proc.stdout``.

        Yields ``(event, reason)`` pairs. ``reason`` is one of:

        - ``"event"`` — normal event from the parser
        - ``"stall"`` — watchdog fired; event is None, caller handles
        - ``"terminator"`` — last event of the turn; emitted with the
          event itself so caller can forward it before stopping
        - ``"eof"`` — stdout closed; event is None

        ``turn_terminator`` is the parser-level event kind whose arrival
        ends the turn (used only in live-session mode — one-shot mode
        passes ``None`` and stops on EOF).
        """
        cfg = self._config
        parser: LineParser = parser_for(cfg)
        watchdog_ms = self._compute_watchdog_ms(resume=resume)
        assert proc.stdout is not None

        if cfg.output == "json":
            try:
                blob_bytes = await asyncio.wait_for(
                    proc.stdout.read(), timeout=watchdog_ms / 1000
                )
            except asyncio.TimeoutError:
                yield None, "stall"
                return
            blob = blob_bytes.decode("utf-8", errors="replace")
            for ev in parser(blob, call_start):
                self._capture_session(ev)
                yield self._apply_output_transforms(ev), "event"
            yield None, "eof"
            return

        while True:
            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=watchdog_ms / 1000
                )
            except asyncio.TimeoutError:
                yield None, "stall"
                return
            if not line_bytes:
                yield None, "eof"
                return
            line = line_bytes.decode("utf-8", errors="replace")
            for ev in parser(line, call_start):
                self._capture_session(ev)
                transformed = self._apply_output_transforms(ev)
                # Detect turn boundary. ``Usage`` is what claude-stream-json
                # emits for the ``result`` message — see parsers.py.
                is_terminator = (
                    turn_terminator == "result" and isinstance(ev, Usage)
                )
                yield transformed, ("terminator" if is_terminator else "event")
                if is_terminator:
                    return

    async def _call_prepare_execution(
        self, model_id: str, auth_profile_id: Optional[str]
    ) -> Optional[PreparedExecution]:
        if self.plugin.prepare_execution is None:
            return None
        ctx = PrepareExecutionContext(
            workspace_dir=self.workspace_dir,
            provider=self.plugin.id,
            model_id=model_id,
            auth_profile_id=auth_profile_id,
        )
        maybe = self.plugin.prepare_execution(ctx)
        if inspect.isawaitable(maybe):
            return await maybe
        return maybe  # type: ignore[return-value]

    async def _teardown_live(self) -> None:
        proc = self._live_proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            self._kill_tree(proc)
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
        self._live_proc = None
        self._live_turn_count = 0
        prepared = getattr(self, "_live_prepared", None)
        self._live_prepared = None
        if prepared is not None:
            await self._run_cleanup(prepared)

    async def close(self) -> None:
        """Tear down any long-running live-session process."""
        await self._teardown_live()

    async def compact(self) -> bool:
        """Ask the live-session CLI to compact its own context.

        Writes ``cfg.compact_command`` (e.g. ``"/compact"``) as a
        plain-text line to the live process's stdin. Returns True if
        sent, False if disabled or no live process is running.

        Compaction itself happens inside the CLI — the runner just
        delivers the trigger. The next ``run()`` turn should see a
        ``CompactBoundary`` event in the stream.
        """
        cfg = self._config
        proc = self._live_proc
        if (
            cfg.compact_command is None
            or proc is None
            or proc.returncode is not None
            or proc.stdin is None
        ):
            return False
        line = cfg.compact_command
        if not line.endswith("\n"):
            line += "\n"
        try:
            proc.stdin.write(line.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            await self._teardown_live()
            return False
        return True

    def bump_auth_epoch(self) -> None:
        """Invalidate current live process + persisted session id.

        Auth changed — the CLI's existing session keyed off the old
        credentials is useless, so we drop it rather than resume into it.
        The live process (if any) is marked stale by clearing the
        reference; the next ``run()`` respawns. Actual teardown is fire-
        and-forget so this stays sync.
        """
        self._auth_epoch += 1
        self._session_id = None
        self._save_session_id(None)
        proc = self._live_proc
        if proc is not None and proc.returncode is None:
            self._kill_tree(proc)
        self._live_proc = None
        self._live_prepared = None
        self._live_turn_count = 0

    # --- session persistence -----------------------------------------

    def _session_key(self) -> str:
        """Key under which this runner's session id is stored on disk.

        Keyed by plugin id so two backends sharing a workspace don't clobber
        each other. Auth-profile scoping is orthogonal — callers that care
        pass a distinct ``session_state_path`` per profile.
        """
        return self.plugin.id

    def _load_session_id(self) -> Optional[str]:
        try:
            raw = self._session_state_path.read_text()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(blob, dict):
            return None
        sid = blob.get(self._session_key())
        return sid if isinstance(sid, str) and sid else None

    def _save_session_id(self, sid: Optional[str]) -> None:
        try:
            self._session_state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        blob: dict[str, str] = {}
        if self._session_state_path.exists():
            try:
                loaded = json.loads(self._session_state_path.read_text())
                if isinstance(loaded, dict):
                    blob = {k: v for k, v in loaded.items() if isinstance(v, str)}
            except (OSError, json.JSONDecodeError):
                blob = {}
        key = self._session_key()
        if sid is None:
            blob.pop(key, None)
        else:
            blob[key] = sid
        try:
            self._session_state_path.write_text(json.dumps(blob, indent=2))
        except OSError:
            pass

    def _capture_session(self, ev: CliEvent) -> None:
        """If the event carries a session id, persist it for future resumes."""
        if isinstance(ev, SessionInfo) and ev.session_id:
            if ev.session_id != self._session_id:
                self._session_id = ev.session_id
                self._save_session_id(ev.session_id)

    # --- subprocess helpers ------------------------------------------

    @staticmethod
    def _kill_tree(proc: asyncio.subprocess.Process) -> None:
        """SIGKILL the whole process group — kills shell wrappers' children too."""
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    # --- watchdog -----------------------------------------------------

    def _compute_watchdog_ms(self, *, resume: bool) -> int:
        """Resolve fresh/resume watchdog to an absolute millisecond budget.

        Priority: backend override in ``cfg.reliability`` > built-in defaults.
        Result is clamped to ``[CLI_WATCHDOG_MIN_TIMEOUT_MS, overall_timeout_ms]``
        so a pathological config can't disable the watchdog entirely.
        """
        timing: WatchdogTiming
        defaults = (
            CLI_RESUME_WATCHDOG_DEFAULTS if resume else CLI_FRESH_WATCHDOG_DEFAULTS
        )
        override: Optional[WatchdogTiming] = None
        rel = self._config.reliability
        if rel is not None and rel.watchdog is not None:
            override = rel.watchdog.resume if resume else rel.watchdog.fresh
        timing = override or defaults

        if timing.no_output_timeout_ms is not None:
            budget = timing.no_output_timeout_ms
        elif timing.no_output_timeout_ratio is not None:
            budget = int(self.overall_timeout_ms * timing.no_output_timeout_ratio)
        else:
            budget = self.overall_timeout_ms

        if timing.min_ms is not None:
            budget = max(budget, timing.min_ms)
        if timing.max_ms is not None:
            budget = min(budget, timing.max_ms)

        budget = max(budget, CLI_WATCHDOG_MIN_TIMEOUT_MS)
        budget = min(budget, self.overall_timeout_ms)
        return budget

    # --- internals ----------------------------------------------------

    def _build_argv(
        self,
        *,
        prompt: str,
        model_id: str,
        system_prompt: Optional[str],
        image_paths: tuple[str, ...],
        resume: bool,
        thinking_level: Optional[str] = None,
    ) -> list[str]:
        cfg = self._config
        argv: list[str] = [cfg.command]

        # Resume takes priority when requested and we have a prior session.
        # ``resume_args`` and ``session_args`` are mutually exclusive on one
        # call — resume_args exists precisely because some CLIs use a
        # different flag to resume vs. start fresh (``--resume <id>`` vs.
        # ``--session-id <id>``).
        apply_session_args = (
            cfg.session_args and cfg.session_mode != "none" and self._session_id
        )
        apply_resume_args = (
            resume and cfg.resume_args and self._session_id
        )
        if apply_resume_args:
            argv.extend(self._fill_session(cfg.resume_args or ()))
        elif apply_session_args:
            argv.extend(self._fill_session(cfg.session_args or ()))

        # Model.
        cli_model = (cfg.model_aliases or {}).get(model_id, model_id)
        if cfg.model_arg and cli_model:
            argv.extend([cfg.model_arg, cli_model])

        # Session id as a standalone arg (``session_arg``, like ``--session-id``).
        # If we don't have one yet, pre-mint a uuid the CLI will echo back via
        # its first "system" message — that's how we seed resume persistence.
        if cfg.session_arg and cfg.session_mode == "always" and not apply_resume_args:
            sid = self._session_id
            if sid is None:
                import uuid
                sid = uuid.uuid4().hex
                self._session_id = sid
                self._save_session_id(sid)
            argv.extend([cfg.session_arg, sid])

        # Thinking level. Plugin decides how to encode it (flag name, value
        # format, or a JSON blob like Claude's ``--settings``). Hook is
        # consulted only when a non-"off" level was explicitly requested.
        if (
            thinking_level
            and thinking_level != "off"
            and self.plugin.build_thinking_args is not None
        ):
            thinking_args = self.plugin.build_thinking_args(thinking_level)
            if thinking_args:
                argv.extend(thinking_args)

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

    def _apply_input_transforms(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return self._apply_text_replacements(
            text,
            getattr(self.plugin.text_transforms, "input", ()) or (),
        )

    def _apply_output_transforms(self, event: CliEvent) -> CliEvent:
        if not isinstance(event, TextDelta):
            return event
        transformed = self._apply_text_replacements(
            event.text,
            getattr(self.plugin.text_transforms, "output", ()) or (),
        )
        if transformed == event.text:
            return event
        return replace(event, text=transformed)

    @staticmethod
    def _apply_text_replacements(
        text: str, replacements: Iterable[PluginTextReplacement]
    ) -> str:
        result = text
        for replacement in replacements:
            pattern = replacement.from_
            if isinstance(pattern, str):
                result = result.replace(pattern, replacement.to)
            elif hasattr(pattern, "sub"):
                result = pattern.sub(replacement.to, result)
            else:
                raise TypeError(
                    "PluginTextReplacement.from_ must be str or compiled regex pattern"
                )
        return result

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
