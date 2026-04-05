"""
Gemini CLI provider — routes LLM calls through the Gemini CLI.

Uses `gemini -p` (prompt mode) which uses your Google account login.
No API key needed.

Supports:
- Text content blocks
- Session continuity (via --resume <session_id>)
- JSON output format for clean response extraction

Unsupported (with warnings):
- Image content blocks (CLI does not support image input)
- Audio/video/file content blocks

Usage:
    from agentic.providers.gemini_cli import GeminiCLIRuntime

    runtime = GeminiCLIRuntime()

    @agentic_function
    def observe(task):
        return runtime.exec(content=[
            {"type": "text", "text": f"Find: {task}"},
        ])
"""

from __future__ import annotations

import json
import subprocess
import shutil
import warnings
from typing import Optional

from agentic.runtime import Runtime


class GeminiCLIRuntime(Runtime):
    """
    Runtime that routes LLM calls through the Gemini CLI.

    Requires `gemini` CLI to be installed and logged in.
    Uses Google account (no separate API key needed).

    Session continuity: the first call starts a new session and captures
    the session_id from JSON output. Subsequent calls use --resume to
    maintain conversation context.

    Args:
        model:      Model to use (default: None = CLI default).
        timeout:    Max seconds per CLI call (default: 120).
        cli_path:   Path to gemini CLI binary (auto-detected if not specified).
        sandbox:    Run in sandbox mode (default: False).
        yolo:       Auto-approve all actions (default: True for non-interactive).
    """

    def __init__(
        self,
        model: str = None,
        timeout: int = 120,
        cli_path: str = None,
        sandbox: bool = False,
        yolo: bool = True,
    ):
        super().__init__(model=model or "default")
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("gemini")
        self.sandbox = sandbox
        self.yolo = yolo
        self._session_id: Optional[str] = None
        self._turn_count = 0

        if self.cli_path is None:
            raise FileNotFoundError(
                "Gemini CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/gemini-cli\n"
                "Then log in:\n"
                "  gemini"
            )

    def _call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """Call Gemini CLI with the content list.

        Uses --output-format json to get structured responses and
        --resume to maintain session continuity across calls.
        """
        # Build prompt from content blocks
        parts = []
        for block in content:
            block_type = block.get("type", "text")

            if block_type == "text":
                if "text" in block:
                    parts.append(block["text"])
            elif block_type == "image":
                warnings.warn(
                    "GeminiCLIRuntime: image blocks not supported in CLI mode, "
                    "passing as text placeholder. Use GeminiRuntime (API) for image support.",
                    UserWarning,
                    stacklevel=2,
                )
                parts.append(f"[Image: {block.get('path', block.get('url', 'unknown'))}]")
            elif block_type in ("audio", "video", "file"):
                warnings.warn(
                    f"GeminiCLIRuntime: '{block_type}' blocks not supported in CLI mode. "
                    f"Use GeminiRuntime (API) for full multimodal support.",
                    UserWarning,
                    stacklevel=2,
                )
                parts.append(f"[{block_type.title()}: {block.get('path', 'unknown')}]")
            elif "text" in block:
                parts.append(block["text"])

        prompt = "\n".join(parts)

        if response_format:
            prompt += f"\n\nRespond with ONLY valid JSON matching this schema: {json.dumps(response_format)}"

        # Build command
        cmd = [self.cli_path, "-p", prompt, "--output-format", "json"]

        if model and model != "default":
            cmd.extend(["-m", model])
        if self.sandbox:
            cmd.append("-s")
        if self.yolo:
            cmd.append("-y")

        # Resume session if we have one
        if self._session_id and self._turn_count > 0:
            cmd.extend(["--resume", self._session_id])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Gemini CLI timed out after {self.timeout}s")

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise RuntimeError(f"Gemini CLI error (exit {result.returncode}): {error_msg}")

        # Parse JSON output to extract clean response and session_id
        raw = result.stdout.strip()
        try:
            data = json.loads(raw)
            # Capture session_id for future resume
            if "session_id" in data:
                self._session_id = data["session_id"]
            self._turn_count += 1
            return data.get("response", raw)
        except (json.JSONDecodeError, KeyError):
            # Fallback to raw output if JSON parsing fails
            self._turn_count += 1
            return raw

    def new_session(self):
        """Start a new session (discard current session context)."""
        self._session_id = None
        self._turn_count = 0
