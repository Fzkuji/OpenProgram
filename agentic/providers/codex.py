"""
Codex CLI provider — routes LLM calls through the OpenAI Codex CLI.

Uses `codex exec` (non-interactive mode) with configurable sandbox,
approval policy, and optional web search.
No API key needed in the harness — Codex CLI uses its own auth.

Supports:
- Text content blocks
- Image content blocks (via -i flag for file paths, temp files for base64)
- Session continuity (via `codex exec resume <session_id>`)

Unsupported (with warnings):
- Audio content blocks (Codex CLI does not support audio input)
- Video content blocks (Codex CLI does not support video input)
- File/PDF content blocks (Codex CLI does not support document input)

Usage:
    from agentic.providers.codex import CodexRuntime

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

from agentic.runtime import Runtime


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
        model: str = None,
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

        if self.cli_path is None:
            raise FileNotFoundError(
                "Codex CLI not found. Install it first:\n"
                "  npm install -g @openai/codex\n"
                "Then authenticate:\n"
                "  codex auth"
            )

    def list_models(self) -> list[str]:
        """Auto-detect available models for Codex CLI.

        ChatGPT subscription mode: limited set.
        API key mode: queries OpenAI API for all chat-capable models.
        """
        try:
            result = subprocess.run(
                [self.cli_path, "login", "status"],
                capture_output=True, text=True, timeout=5,
            )
            output = (result.stdout + result.stderr).strip()
            if "ChatGPT" in output:
                return ["o4-mini", "gpt-5.4", "gpt-5.4-mini"]
        except Exception:
            pass

        # API key mode — query OpenAI API for real available models
        try:
            import re
            import openai
            client = openai.OpenAI()
            all_models = client.models.list()
            skip = ("audio", "realtime", "image", "search", "transcribe", "tts",
                    "instruct", "embed", "davinci", "babbage", "whisper", "dall-e",
                    "moderation", "diarize", "chat-latest", "-codex", "deep-research")
            date_re = re.compile(r"-\d{4}-\d{2}-\d{2}")
            models = []
            for m in all_models:
                mid = m.id
                if not any(mid.startswith(p) for p in ("gpt-4", "gpt-5", "o1", "o3", "o4")):
                    continue
                if any(s in mid for s in skip):
                    continue
                if date_re.search(mid):
                    continue
                models.append(mid)
            models.sort(key=lambda x: (not x.startswith("o"), x))
            return models if models else ["o4-mini", "gpt-5.4", "gpt-5.4-mini"]
        except Exception:
            return ["o4-mini", "gpt-5.4", "gpt-5.4-mini"]

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
        if self.search:
            cmd.append("--search")
        if self.approval_policy:
            cmd.extend(["-a", self.approval_policy])

        if is_resume:
            cmd.extend(["exec", "resume", self._session_id])
        else:
            cmd.append("exec")

        # Common flags
        if model:
            cmd.extend(["--model", model])

        if self.full_auto:
            cmd.append("--full-auto")
        elif self.sandbox:
            cmd.extend(["--sandbox", self.sandbox])

        if self.workdir:
            cmd.extend(["--cd", self.workdir])

        cmd.append("--skip-git-repo-check")

        # Image flags
        for img_path in image_paths:
            cmd.extend(["-i", img_path])

        # Output: capture last message to a temp file for reliable extraction
        fd, output_file = tempfile.mkstemp(suffix=".txt", prefix="codex_out_")
        os.close(fd)
        try:
            cmd.append("--json")
            cmd.extend(["-o", output_file])

            # Pass prompt via stdin for long prompts (CLI arg has OS limits),
            # use "-" to signal stdin mode.
            cmd.append("-")

            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                raise TimeoutError(f"Codex CLI timed out after {self.timeout}s")

            if proc.returncode != 0:
                self._handle_error(proc)

            session_id = self._extract_thread_id(proc.stdout)
            if session_id:
                self._session_id = session_id
                self.has_session = True

            # Read output from the -o file
            try:
                with open(output_file, "r") as f:
                    result = f.read().strip()
                if result:
                    return result
            except (FileNotFoundError, IOError):
                pass

            # Fall back to stdout
            return proc.stdout.strip()

        finally:
            try:
                os.unlink(output_file)
            except OSError:
                pass

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
        """Handle CLI errors."""
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        error_lower = error_msg.lower()
        if "auth" in error_lower or "login" in error_lower or "api key" in error_lower:
            raise ConnectionError(
                f"Codex CLI authentication error. Run: codex auth\n"
                f"Error: {error_msg}"
            )
        if "quota" in error_lower or "rate limit" in error_lower:
            raise ConnectionError(
                f"Codex CLI quota/rate limit exceeded.\n"
                f"Error: {error_msg}"
            )
        raise RuntimeError(f"Codex CLI error (exit {result.returncode}): {error_msg}")

    def reset(self):
        """Start a new session."""
        self._session_id = None
        self._turn_count = 0
        self.has_session = False
