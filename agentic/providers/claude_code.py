"""
Claude Code CLI provider — routes LLM calls through the Claude Code CLI.

Uses `claude -p` (print mode) which is covered by Claude Code subscription.
No API key needed — uses the logged-in Claude Code session.

Architecture:
  A single long-running `claude` process is kept alive for the entire runtime.
  Messages are sent via stdin (stream-json format) and responses read from
  stdout. This eliminates process startup overhead (~2-3s per call) and
  enables natural KV cache reuse across turns.

Supports:
- Text content blocks
- Image content blocks (base64 encoded via stream-json)
- Session continuity (single persistent process)

Unsupported (with warnings):
- Audio content blocks (Claude CLI does not support audio input)
- Video content blocks (Claude CLI does not support video input)
- File/PDF content blocks (Claude CLI does not support document input)

Usage:
    from agentic.providers.claude_code import ClaudeCodeRuntime

    runtime = ClaudeCodeRuntime(model="sonnet")

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

    A single `claude -p` process is started on first call and kept alive.
    All subsequent calls reuse the same process via stdin/stdout streaming,
    eliminating the ~2-3s startup overhead per call.

    Requires `claude` CLI to be installed and logged in.
    Uses Claude Code subscription (no separate API key needed).

    Args:
        model:      Model to use (default: "sonnet"). Passed to --model flag.
        timeout:    Max seconds per LLM call (default: 300).
        cli_path:   Path to claude CLI binary (auto-detected if not specified).
        session_id: Kept for API compat. Ignored (persistent process manages
                    its own session internally).
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 300,
        cli_path: str = None,
        session_id: str = "auto",
        max_turns_per_process: int = 5,
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("claude")
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._turn_count = 0
        self._max_turns = max_turns_per_process

        if self.cli_path is None:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then log in:\n"
                "  claude login"
            )

    def _ensure_process(self):
        """Start the persistent claude process if not already running.

        Also restarts the process every max_turns_per_process turns to
        prevent context window overflow from accumulated conversation
        history (especially with images).
        """
        if self._proc is not None and self._proc.poll() is None:
            if self._turn_count < self._max_turns:
                return  # Still alive and under turn limit
            # Turn limit reached — restart to clear context
            self.reset()

        cmd = [
            self.cli_path, "-p",
            "--permission-mode", "bypassPermissions",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",  # required for stream-json output in print mode
            "--no-session-persistence",  # don't save to disk (process-only context)
        ]

        if self.model and self.model != "sonnet":
            cmd.extend(["--model", self.model])

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )
        self._turn_count = 0

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
            reply = self._read_response()
            self._turn_count += 1
            return reply

    def _read_response(self) -> str:
        """Read lines from stdout until we get a result message."""
        deadline = time.time() + self.timeout
        result_text = None

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

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            # "result" marks the end of a turn
            if msg_type == "result":
                result_text = data.get("result", "")
                return result_text

            # "assistant" message with content
            if msg_type == "assistant" and "message" in data:
                msg = data["message"]
                if isinstance(msg, dict) and "content" in msg:
                    texts = [
                        b["text"] for b in msg["content"]
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if texts:
                        result_text = "\n".join(texts)
                        # Don't return yet — wait for "result" to mark end of turn

        raise TimeoutError(f"Claude Code CLI timed out after {self.timeout}s")

    def _read_line_with_timeout(self, remaining: float) -> Optional[str]:
        """Read a single line from stdout with timeout using a thread."""
        result = [None]
        exc = [None]

        def _read():
            try:
                result[0] = self._proc.stdout.readline()
            except Exception as e:
                exc[0] = e

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout=min(remaining, 5.0))

        if exc[0]:
            raise exc[0]
        return result[0]

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

    def reset(self):
        """Kill the current process and start fresh on next call."""
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

    def __del__(self):
        """Clean up the subprocess on garbage collection."""
        self.reset()
