"""
Claude Code CLI provider — routes LLM calls through the Claude Code CLI.

Uses Claude Code in SDK/agent mode (stream-json) which is covered by
Claude Code subscription. No API key needed — uses the logged-in session.

Architecture:
  A single long-running `claude` process is kept alive for the entire runtime.
  Messages are sent via stdin (stream-json format) and responses read from
  stdout. The agent runs in full mode — it can use tools, edit files, and
  execute commands. This eliminates process startup overhead (~2-3s per call)
  and enables natural KV cache reuse across turns.

Supports:
- Text content blocks
- Image content blocks (base64 encoded via stream-json)
- Session continuity (single persistent process)
- Full agent execution (tool use, file editing, bash commands)

Unsupported (with warnings):
- Audio content blocks (Claude CLI does not support audio input)
- Video content blocks (Claude CLI does not support video input)
- File/PDF content blocks (Claude CLI does not support document input)

Usage:
    from agentic.providers.claude_code import ClaudeCodeRuntime

    runtime = ClaudeCodeRuntime(model="sonnet")

    # Reasoning mode (exec)
    @agentic_function
    def observe(task):
        return runtime.exec(content=[
            {"type": "text", "text": f"Find: {task}"},
            {"type": "image", "path": "screenshot.png"},
        ])

    # Execution mode (execute)
    result = runtime.execute("Create a file called hello.py with a hello world script")
    # Returns "DONE" or "ERROR: ..."
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import warnings
from typing import Optional

from agentic.runtime import Runtime


class ClaudeCodeRuntime(Runtime):
    """
    Runtime that routes LLM calls through a persistent Claude Code CLI process.

    Runs Claude Code in SDK/agent mode (not -p print mode). The agent has
    full capabilities: tool use, file editing, bash commands, etc.

    A single process is started on first call and kept alive. All subsequent
    calls reuse the same process via stdin/stdout streaming, eliminating
    the ~2-3s startup overhead per call.

    Requires `claude` CLI to be installed and logged in.
    Uses Claude Code subscription (no separate API key needed).

    Args:
        model:      Model to use (default: "sonnet"). Passed to --model flag.
        timeout:    Max seconds per LLM call (default: 600). In SDK agent mode
                    the agent may use tools (file editing, bash), so calls
                    take longer than pure text responses.
        cli_path:   Path to claude CLI binary (auto-detected if not specified).
        session_id: Kept for API compat. Ignored (persistent process manages
                    its own session internally).
        max_turns_per_process: Restart process after this many turns to
                    prevent context window overflow (default: 20).
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 600,
        cli_path: str = None,
        session_id: str = "auto",
        max_turns_per_process: int = 100,
        compact_every: int = 0,
        tools: str = None,
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("claude")
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._turn_count = 0
        self._compact_every = compact_every
        self._max_turns = max_turns_per_process
        self._tools = tools  # e.g. "" for no tools, "Bash" for bash only
        # Persistent process manages its own context — skip summarize()
        self.has_session = session_id is not None

        if self.cli_path is None:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then log in:\n"
                "  claude login"
            )

    def list_models(self) -> list[str]:
        """Return available Claude Code CLI models."""
        return ["sonnet", "opus", "haiku"]

    def _ensure_process(self):
        """Start the persistent claude process if not already running.

        Also restarts the process every max_turns_per_process turns to
        prevent context window overflow from accumulated conversation
        history (especially with images).
        """
        if self._proc is not None and self._proc.poll() is None:
            if self._turn_count < self._max_turns:
                return  # Still alive and under turn limit
            # Turn limit reached — restart process to clear context
            self._restart_process()

        cmd = [
            self.cli_path,
            "--permission-mode", "bypassPermissions",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]

        if self.model and self.model != "sonnet":
            cmd.extend(["--model", self.model])

        # Thinking effort
        effort = getattr(self, '_thinking_effort', None)
        if effort and effort != "medium":
            # Map our levels to Claude Code's levels
            effort_map = {"none": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "max"}
            cmd.extend(["--effort", effort_map.get(effort, effort)])

        if self._tools is not None:
            cmd.extend(["--tools", self._tools])

        # Remove ANTHROPIC_API_KEY so CLI uses subscription, not API credits
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            env=env,
        )
        self._turn_count = 0

        # Persistent stdout reader thread + queue (avoids race conditions
        # from creating multiple readline threads). Each call to
        # _read_line_with_timeout previously spawned a new thread that
        # blocked on readline(); old threads kept blocking even after timeout,
        # causing multiple threads to race on the same pipe and lose data.
        import queue
        self._stdout_queue = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout_loop, daemon=True
        )
        self._stdout_thread.start()

        # Drain stderr in background to prevent buffer deadlock.
        # Without this, complex tasks that produce lots of stderr output
        # (e.g., multi-tool Bash calls) fill the 64KB pipe buffer, causing
        # the process to block on stderr.write() which also blocks stdout.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

    def _call(self, content: list[dict], model: str = "sonnet", response_format: dict = None) -> str:
        """Send a message to the persistent claude process and read the response.

        Unsupported block types (audio, video, file) emit warnings and are skipped.
        """
        # Filter unsupported block types
        filtered_content = []
        for block in content:
            btype = block.get("type", "text")
            if btype == "audio":
                warnings.warn(
                    "ClaudeCodeRuntime does not support audio content blocks. "
                    "Audio block will be skipped. Use AnthropicRuntime API directly for full multimodal support.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "video":
                warnings.warn(
                    "ClaudeCodeRuntime does not support video content blocks. "
                    "Video block will be skipped. Consider using GeminiRuntime for video.",
                    UserWarning,
                    stacklevel=3,
                )
            elif btype == "file":
                warnings.warn(
                    "ClaudeCodeRuntime does not support file/PDF content blocks. "
                    "File block will be skipped. Use AnthropicRuntime API directly for PDF support.",
                    UserWarning,
                    stacklevel=3,
                )
            else:
                filtered_content.append(block)

        content = filtered_content

        with self._lock:
            self._ensure_process()

            # Build Anthropic-format content blocks
            anthropic_content = []
            for block in content:
                if block.get("type") == "text":
                    anthropic_content.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    img_block = self._encode_image(block)
                    if img_block:
                        anthropic_content.append(img_block)
                elif "text" in block:
                    anthropic_content.append({"type": "text", "text": block["text"]})

            if response_format:
                anthropic_content.append({
                    "type": "text",
                    "text": f"\n\nRespond with ONLY valid JSON matching: {json.dumps(response_format)}",
                })

            # Build stream-json message
            message = json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": anthropic_content,
                },
            })

            try:
                self._proc.stdin.write(message + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                # Process died — restart and retry
                self._proc = None
                self._ensure_process()
                self._proc.stdin.write(message + "\n")
                self._proc.stdin.flush()

            # Read response lines until we get a result
            reply, events = self._read_response()
            self._turn_count += 1

            # Save intermediate events to log (not to LLM context)
            self._save_turn_log(events)

            # Compact context periodically to prevent bloat
            if self._compact_every and self._turn_count % self._compact_every == 0:
                self._compact()

            return reply

    def _read_response(self) -> tuple[str, list]:
        """Read lines from stdout until we get a result message.

        The timeout is per-line, not total. As long as the process keeps
        producing output (e.g., tool_use events during interactive mode),
        the deadline is extended. Timeout only fires when the process goes
        silent for self.timeout seconds.

        Returns:
            (result_text, events) — the final reply text and a list of
            all intermediate events (for logging, not for LLM context).
        """
        deadline = time.time() + self.timeout
        result_text = None
        events = []
        start_time = time.time()

        while time.time() < deadline:
            # Check if process is still alive
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read() if self._proc.stderr else ""
                raise RuntimeError(
                    f"Claude Code CLI process exited unexpectedly "
                    f"(code {self._proc.returncode}): {stderr[:500]}"
                )

            # Read one line with timeout
            line = self._read_line_with_timeout(deadline - time.time())
            if line is None:
                continue

            line = line.strip()
            if not line:
                continue

            # Got output — reset deadline (process is alive and working)
            deadline = time.time() + self.timeout

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")
            elapsed = round(time.time() - start_time, 1)

            # Collect event for logging
            event = {"type": msg_type, "elapsed": elapsed}

            if msg_type == "result":
                result_text = data.get("result", "")
                usage = data.get("usage", {})
                event["result"] = result_text[:200]
                event["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read": usage.get("cache_read_input_tokens", 0),
                    "cache_create": usage.get("cache_creation_input_tokens", 0),
                }
                event["duration_ms"] = data.get("duration_ms", 0)
                event["num_turns"] = data.get("num_turns", 0)
                events.append(event)
                return result_text, events

            if msg_type == "assistant" and "message" in data:
                msg = data["message"]
                if isinstance(msg, dict) and "content" in msg:
                    for block in msg["content"]:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                event_text = {"type": "text", "elapsed": elapsed,
                                              "text": block["text"][:200]}
                                events.append(event_text)
                                result_text = block["text"]
                                if self.on_stream:
                                    try:
                                        self.on_stream(event_text)
                                    except Exception:
                                        pass
                            elif block.get("type") == "tool_use":
                                event_tool = {"type": "tool_use", "elapsed": elapsed,
                                              "tool": block.get("name", "?"),
                                              "input": str(block.get("input", {}))[:100]}
                                events.append(event_tool)
                                if self.on_stream:
                                    try:
                                        self.on_stream(event_tool)
                                    except Exception:
                                        pass
                    continue

            # Other events (rate_limit, system, etc.)
            events.append(event)

        raise TimeoutError(f"Claude Code CLI timed out (no output for {self.timeout}s)")

    def _save_turn_log(self, events: list):
        """Save intermediate events from a turn to a log file."""
        try:
            log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "claude_code_turns.jsonl")
            entry = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "turn": self._turn_count,
                "events": events,
            }
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Never fail for logging

    def _read_stdout_loop(self):
        """Persistent thread that reads stdout lines into a queue."""
        try:
            while self._proc and self._proc.poll() is None:
                line = self._proc.stdout.readline()
                if not line:
                    break
                self._stdout_queue.put(line)
        except Exception:
            pass

    def _read_line_with_timeout(self, remaining: float) -> Optional[str]:
        """Read a single line from the stdout queue with timeout."""
        import queue
        try:
            return self._stdout_queue.get(timeout=min(remaining, 5.0))
        except queue.Empty:
            return None

    def _drain_stderr(self):
        """Read and discard stderr to prevent pipe buffer deadlock."""
        try:
            while self._proc and self._proc.poll() is None:
                line = self._proc.stderr.readline()
                if not line:
                    break
        except Exception:
            pass

    def _compact(self):
        """Send /compact to compress the conversation context.

        This is a Claude Code slash command that summarizes prior messages
        to free up context window space. Keeps the session alive without
        restarting the process.
        """
        try:
            compact_msg = json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "/compact"}],
                },
            })
            self._proc.stdin.write(compact_msg + "\n")
            self._proc.stdin.flush()
            # Read the compact response (don't care about content)
            self._read_response()
        except Exception:
            pass  # Best-effort, never fail

    def _encode_image(self, block: dict) -> Optional[dict]:
        """Convert an image content block to Anthropic base64 format."""
        if "data" in block:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.get("media_type", "image/png"),
                    "data": block["data"],
                },
            }

        if "path" in block:
            path = block["path"]
            media_type = mimetypes.guess_type(path)[0] or "image/png"
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            except FileNotFoundError:
                return None

        return None

    def _restart_process(self):
        """Kill the current process so a new one starts on next _call().

        Unlike close(), this does NOT mark the runtime as closed —
        it just restarts the underlying CLI process.
        """
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._turn_count = 0

    def close(self):
        """Kill the Claude Code process and release resources."""
        self._restart_process()
        super().close()
