"""
Session — the pluggable execution backend for Agentic Programming.

A Session is like a CPU or interpreter — it executes what it's given and returns
the result. It doesn't decide what to run or in what order.

Two lifecycles:
    - Ephemeral: created for one Function, then destroyed (Runtime uses these)
    - Persistent: survives across calls, maintains conversation (Programmer uses these)

All Sessions maintain conversation history so that:
    1. Persistent Sessions can be reused across multiple calls
    2. KV cache prefix is preserved when the same Session is reused
    3. Context accumulates naturally (append-only)

Any class that implements send() is a valid Session.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Union
import uuid


# Message can be plain text, a structured dict, or a list of content parts
Message = Union[str, dict, list]


class Session(ABC):
    """
    The runtime interface for Function execution.

    A Session is anything that can:
        1. Receive a message (text, multimodal, or structured)
        2. Return a reply (string)
        3. Maintain conversation history for reuse

    The Session is responsible for:
        - Interpreting the message format
        - Maintaining its own conversation history
        - Managing its own connection and authentication
        - Returning complete (not streamed) replies

    The Session is NOT responsible for:
        - Parsing return values (Function handles that)
        - Retry logic (Function handles that)
        - Deciding what to do next (Programmer does that)
    """

    @abstractmethod
    def send(self, message: Message) -> str:
        """Send a message and return the reply."""
        pass

    def reset(self):
        """Clear conversation history. Override in subclasses."""
        pass

    @property
    def history_length(self) -> int:
        """Number of turns in conversation history. Override in subclasses."""
        return 0


# ==================================================================
# Direct API Sessions (stateful — maintain history in memory)
# ==================================================================

class AnthropicSession(Session):
    """
    Direct Anthropic API session. Supports text and image input.
    Maintains full conversation history for KV cache reuse.

    Args:
        model:          Model name (default: claude-sonnet-4-6)
        max_tokens:     Max reply tokens
        system_prompt:  System prompt for the session
        api_key:        Anthropic API key (default: ANTHROPIC_API_KEY env var)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        system_prompt: str = "You are a helpful assistant that follows instructions precisely and always returns valid JSON when asked.",
        api_key: str = None,
    ):
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._history: list[dict] = []

    def send(self, message: Message) -> str:
        content = self._to_content(message)
        self._history.append({"role": "user", "content": content})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_prompt,
            messages=self._history,
        )

        reply = response.content[0].text
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def reset(self):
        """Clear conversation history."""
        self._history = []

    @property
    def history_length(self) -> int:
        return len(self._history) // 2  # each turn = user + assistant

    @staticmethod
    def _to_content(message: Message):
        """Convert flexible message format to Anthropic content format."""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return message
        if isinstance(message, dict):
            parts = []
            if "text" in message:
                parts.append({"type": "text", "text": message["text"]})
            if "images" in message:
                import base64
                for img_path in message["images"]:
                    with open(img_path, "rb") as f:
                        data = base64.standard_b64encode(f.read()).decode()
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    media_type = {
                        "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "gif": "image/gif",
                        "webp": "image/webp",
                    }.get(ext, "image/png")
                    parts.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        }
                    })
            return parts if parts else message
        return message


