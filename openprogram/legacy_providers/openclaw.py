"""
OpenClawRuntime — Runtime subclass for the OpenClaw agent CLI.

Routes LLM calls through `openclaw agent`. Uses your existing OpenClaw
configuration — no separate API keys, no per-token cost beyond what
your OpenClaw setup already covers.

Session mode: maintains a per-runtime session_id across calls so the
OpenClaw agent can accumulate context on its side.

Requires: `openclaw` CLI on PATH (https://github.com/openclaw/openclaw)

Usage:
    from openprogram.legacy_providers import OpenClawRuntime

    rt = OpenClawRuntime(
        model="default",
        system="You are a helpful assistant.",  # optional
    )
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class OpenClawRuntime(Runtime):
    """
    Runtime implementation that shells out to the `openclaw agent` CLI.

    Args:
        model:      Default model name (forwarded to OpenClaw — its internal
                    config decides which model is actually used).
        system:     Optional system prompt prepended to every call's prompt.
        timeout:    Per-call CLI timeout in seconds (default: 120).
        cli_path:   Path to the openclaw binary. Auto-detected if None.
    """

    def __init__(
        self,
        model: str = "default",
        system: Optional[str] = None,
        timeout: int = 120,
        cli_path: Optional[str] = None,
    ):
        super().__init__(model=model)
        self.system = system
        self.timeout = timeout
        self.cli_path = cli_path or shutil.which("openclaw")
        self._session_id: Optional[str] = None  # set on first call for continuity

        # Live subprocess handle so webui's kill_active_runtime can terminate
        # mid-call. Set to None when no call is in flight.
        self._proc: Optional[subprocess.Popen] = None

        if self.cli_path is None:
            raise FileNotFoundError(
                "openclaw CLI not found on PATH. Install from "
                "https://github.com/openclaw/openclaw"
            )

    def _call(
        self,
        content: list[dict],
        model: str = "default",
        response_format: Optional[dict] = None,
    ) -> str:
        """Build a single prompt string and shell out to `openclaw agent`.

        OpenClaw has a built-in `image` tool — image blocks are passed as
        file paths with an instruction for the agent to analyze them.
        Unsupported block types (audio, video, file) are ignored silently.
        """
        parts: list[str] = []
        if self.system:
            parts.append(self.system)
            parts.append("")

        image_paths: list[str] = []
        for block in content:
            btype = block.get("type", "text")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "image":
                p = block.get("path")
                if p:
                    image_paths.append(p)

        if image_paths:
            paths_str = ", ".join(image_paths)
            parts.append(
                f"\nAnalyze the following image(s) using your image tool: {paths_str}"
                "\nIncorporate the visual analysis into your response."
            )

        if response_format:
            parts.append(
                f"\nReturn ONLY valid JSON matching: {json.dumps(response_format)}"
            )

        prompt = "\n".join(parts)

        if self._session_id is None:
            self._session_id = str(uuid.uuid4())

        cmd = [
            self.cli_path, "agent",
            "--message", prompt,
            "--session-id", self._session_id,
            "--json",
        ]

        # Popen (not subprocess.run) so self._proc is exposed for external kill.
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start openclaw agent: {e}")

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
                raise TimeoutError(f"openclaw agent timed out after {self.timeout}s")

            if proc.returncode != 0:
                raise RuntimeError(
                    f"openclaw agent failed (exit {proc.returncode}): "
                    f"{(stderr or '').strip() or (stdout or '').strip()}"
                )

            # Build a minimal result shim for the downstream JSON parsing.
            class _R:
                pass
            result = _R()
            result.stdout = stdout or ""
            result.stderr = stderr or ""
            result.returncode = proc.returncode
        finally:
            self._proc = None

        try:
            data = json.loads(result.stdout.strip())
            payloads = data.get("result", {}).get("payloads", [])
            if payloads:
                return payloads[0].get("text", result.stdout.strip())
            return data.get("reply", data.get("message", result.stdout.strip()))
        except json.JSONDecodeError:
            return result.stdout.strip()

    def reset(self) -> None:
        """Drop the session_id so the next call starts a fresh OpenClaw session."""
        self._session_id = None
