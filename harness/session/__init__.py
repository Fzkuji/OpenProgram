"""
Session — the pluggable runtime for Function execution.

Analogous to a language runtime (CPython, JVM, V8):
    - The Session is what actually "runs" a Function
    - Any class that implements send(message: str) -> str can be a Session
    - The framework has no opinion on which LLM or platform you use

Built-in implementations:
    - AnthropicSession   direct Anthropic API
    - OpenClawSession    routes through OpenClaw agent
    - NanobotSession     routes through nanobot agent

To add a new platform, implement the Session interface:

    class MySession(Session):
        def send(self, message: str) -> str:
            # call your platform here
            return reply
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class Session(ABC):
    """
    The runtime interface for Function execution.

    A Session is anything that can:
        1. Receive a message (string)
        2. Return a reply (string)

    The Session is responsible for:
        - Maintaining its own conversation history
        - Managing its own connection and authentication
        - Returning complete (not streamed) replies

    The Session is NOT responsible for:
        - Parsing return values (Function handles that)
        - Tool execution (the Session's environment handles that)
        - Retry logic (Function handles that)
    """

    @abstractmethod
    def send(self, message: str) -> str:
        """
        Send a message and return the reply.

        Args:
            message: The assembled Function call message

        Returns:
            The LLM's reply as a plain string
        """
        pass


class AnthropicSession(Session):
    """
    Direct Anthropic API session.

    Full control over the LLM call — best for Functions that need
    precise output control or access to tool_choice.
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
        """Clear conversation history to start a fresh session."""
        self._history = []


class OpenClawSession(Session):
    """
    Routes messages through an OpenClaw agent.

    Benefits from OpenClaw's persistent memory, tools, and context.
    Useful for Functions that need access to prior conversation history
    or OpenClaw's built-in capabilities.

    Requires OpenClaw gateway to be running:
        openclaw gateway
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

    Requires nanobot gateway to be running:
        nanobot gateway
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
