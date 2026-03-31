"""
Session — the pluggable runtime for Step execution.

Any class that implements send(message: str) -> str can be a Session.
The framework has no opinion on which LLM or platform you use.

Built-in implementations:
    - AnthropicSession   direct Anthropic API
    - OpenClawSession    routes through OpenClaw agent
    - NanobotSession     routes through nanobot agent

To add a new platform, just implement the Session interface.
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class Session(ABC):
    """
    The runtime interface for Step execution.

    A Session is anything that can:
        1. Receive a message (string)
        2. Return a reply (string)

    The Session is responsible for:
        - Maintaining its own conversation history
        - Managing its own connection/authentication
        - Returning complete (not streamed) replies

    The Session is NOT responsible for:
        - Structured output parsing (the Step handles that)
        - Tool execution (the Session's environment handles that)
        - Retry logic (the Step handles that)
    """

    @abstractmethod
    def send(self, message: str) -> str:
        """
        Send a message and return the reply.

        Args:
            message: The assembled Step message

        Returns:
            The LLM's reply as a plain string
        """
        pass


class AnthropicSession(Session):
    """
    Direct Anthropic API session.
    Full control — best for steps that need force_tool or strict output control.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        system_prompt: str = "You are a helpful assistant that follows instructions precisely.",
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
        self._history = []

    def send(self, message: str) -> str:
        self._history.append({"role": "user", "content": message})

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


class OpenClawSession(Session):
    """
    Routes messages through an OpenClaw agent.
    Benefits from OpenClaw's memory, tools, and context.

    Requires OpenClaw gateway to be running:
        nanobot gateway  (or openclaw gateway)
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:18789",
        session_id: str = "default",
    ):
        self._gateway_url = gateway_url
        self._session_id = session_id

    def send(self, message: str) -> str:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required: pip install httpx")

        response = httpx.post(
            f"{self._gateway_url}/message",
            json={"message": message, "session_id": self._session_id},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()["reply"]


class NanobotSession(Session):
    """
    Routes messages through a nanobot agent.
    Lightweight alternative to OpenClaw.
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:18790",
        session_id: str = "default",
    ):
        self._gateway_url = gateway_url
        self._session_id = session_id

    def send(self, message: str) -> str:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required: pip install httpx")

        response = httpx.post(
            f"{self._gateway_url}/message",
            json={"message": message, "session_id": self._session_id},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()["reply"]
