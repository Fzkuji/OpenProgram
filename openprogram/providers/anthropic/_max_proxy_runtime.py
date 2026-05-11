"""Claude Max API proxy adapter.

Anthropic's official ``claude`` CLI is the only sanctioned way to use
the "Claude Max" plan in agent contexts; the underlying account does
**not** expose a normal ``api.anthropic.com`` key. The community
workaround is the ``claude-max-api-proxy`` npm package — it runs a
local HTTP server that exposes an OpenAI-compatible
``/v1/chat/completions`` endpoint and shuttles traffic through a
logged-in Claude Code subprocess underneath.

Practical setup:

  # one-time install + login
  npm install -g @anthropic-ai/claude-code
  claude auth login

  # one-time proxy install + run
  npm install -g claude-max-api-proxy
  claude-max-api                      # binary name is `claude-max-api`,
                                      # NOT `claude-max-api-proxy`.

The daemon listens on ``http://localhost:3456`` by default. Override
with the ``CLAUDE_MAX_PROXY_URL`` env var if you ran it on a
different port.

Wire format gotcha: the proxy is OpenAI-compatible, NOT Anthropic
Messages. Our model registry attaches ``api='openai-completions'`` so
the standard stream layer routes through ``openai_completions``;
this Runtime class therefore just configures the modern
``Runtime(model="claude-max-proxy:<id>")`` path — no Anthropic SDK
involvement.
"""

from __future__ import annotations

import os
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


_DEFAULT_PROXY_URL = "http://localhost:3456"
_PLACEHOLDER_KEY = "claude-max-proxy"


def _resolve_base_url() -> str:
    val = os.environ.get("CLAUDE_MAX_PROXY_URL")
    return val.rstrip("/") if val else _DEFAULT_PROXY_URL


def _resolve_api_key() -> str:
    # The proxy ignores the key value (it routes via Claude Code's
    # OAuth), but the openai SDK still requires a non-empty string.
    return (
        os.environ.get("CLAUDE_MAX_PROXY_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or _PLACEHOLDER_KEY
    )


class ClaudeMaxProxyRuntime(Runtime):
    """Runtime that talks to ``claude-max-api-proxy`` (OpenAI-compatible).

    Drives the modern AgentSession path via ``model="claude-max-proxy:<id>"``.
    The model registry entry carries ``api='openai-completions'`` and
    ``base_url=http://localhost:3456/v1`` so the standard
    ``openai_completions.stream_simple`` picks the right wire format.

    Side effect: this constructor exports ``OPENAI_API_KEY`` if not
    already set, since the openai SDK reads it from the environment
    when no api_key is passed through the per-call provider config.
    The proxy ignores the value, but the SDK still requires one.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 2,
        base_url: Optional[str] = None,  # noqa: ARG002 — kept for API parity
        **_unused,
    ) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = api_key or _resolve_api_key()
        # Re-resolve base_url at construction so a freshly-changed env
        # var takes effect for runtimes built after the change. The
        # registry entry uses the *current* env-resolved URL too.
        url = (base_url or _resolve_base_url()).rstrip("/")
        if not url.endswith("/v1"):
            url = url + "/v1"
        os.environ.setdefault("CLAUDE_MAX_PROXY_RESOLVED_URL", url)
        super().__init__(model=f"claude-max-proxy:{model}", max_retries=max_retries)


__all__ = ["ClaudeMaxProxyRuntime"]