class OpenAISession(Session):
    """
    Direct OpenAI API session. Supports text and image input.
    Maintains full conversation history for KV cache reuse.

    Args:
        model:          Model name (default: gpt-4o)
        max_tokens:     Max reply tokens
        system_prompt:  System prompt for the session
        api_key:        OpenAI API key (default: OPENAI_API_KEY env var)
        base_url:       Custom API base URL (for compatible APIs)
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        system_prompt: str = "You are a helpful assistant that follows instructions precisely and always returns valid JSON when asked.",
        api_key: str = None,
        base_url: str = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai")

        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        self._client = OpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._history: list[dict] = []

    def send(self, message: Message) -> str:
        content = self._to_content(message)
        self._history.append({"role": "user", "content": content})

        messages = [{"role": "system", "content": self._system_prompt}] + self._history

        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=messages,
        )

        reply = response.choices[0].message.content
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def reset(self):
        self._history = []

    @property
    def history_length(self) -> int:
        return len(self._history) // 2


    @staticmethod
    def _to_content(message: Message):
        """Convert flexible message format to OpenAI content format."""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return message
        if isinstance(message, dict):
            parts = []
            if "text" in message:
                parts.append({"type": "text", "text": message["text"]})
            if "images" in message:
                import base64
                for img_path in message["images"]:
                    with open(img_path, "rb") as f:
                        data = base64.standard_b64encode(f.read()).decode()
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    media_type = {
                        "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "gif": "image/gif",
                        "webp": "image/webp",
                    }.get(ext, "image/png")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"}
                    })
            return parts if parts else message
        return message


# ==================================================================
# CLI Agent Sessions (stateful — use session ID for persistence)
# ==================================================================

class ClaudeCodeSession(Session):
    """
    Claude Code CLI session with conversation persistence.

    Uses --session-id to maintain a persistent conversation across calls.
    Each send() resumes the same conversation, preserving full context.

    Two modes:
        - Persistent (default): uses --resume + --session-id to continue conversations
        - Stateless: each send() is independent (set session_id=None)

    Args:
        model:              Model override
        max_turns:          Max agent turns per invocation
        system_prompt:      System prompt override
        allowed_tools:      List of allowed tools
        permission_mode:    Permission mode (default: bypassPermissions)
        session_id:         Session ID for persistence (auto-generated if not set)
        timeout:            Seconds to wait for completion (default: 600)
    """

    def __init__(
        self,
        model: str = None,
        max_turns: int = None,
        system_prompt: str = None,
        allowed_tools: list = None,
        permission_mode: str = "bypassPermissions",
        session_id: str = "auto",
        timeout: int = 600,
    ):
        self._model = model
        self._max_turns = max_turns
        self._system_prompt = system_prompt
        self._allowed_tools = allowed_tools
        self._permission_mode = permission_mode
        self._timeout = timeout
        self._turn_count = 0

        # Session persistence
        if session_id == "auto":
            self._session_id = f"harness-{uuid.uuid4().hex[:12]}"
        else:
            self._session_id = session_id  # None = stateless

    def send(self, message: Message) -> str:
        import subprocess
        import os
        import json as _json

        has_images = self._has_images(message)

        if has_images:
            # Use stream-json mode for multimodal input
            return self._send_stream_json(message)

        # Plain text mode
        text = self._extract_text(message)

        cmd = ["claude", "--print", f"--permission-mode={self._permission_mode}"]

        # Session persistence: resume on 2nd+ call
        if self._session_id:
            if self._turn_count > 0:
                cmd.extend(["--resume", "--session-id", self._session_id])
            else:
                cmd.extend(["--session-id", self._session_id])

        if self._model:
            cmd.extend(["--model", self._model])
        if self._max_turns:
            cmd.extend(["--max-turns", str(self._max_turns)])
        if self._system_prompt and self._turn_count == 0:
            cmd.extend(["--system-prompt", self._system_prompt])
        if self._allowed_tools:
            for tool in self._allowed_tools:
                cmd.extend(["--allowedTools", tool])

        cmd.extend(["--prompt", text])

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._timeout, env=os.environ,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        self._turn_count += 1
        return result.stdout.strip()

    def _send_stream_json(self, message: Message) -> str:
        """Send multimodal message via stream-json input format."""
        import subprocess
        import os
        import json as _json

        content = self._to_anthropic_content(message)

        # Build stream-json input
        stream_msg = _json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            }
        })

        cmd = [
            "claude", "--print",
            f"--permission-mode={self._permission_mode}",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]

        if self._session_id:
            if self._turn_count > 0:
                cmd.extend(["--resume", "--session-id", self._session_id])
            else:
                cmd.extend(["--session-id", self._session_id])

        if self._model:
            cmd.extend(["--model", self._model])
        if self._max_turns:
            cmd.extend(["--max-turns", str(self._max_turns)])
        if self._system_prompt and self._turn_count == 0:
            cmd.extend(["--system-prompt", self._system_prompt])
        if self._allowed_tools:
            for tool in self._allowed_tools:
                cmd.extend(["--allowedTools", tool])

        result = subprocess.run(
            cmd, input=stream_msg, capture_output=True, text=True,
            timeout=self._timeout, env=os.environ,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        # Parse stream-json output: find the result line
        for line in result.stdout.strip().split("\n"):
            try:
                data = _json.loads(line)
                if data.get("type") == "result":
                    self._turn_count += 1
                    return data.get("result", "")
            except _json.JSONDecodeError:
                continue

        self._turn_count += 1
        return result.stdout.strip()

    def reset(self):
        """Start a new session (new session ID)."""
        self._session_id = f"harness-{uuid.uuid4().hex[:12]}"
        self._turn_count = 0

    @property
    def history_length(self) -> int:
        return self._turn_count

    @staticmethod
    def _has_images(message: Message) -> bool:
        """Check if message contains image data."""
        if isinstance(message, dict):
            return bool(message.get("images"))
        if isinstance(message, list):
            return any(
                isinstance(p, dict) and p.get("type") in ("image", "image_url")
                for p in message
            )
        return False

    @staticmethod
    def _to_anthropic_content(message: Message) -> list:
        """Convert message to Anthropic content format for stream-json."""
        import base64 as _b64

        if isinstance(message, str):
            return [{"type": "text", "text": message}]

        if isinstance(message, list):
            return message  # assume already in Anthropic format

        if isinstance(message, dict):
            parts = []
            if "text" in message:
                parts.append({"type": "text", "text": message["text"]})
            if "images" in message:
                for img_path in message["images"]:
                    with open(img_path, "rb") as f:
                        data = _b64.standard_b64encode(f.read()).decode()
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    media_type = {
                        "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "gif": "image/gif",
                        "webp": "image/webp",
                    }.get(ext, "image/png")
                    parts.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        }
                    })
            return parts if parts else [{"type": "text", "text": str(message)}]

        return [{"type": "text", "text": str(message)}]

    @staticmethod
    def _extract_text(message: Message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return message.get("text", str(message))
        if isinstance(message, list):
            texts = [p.get("text", "") for p in message
                     if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(texts) if texts else str(message)
        return str(message)


class CodexSession(Session):
    """
    OpenAI Codex CLI session with conversation persistence.

    Uses --session-id to maintain a persistent conversation across calls.

    Two modes:
        - Persistent (default): uses --session-id to continue conversations
        - Stateless: each send() is independent (set session_id=None)

    Args:
        model:      Model override
        provider:   Provider (openai, anthropic, etc.)
        quiet:      Suppress non-essential output (default: True)
        session_id: Session ID for persistence (auto-generated if not set)
        timeout:    Seconds to wait for completion (default: 600)
    """

    def __init__(
        self,
        model: str = None,
        provider: str = None,
        quiet: bool = True,
        session_id: str = "auto",
        timeout: int = 600,
    ):
        self._model = model
        self._provider = provider
        self._quiet = quiet
        self._timeout = timeout
        self._turn_count = 0

        if session_id == "auto":
            self._session_id = f"harness-{uuid.uuid4().hex[:12]}"
        else:
            self._session_id = session_id

    def send(self, message: Message) -> str:
        import subprocess
        import os

        text = self._extract_text(message)
        images = self._extract_images(message)

        cmd = ["codex", "exec", "--full-auto"]

        if self._model:
            cmd.extend(["--model", self._model])
        if self._provider:
            cmd.extend(["-c", f"model_provider={self._provider}"])

        # Image support via --image flag
        for img_path in images:
            cmd.extend(["--image", img_path])

        cmd.append(text)

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._timeout, env=os.environ,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Codex failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        self._turn_count += 1
        return result.stdout.strip()

    def reset(self):
        """Start a new session."""
        self._session_id = f"harness-{uuid.uuid4().hex[:12]}"
        self._turn_count = 0

    @property
    def history_length(self) -> int:
        return self._turn_count

    @staticmethod
    def _extract_text(message: Message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return message.get("text", str(message))
        if isinstance(message, list):
            texts = [p.get("text", "") for p in message
                     if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(texts) if texts else str(message)
        return str(message)

    @staticmethod
    def _extract_images(message: Message) -> list[str]:
        """Extract image file paths from message."""
        if isinstance(message, dict):
            return message.get("images", [])
        return []


# ==================================================================
# Generic CLI Session
# ==================================================================

class CLISession(Session):
    """
    Generic CLI agent session via subprocess.

    Each send() runs the command. Stateless by default — no history.

    Args:
        command:    Command template. Use {message} for input placeholder.
        timeout:    Seconds to wait
        env:        Additional environment variables
    """

    def __init__(self, command: str, timeout: int = 300, env: dict = None):
        self._command = command
        self._timeout = timeout
        self._env = env

    def send(self, message: Message) -> str:
        import subprocess
        import os

        text = self._extract_text(message)
        env = dict(os.environ)
        if self._env:
            env.update(self._env)

        if "{message}" in self._command:
            escaped = text.replace("'", "'\\''")
            cmd = self._command.replace("{message}", escaped)
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=self._timeout, env=env,
            )
        else:
            result = subprocess.run(
                self._command, shell=True, input=text, capture_output=True,
                text=True, timeout=self._timeout, env=env,
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"CLI failed (exit {result.returncode}): {result.stderr[:500]}"
            )
        return result.stdout.strip()

    @staticmethod
    def _extract_text(message: Message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return message.get("text", str(message))
        if isinstance(message, list):
            texts = [p.get("text", "") for p in message
                     if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(texts) if texts else str(message)
        return str(message)


# ==================================================================
# Gateway Session
# ==================================================================

class OpenClawSession(Session):
    """
    Routes messages through an OpenClaw gateway's OpenAI-compatible endpoint.

    Uses /v1/chat/completions (must be enabled in gateway config).
    Supports text and images via the OpenAI content format.
    Maintains conversation history for session continuity.

    Args:
        gateway_url:    OpenClaw gateway URL (default: http://localhost:18789)
        auth_token:     Gateway auth token (or OPENCLAW_GATEWAY_TOKEN env var)
        agent_id:       Agent to target (default: "default")
        model_override: Override the backend model
        session_key:    Session key for routing (auto-generated if "auto")
        max_tokens:     Max reply tokens
        timeout:        Request timeout in seconds
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:18789",
        auth_token: str = None,
        agent_id: str = "default",
        model_override: str = None,
        session_key: str = "auto",
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._agent_id = agent_id
        self._model_override = model_override
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._turn_count = 0
        self._history: list[dict] = []

        if session_key == "auto":
            self._session_key = f"harness-{uuid.uuid4().hex[:12]}"
        else:
            self._session_key = session_key

        # Auth
        import os
        self._auth_token = auth_token or os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

    def send(self, message: Message) -> str:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required: pip install httpx")

        content = self._to_openai_content(message)
        self._history.append({"role": "user", "content": content})

        # Build request
        headers = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._model_override:
            headers["x-openclaw-model"] = self._model_override
        if self._session_key:
            headers["x-openclaw-session-key"] = self._session_key

        payload = {
            "model": f"openclaw/{self._agent_id}",
            "messages": self._history,
            "max_tokens": self._max_tokens,
        }

        response = httpx.post(
            f"{self._gateway_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()

        data = response.json()
        reply = data["choices"][0]["message"]["content"]
        self._history.append({"role": "assistant", "content": reply})

        self._turn_count += 1
        return reply

    def reset(self):
        """Start a new session."""
        self._session_key = f"harness-{uuid.uuid4().hex[:12]}"
        self._history = []
        self._turn_count = 0

    @property
    def history_length(self) -> int:
        return self._turn_count

    @staticmethod
    def _to_openai_content(message: Message):
        """Convert to OpenAI content format (same as OpenAISession)."""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return message
        if isinstance(message, dict):
            parts = []
            if "text" in message:
                parts.append({"type": "text", "text": message["text"]})
            if "images" in message:
                import base64
                for img_path in message["images"]:
                    with open(img_path, "rb") as f:
                        data = base64.standard_b64encode(f.read()).decode()
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    media_type = {
                        "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "gif": "image/gif",
                        "webp": "image/webp",
                    }.get(ext, "image/png")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"}
                    })
            return parts if parts else message
        return message
