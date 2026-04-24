"""
Codex CLI provider — routes LLM calls through the OpenAI Codex CLI.

Uses `codex exec` (non-interactive mode) with configurable sandbox,
approval policy, and optional web search.
No API key needed in the harness — Codex CLI uses its own auth.

Setup (subscription mode — recommended):
    Using device auth lets Codex consume your ChatGPT subscription quota
    instead of burning API credits. Two steps:

    1. Enable device code auth in ChatGPT web settings:
       Log in to chatgpt.com → Settings → Security →
       Turn on "为 Codex 应用设备代码授权" (Enable device code authorization for Codex).

    2. Authenticate the CLI:
       $ codex login --device-auth
       This opens a browser for OAuth. Once approved, the token is stored
       in ~/.codex/auth.json and refreshes automatically.

    Note: If OPENAI_API_KEY is set in your environment, Codex CLI will
    prioritise it over device auth and consume API credits. This provider
    automatically strips OPENAI_API_KEY from the subprocess environment
    so that device auth (subscription) is always used.

Setup (API key mode — alternative):
    $ export OPENAI_API_KEY=sk-...
    $ codex login --with-api-key
    This uses your OpenAI API quota directly. Not recommended if you have
    a ChatGPT subscription.

Supports:
- Text content blocks
- Image content blocks (via -i flag for file paths, temp files for base64)
- Session continuity (via `codex exec resume <session_id>`)

Unsupported (with warnings):
- Audio content blocks (Codex CLI does not support audio input)
- Video content blocks (Codex CLI does not support video input)
- File/PDF content blocks (Codex CLI does not support document input)

Usage:
    from openprogram.providers.codex import CodexRuntime

    runtime = CodexRuntime(model="o4-mini")

    @agentic_function
    def observe(task):
        return runtime.exec(content=[
            {"type": "text", "text": f"Find: {task}"},
            {"type": "image", "path": "screenshot.png"},
        ])
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class CodexRuntime(Runtime):
    """
    Runtime that routes LLM calls through the OpenAI Codex CLI.

    Requires `codex` CLI to be installed and authenticated.
    Uses Codex CLI's own auth (no separate API key needed in harness).

    Supports images via -i flag (file paths) or temp files (base64 data).
    Supports session continuity via `codex exec resume`.

    Args:
        model:      Model to use (default: None = let CLI choose). Passed to --model flag.
        timeout:    Max seconds per CLI call (default: 300).
        cli_path:   Path to codex CLI binary (auto-detected if not specified).
        session_id: Session ID for continuity. "auto" = capture the CLI's
                    thread id after the first call. None = stateless.
        workdir:    Working directory for codex. None = current directory.
        sandbox:    Sandbox mode: "read-only", "workspace-write", or
                    "danger-full-access" (default: "workspace-write").
        full_auto:  Use --full-auto flag (default: True).
        approval_policy:
                    Root-level approval policy for non-interactive exec
                    (default: "never").
        search:     Enable Codex web search tool for live/current queries
                    (default: False).
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        timeout: int = 300,
        cli_path: str = None,
        session_id: str = "auto",
        workdir: str = None,
        sandbox: str = "workspace-write",
        full_auto: bool = True,
        approval_policy: Optional[str] = "never",
        search: bool = False,
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("codex")
        self.workdir = workdir
        self.sandbox = sandbox
        self.full_auto = full_auto
        self.approval_policy = approval_policy
        self.search = search
        self._turn_count = 0
        self._auto_session = session_id == "auto"

        self._session_id = None if self._auto_session else session_id

        # Once a real Codex thread id is known, the CLI manages context.
        self.has_session = self._session_id is not None

        # Always track the last thread_id for external use (e.g. modify/resume)
        # even when session_id=None (stateless mode). Not used for _call resume.
        self.last_thread_id = None

        # turn.completed reports session-cumulative usage (total_token_usage),
        # not per-call usage. Track the cumulative baseline so we can diff.
        self._session_cumulative = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

        # Live handle to the current codex subprocess (or None).
        # Exposed so webui's kill_active_runtime can terminate mid-call.
        self._proc: Optional[subprocess.Popen] = None

        if self.cli_path is None:
            raise FileNotFoundError(
                "Codex CLI not found. Install: npm install -g @openai/codex"
            )

    def list_models(self) -> list[str]:
        """Auto-detect available models from Codex CLI's own model cache.

        Reads ~/.codex/models_cache.json which the CLI maintains.
        Returns models with visibility='list', sorted by priority.
        Falls back to a reasonable default if the cache doesn't exist.
        """
        # Read Codex CLI's own model cache
        cache_path = os.path.join(os.path.expanduser("~"), ".codex", "models_cache.json")
        try:
            with open(cache_path) as f:
                data = json.load(f)
            models = []
            for m in data.get("models", []):
                if m.get("visibility") == "list":
                    models.append((m.get("priority", 999), m["slug"]))
            models.sort()
            return [slug for _, slug in models]
        except Exception:
            pass

        # Fallback — codex-spark models are included in ChatGPT subscriptions
        return ["gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-mini"]

    def _call(self, content: list[dict], model: str = None, response_format: dict = None) -> str:
        """Call Codex CLI with the content list.

        Images are passed via -i flag (file paths). Base64 data is
        written to temp files first. URL images are skipped with a
        text note (Codex CLI only supports local files).

        Unsupported block types (audio, video, file) emit warnings and are skipped.
        """
        import warnings

        # Collect text parts and image paths
        text_parts = []
        image_paths = []
        temp_files = []

        # Handle plain string input
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        try:
            for block in content:
                btype = block.get("type", "text")

                if btype == "text":
                    text_parts.append(block["text"])

                elif btype == "image":
                    path = self._resolve_image(block, temp_files)
                    if path:
                        image_paths.append(path)
                    elif "url" in block:
                        # Codex CLI doesn't support URLs — add as text note
                        text_parts.append(f"[Image URL: {block['url']}]")

                elif btype == "audio":
                    warnings.warn(
                        "CodexRuntime does not support audio content blocks. "
                        "Audio block will be skipped. Consider using OpenAIRuntime API for audio support.",
                        UserWarning,
                        stacklevel=3,
                    )

                elif btype == "video":
                    warnings.warn(
                        "CodexRuntime does not support video content blocks. "
                        "Video block will be skipped. Consider using GeminiRuntime for video.",
                        UserWarning,
                        stacklevel=3,
                    )

                elif btype == "file":
                    warnings.warn(
                        "CodexRuntime does not support file/PDF content blocks. "
                        "File block will be skipped. Consider using OpenAIRuntime API for file support.",
                        UserWarning,
                        stacklevel=3,
                    )

                elif "text" in block:
                    text_parts.append(block["text"])

            prompt = "\n".join(text_parts)
            if response_format:
                prompt += f"\n\nRespond with ONLY valid JSON matching this schema: {json.dumps(response_format)}"

            result = self._run_codex(prompt, image_paths, model)
            self._turn_count += 1
            return result

        finally:
            # Clean up temp files
            for tf in temp_files:
                try:
                    os.unlink(tf)
                except OSError:
                    pass

    def _resolve_image(self, block: dict, temp_files: list) -> Optional[str]:
        """Resolve an image block to a local file path.

        For file paths, returns the path directly.
        For base64 data, writes to a temp file and returns its path.
        For URLs, returns None (Codex CLI doesn't support URLs).
        """
        if "path" in block:
            return block["path"]

        if "data" in block:
            media_type = block.get("media_type", "image/png")
            ext = mimetypes.guess_extension(media_type) or ".png"
            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="codex_img_")
            try:
                os.write(fd, base64.b64decode(block["data"]))
            finally:
                os.close(fd)
            temp_files.append(tmp_path)
            return tmp_path

        return None

    def _run_codex(self, prompt: str, image_paths: list[str], model: str) -> str:
        """Build and run the codex exec command."""
        is_resume = bool(self._session_id and self._turn_count > 0)

        cmd = [self.cli_path]

        # Root-level flags (before "exec" subcommand)
        if self.search:
            cmd.append("--search")
        if self.approval_policy:
            cmd.extend(["-a", self.approval_policy])

        if is_resume:
            # `exec resume` has a limited set of flags
            cmd.extend(["exec", "resume", self._session_id])
            if model:
                cmd.extend(["--model", model])
            if self.full_auto:
                cmd.append("--full-auto")
            cmd.append("--skip-git-repo-check")
            # Image flags
            for img_path in image_paths:
                cmd.extend(["-i", img_path])
        else:
            cmd.append("exec")
            if model:
                cmd.extend(["--model", model])
            if self.full_auto:
                cmd.append("--full-auto")
            else:
                if self.sandbox:
                    cmd.extend(["--sandbox", self.sandbox])
            if self.workdir:
                cmd.extend(["--cd", self.workdir])
            cmd.append("--skip-git-repo-check")
            # Image flags
            for img_path in image_paths:
                cmd.extend(["-i", img_path])

        # Reasoning effort (via config override). Always pass if set — do NOT
        # skip on "medium"; codex's default is "xhigh" not "medium".
        effort = getattr(self, '_reasoning_effort', None)
        if effort:
            cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])

        # Output: capture last message to a temp file for reliable extraction
        fd, output_file = tempfile.mkstemp(suffix=".txt", prefix="codex_out_")
        os.close(fd)
        try:
            cmd.append("--json")
            cmd.extend(["-o", output_file])

            # Pass prompt: resume uses positional arg, exec uses stdin via "-"
            if is_resume:
                cmd.append(prompt)
            else:
                cmd.append("-")

            # Remove API keys so CLI uses device-auth (subscription) instead of API credits.
            # If OPENAI_API_KEY is present, Codex CLI prioritises it over device-auth,
            # which burns API quota even when the user has an active subscription.
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("GEMINI_API_KEY", None)
            env.pop("GOOGLE_API_KEY", None)

            import time as _time
            start_time = _time.time()

            # Popen (not subprocess.run) so we can stream stdout line-by-line
            # and expose self._proc for external kill via kill_active_runtime.
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if not is_resume else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # line-buffered
                    env=env,
                )
            except Exception as e:
                raise RuntimeError(f"Failed to start Codex CLI: {e}")

            self._proc = proc
            try:
                # Feed the prompt over stdin for non-resume calls, then close
                # the pipe so codex knows the input is complete.
                if not is_resume and proc.stdin is not None:
                    try:
                        proc.stdin.write(prompt)
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass

                stdout_lines: list[str] = []
                timed_out = False
                for line in proc.stdout:
                    # Enforce overall timeout between reads.
                    if _time.time() - start_time > self.timeout:
                        timed_out = True
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        break

                    stdout_lines.append(line)
                    stripped = line.strip()
                    if not stripped or not stripped.startswith("{"):
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue

                    elapsed = round(_time.time() - start_time, 1)
                    etype = event.get("type", "")

                    # Extract thread_id from session events
                    if etype in ("thread.started", "thread.resumed"):
                        thread_id = event.get("thread_id")
                        if thread_id:
                            self.last_thread_id = thread_id  # always track
                            # Only capture session ID once; don't overwrite
                            # on resume (CLI may return a new thread_id).
                            if self._auto_session and not self._session_id:
                                self._session_id = thread_id
                                self.has_session = True

                    streamed = self._stream_codex_event(event, elapsed)
                    if streamed and self.on_stream:
                        try:
                            self.on_stream(streamed)
                        except Exception:
                            pass

                # Drain stderr (may have diagnostics even on success)
                try:
                    stderr_output = proc.stderr.read() if proc.stderr else ""
                except Exception:
                    stderr_output = ""

                # Wait for final exit status
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)

                if timed_out:
                    raise TimeoutError(f"Codex CLI timed out after {self.timeout}s")

                if proc.returncode != 0:
                    # Build a pseudo-CompletedProcess-like namespace for
                    # _handle_error (it reads .returncode/.stdout/.stderr).
                    class _R:
                        pass
                    r = _R()
                    r.returncode = proc.returncode
                    r.stdout = "".join(stdout_lines)
                    r.stderr = stderr_output
                    self._handle_error(r)

                # Extract thread_id from stdout if session events didn't give it
                if not self.last_thread_id:
                    tid = self._extract_thread_id("".join(stdout_lines))
                    if tid:
                        self.last_thread_id = tid
                        if self._auto_session and not self._session_id:
                            self._session_id = tid
                            self.has_session = True

                # Read output from the -o file
                try:
                    with open(output_file, "r") as f:
                        result_text = f.read().strip()
                    if result_text:
                        return result_text
                except (FileNotFoundError, IOError):
                    pass

                # Fall back to stdout
                return "".join(stdout_lines).strip()

            finally:
                # Ensure pipes are closed even on unexpected error
                for _stream in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        if _stream is not None:
                            _stream.close()
                    except Exception:
                        pass
                self._proc = None

        finally:
            try:
                os.unlink(output_file)
            except OSError:
                pass

    def _stream_codex_event(self, event: dict, elapsed: float) -> Optional[dict]:
        """Parse a Codex JSONL event into a stream dict for the frontend.

        Codex JSONL format (v0.120+):
          {"type": "thread.started", "thread_id": "..."}
          {"type": "turn.started"}
          {"type": "item.started",   "item": {"type": "command_execution", "command": "...", ...}}
          {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}
          {"type": "item.completed", "item": {"type": "command_execution", "command": "...",
                                              "aggregated_output": "...", "exit_code": 0}}
          {"type": "turn.completed", "usage": {...}}
        """
        etype = event.get("type", "")
        item = event.get("item") or {}
        item_type = item.get("type", "")

        if etype == "item.completed" and item_type == "agent_message":
            text = item.get("text", "")
            if text:
                return {"type": "text", "elapsed": elapsed, "text": text[:500]}

        if etype == "item.started" and item_type == "command_execution":
            cmd = item.get("command", "")
            if cmd:
                return {"type": "tool_use", "elapsed": elapsed,
                        "tool": "shell", "input": cmd[:300]}

        if etype == "item.completed" and item_type == "command_execution":
            output = item.get("aggregated_output", "")
            exit_code = item.get("exit_code", "?")
            cmd = item.get("command", "")
            if output:
                return {"type": "text", "elapsed": elapsed,
                        "text": f"[exit {exit_code}] {output[:400]}"}
            return {"type": "status", "elapsed": elapsed,
                    "text": f"command done (exit {exit_code})"}

        if etype == "item.completed" and item_type == "file_edit":
            path = item.get("path", item.get("file", ""))
            return {"type": "tool_use", "elapsed": elapsed,
                    "tool": "edit", "input": path[:200]}

        if etype == "turn.completed":
            usage = event.get("usage", {})
            tokens = usage.get("output_tokens", 0)
            # turn.completed reports session-cumulative total_token_usage.
            # Diff against baseline to get per-call usage.
            if usage:
                cum_cached = usage.get("cached_input_tokens", 0)
                cum_in = usage.get("input_tokens", 0)
                cum_out = usage.get("output_tokens", 0)
                base = self._session_cumulative
                call_in = cum_in - base["input_tokens"]
                call_out = cum_out - base["output_tokens"]
                call_cached = cum_cached - base["cached_input_tokens"]
                self.last_usage = {
                    "input_tokens": call_in,
                    "output_tokens": call_out,
                    "cache_read": call_cached,
                }
                # Session-level cumulative (for chat context display)
                self.session_usage = {
                    "input_tokens": cum_in,
                    "output_tokens": cum_out,
                    "cache_read": cum_cached,
                }
                # Update cumulative baseline for next call
                self._session_cumulative = {
                    "input_tokens": cum_in,
                    "output_tokens": cum_out,
                    "cached_input_tokens": cum_cached,
                }
            if tokens:
                return {"type": "status", "elapsed": elapsed,
                        "text": f"turn done ({tokens} tokens)"}
            return None

        # Skip noisy events
        if etype in ("turn.started", "ping", "pong"):
            return None

        # Session events
        thread_id = event.get("thread_id")
        if thread_id:
            return {"type": "status", "elapsed": elapsed,
                    "text": f"session {thread_id[:12]}..."}

        return None

    def _extract_thread_id(self, stdout: str) -> Optional[str]:
        """Extract the Codex thread id from JSONL exec output."""
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") in {"thread.started", "thread.resumed"}:
                thread_id = event.get("thread_id")
                if thread_id:
                    return thread_id

        return None

    def _handle_error(self, result):
        """Handle CLI errors with concise messages."""
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        error_lower = error_msg.lower()
        if "auth" in error_lower or "login" in error_lower or "api key" in error_lower:
            raise ConnectionError(
                "Codex authentication expired. Please re-login: codex login --device-auth"
            )
        if "quota" in error_lower or "rate limit" in error_lower:
            raise ConnectionError(
                "Codex rate limited. Try again later, or re-login: codex login --device-auth"
            )
        raise RuntimeError(f"Codex CLI error (exit {result.returncode}): {error_msg}")

    def reset(self):
        """Drop the active Codex session and clear provider-side conversation state."""
        self._session_id = None
        self._turn_count = 0
        self.has_session = False
        self._prompted_functions.clear()

    def close(self):
        """Clear Codex session and release resources."""
        self.reset()
        super().close()
