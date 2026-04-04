"""
Claude Code CLI provider — routes LLM calls through the Claude Code CLI.

Uses `claude -p` (print mode) which is covered by Claude Code subscription.
No API key needed — uses the logged-in Claude Code session.

Supports:
- Text content blocks
- Image content blocks (via --input-format stream-json with base64)
- Session continuity (via --session-id + --resume)

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
import shutil
import subprocess
import uuid
from typing import Optional

from agentic.runtime import Runtime


class ClaudeCodeRuntime(Runtime):
    """
    Runtime that routes LLM calls through the Claude Code CLI.

    Requires `claude` CLI to be installed and logged in.
    Uses Claude Code subscription (no separate API key needed).

    Supports images via stream-json input format (base64 encoded).
    Supports session continuity via --session-id.

    Args:
        model:      Model to use (default: "sonnet"). Passed to --model flag.
        timeout:    Max seconds per CLI call (default: 300).
        cli_path:   Path to claude CLI binary (auto-detected if not specified).
        session_id: Session ID for continuity. "auto" = generate UUID.
                    None = stateless (each call independent).
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 300,
        cli_path: str = None,
        session_id: str = "auto",
    ):
        super().__init__(model=model)
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("claude")
        self._turn_count = 0

        if session_id == "auto":
            self._session_id = str(uuid.uuid4())
        else:
            self._session_id = session_id

        if self.cli_path is None:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then log in:\n"
                "  claude login"
            )

    def _call(self, content: list[dict], model: str = "sonnet", response_format: dict = None) -> str:
        """Call Claude Code CLI with the content list.

        If content contains image blocks, uses stream-json input format
        to pass base64-encoded images. Otherwise uses plain text mode.
        """
        has_images = any(
            b.get("type") == "image" and (b.get("path") or b.get("data"))
            for b in content
        )

        if has_images:
            return self._call_with_images(content, model, response_format)
        return self._call_text_only(content, model, response_format)

    def _call_text_only(self, content: list[dict], model: str, response_format: dict = None) -> str:
        """Plain text mode — fast path for text-only calls."""
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block["text"])

        prompt = "\n".join(parts)
        if response_format:
            prompt += f"\n\nRespond with ONLY valid JSON matching this schema: {json.dumps(response_format)}"

        cmd = [self.cli_path, "-p", "--permission-mode", "bypassPermissions"]

        if self._session_id:
            if self._turn_count > 0:
                cmd.extend(["--resume", "--session-id", self._session_id])
            else:
                cmd.extend(["--session-id", self._session_id])

        if model and model != "sonnet":
            cmd.extend(["--model", model])

        cmd.append(prompt)

        result = self._run_cli(cmd)
        self._turn_count += 1
        return result

    def _call_with_images(self, content: list[dict], model: str, response_format: dict = None) -> str:
        """Stream-json mode — supports images via base64."""
        # Build Anthropic-format content blocks
        anthropic_content = []
        for block in content:
            if block.get("type") == "text":
                anthropic_content.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image":
                img_block = self._encode_image(block)
                if img_block:
                    anthropic_content.append(img_block)

        if response_format:
            anthropic_content.append({
                "type": "text",
                "text": f"\n\nRespond with ONLY valid JSON matching: {json.dumps(response_format)}",
            })

        # Build stream-json message
        stream_msg = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": anthropic_content,
            },
        })

        cmd = [
            self.cli_path, "-p",
            "--permission-mode", "bypassPermissions",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",  # Required for stream-json output in print mode
        ]

        if self._session_id:
            if self._turn_count > 0:
                cmd.extend(["--resume", "--session-id", self._session_id])
            else:
                cmd.extend(["--session-id", self._session_id])

        if model and model != "sonnet":
            cmd.extend(["--model", model])

        try:
            proc = subprocess.run(
                cmd,
                input=stream_msg,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Claude Code CLI timed out after {self.timeout}s")

        if proc.returncode != 0:
            self._handle_error(proc)

        self._turn_count += 1

        # Parse stream-json output — find the result
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "result":
                    return data.get("result", "")
                if data.get("type") == "assistant" and "message" in data:
                    msg = data["message"]
                    if isinstance(msg, dict) and "content" in msg:
                        texts = [
                            b["text"] for b in msg["content"]
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        if texts:
                            return "\n".join(texts)
            except json.JSONDecodeError:
                continue

        return proc.stdout.strip()

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

    def _run_cli(self, cmd: list[str]) -> str:
        """Run CLI command and return stdout."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Claude Code CLI timed out after {self.timeout}s")

        if result.returncode != 0:
            self._handle_error(result)

        return result.stdout.strip()

    def _handle_error(self, result):
        """Handle CLI errors."""
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        if "Not logged in" in error_msg or "login" in error_msg.lower():
            raise ConnectionError(
                f"Claude Code CLI not logged in. Run: claude login\n"
                f"Error: {error_msg}"
            )
        raise RuntimeError(f"Claude Code CLI error (exit {result.returncode}): {error_msg}")

    def reset(self):
        """Start a new session."""
        self._session_id = str(uuid.uuid4())
        self._turn_count = 0
