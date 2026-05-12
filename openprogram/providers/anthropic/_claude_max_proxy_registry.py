"""Register the Claude models under the ``claude-max`` provider.

Mirrors :mod:`_claude_code_registry`: the curated model list from
``claude_models.json`` is the source of truth, but here every entry is
attached to ``provider="claude-code"`` and to a ``base_url`` that
points at the local ``claude-max-api-proxy`` daemon. The runtime
:class:`ClaudeCodeRuntime` consumes those models via the standard
anthropic API client, so OpenProgram's tool registry actually wires
through to Claude — unlike the CLI provider where tools come baked
into the subprocess.
"""
from __future__ import annotations

import os


# Default daemon port for ``claude-max-api-proxy`` v1.0.0. The package
# name has a ``-proxy`` suffix but the installed binary is just
# ``claude-max-api`` and listens on 3456 unless given a positional
# port argument. Override via env if the user moved it.
_DEFAULT_PROXY_URL = "http://localhost:3456"


def _proxy_base_url() -> str:
    """Return the ``/v1`` base URL the openai SDK should hit.

    Reads ``CLAUDE_MAX_PROXY_URL`` and guarantees a ``/v1`` suffix so
    the registry entry plugs straight into
    ``openai_completions.stream_simple`` (which does
    ``client.chat.completions.create(...)`` against ``<base>/chat/completions``).
    """
    val = os.environ.get("CLAUDE_MAX_PROXY_URL")
    base = (val or _DEFAULT_PROXY_URL).rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


# Only three model ids the ``claude-max-api`` proxy actually
# recognises (verified against ``GET /v1/models`` on v1.0.0). Anything
# else the proxy silently downgrades to ``claude-haiku-4``, so we
# don't expose internal version-suffixed names like
# ``claude-sonnet-4-6`` here — the user would think they picked
# Sonnet and silently get Haiku. Keep this hand-curated; the next
# proxy release that adds more ids should be reflected here.
# Display names carry the actual sub-version Anthropic's CLI alias
# currently routes to (verified by asking the model itself: the
# ``claude-opus-4`` alias self-identifies as ``claude-opus-4-7``,
# ``claude-sonnet-4`` as ``claude-sonnet-4-6``, ``claude-haiku-4``
# as ``claude-haiku-4-5``). The IDs stay as the bare aliases since
# that's what the proxy's ``/v1/chat/completions`` accepts — but
# the display name reflects the real version so users know which
# Claude they're actually talking to.
_PROXY_MODELS = [
    {
        "id": "claude-opus-4",
        "name": "Claude Opus 4.7",
        "family": "opus",
        "context_window": 200000,
        "max_tokens": 32000,
    },
    {
        "id": "claude-sonnet-4",
        "name": "Claude Sonnet 4.6",
        "family": "sonnet",
        "context_window": 200000,
        "max_tokens": 16000,
    },
    {
        "id": "claude-haiku-4",
        "name": "Claude Haiku 4.5",
        "family": "haiku",
        "context_window": 200000,
        "max_tokens": 16000,
    },
]


def _augment_registry_with_max_proxy_models() -> None:
    from openprogram.providers.models_generated import MODELS
    from openprogram.providers.types import Model, ModelCost

    base_url = _proxy_base_url()
    for m in _PROXY_MODELS:
        mid = m["id"]
        key = f"claude-max/{mid}"
        if key in MODELS:
            continue
        family = m["family"]
        reasoning = family in ("opus", "sonnet")
        MODELS[key] = Model(
            id=mid,
            name=m["name"],
            # Proxy speaks OpenAI Chat Completions wire format
            # (POST /v1/chat/completions). Routing this through
            # `anthropic-messages` would break — fields, streaming
            # event names, and tool-call schemas all differ.
            api="openai-completions",
            provider="claude-code",
            base_url=base_url,
            context_window=m["context_window"],
            max_tokens=m["max_tokens"],
            input=["text", "image"],
            reasoning=reasoning,
            # Cost figures are intentionally zero: the Max plan is
            # flat-rate at the human-account level, the proxy doesn't
            # bill per token. Showing 0 in the UI is more honest than
            # populating API-key prices that don't apply here.
            cost=ModelCost(),
        )


_augment_registry_with_max_proxy_models()
