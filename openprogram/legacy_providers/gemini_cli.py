"""
Gemini CLI provider — routes LLM calls through the Gemini CLI.

Uses Gemini CLI in agent mode. No API key needed — uses your Google account.

The CLI runs as a full agent with tool use, file editing, and command
execution capabilities (when --yolo is enabled).

Supports:
- Text content blocks
- Session continuity (via --resume <session_id>)
- JSON output format for clean response extraction
- Full agent execution (tool use, file editing, commands)

Unsupported (with warnings):
- Image content blocks (CLI does not support image input)
- Audio/video/file content blocks

Usage:
    from openprogram.legacy_providers.gemini_cli import GeminiCLIRuntime

    runtime = GeminiCLIRuntime()

    # Reasoning mode
    @agentic_function
    def observe(task):
        return runtime.exec(content=[
            {"type": "text", "text": f"Find: {task}"},
        ])

    # Execution mode
    result = runtime.execute("Create a hello.py file")
"""

from __future__ import annotations

import json
import os
import subprocess
import shutil
import warnings
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


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
        self.last_thread_id = None  # for external session reuse

        # Live subprocess handle so webui's kill_active_runtime can terminate
        # mid-call. Set to None when no call is in flight.
        self._proc: Optional[subprocess.Popen] = None

        if self.cli_path is None:
            raise FileNotFoundError(
                "Gemini CLI not found. Install it first:\n"
                "  npm install -g @google/gemini-cli\n"
                "Then log in:\n"
                "  gemini"
            )

    def list_models(self) -> list[str]:
        """Return available Gemini CLI models."""
        return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

    def _call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """Call Gemini CLI with the content list.

        Uses --output-format json to get structured responses and
        --resume to maintain session continuity across calls.
        """
        prompt = self._build_prompt(content, response_format)
        return self._run_gemini(prompt, model)

    def _build_prompt(self, content: list[dict], response_format: dict = None) -> str:
        """Build prompt string from content blocks."""
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

        return prompt

    def _run_gemini(self, prompt: str, model: str = "default") -> str:
        """Build and run the gemini CLI command."""
        cmd = [self.cli_path, prompt, "--output-format", "json"]

        if model and model != "default":
            cmd.extend(["-m", model])
        if self.sandbox:
            cmd.append("-s")
        if self.yolo:
            cmd.append("-y")

        # Resume session if we have one
        if self._session_id and self._turn_count > 0:
            cmd.extend(["--resume", self._session_id])

        # Remove unrelated API keys so CLI doesn't pick up wrong credentials
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        # Popen (not subprocess.run) so self._proc is exposed for external kill.
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start Gemini CLI: {e}")

        self._proc = proc
        try:
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass
                raise TimeoutError(f"Gemini CLI timed out after {self.timeout}s")

            if proc.returncode != 0:
                error_msg = (stderr or "").strip() or (stdout or "").strip() or "Unknown error"
                raise RuntimeError(f"Gemini CLI error (exit {proc.returncode}): {error_msg}")

            # Parse JSON output to extract clean response and session_id
            raw = (stdout or "").strip()
        finally:
            self._proc = None
        try:
            data = json.loads(raw)
            # Capture session_id for future resume
            if "session_id" in data:
                self._session_id = data["session_id"]
                self.last_thread_id = data["session_id"]
                self.has_session = True  # CLI now manages context
            self._turn_count += 1
            return data.get("response", raw)
        except (json.JSONDecodeError, KeyError):
            # Fallback to raw output if JSON parsing fails
            self._turn_count += 1
            return raw

    def close(self):
        """Clear session and release resources."""
        self._session_id = None
        self._turn_count = 0
        super().close()
