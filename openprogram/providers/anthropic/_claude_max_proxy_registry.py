"""Register the Claude models under the ``claude-code`` provider.

Mirrors :mod:`_claude_code_registry`: the curated model list from
``claude_models.json`` is the source of truth, but here every entry is
attached to ``provider="claude-code"`` and to a ``base_url`` that
points at the local Claude Max HTTP proxy daemon (``meridian`` by
default; the older ``claude-max-api-proxy`` works on the same port for
text-only flows). The runtime :class:`ClaudeCodeRuntime` consumes those
models via the standard anthropic API client, so OpenProgram's tool
registry actually wires through to Claude — unlike the CLI provider
where tools come baked into the subprocess.
"""
from __future__ import annotations

import os


# Default daemon port for both meridian and the older claude-max-api-proxy.
# Both packages listen on 3456 unless reconfigured. Override via env if
# the user moved it.
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


def meridian_profile() -> str | None:
    """The Meridian profile OpenProgram's claude-code traffic is pinned to.

    Meridian (the local Claude proxy) can hold several Claude accounts as
    named profiles and routes each request to the one named by an
    ``x-meridian-profile`` header — its highest-priority profile selector,
    overriding both the keychain's current ``claude auth login`` session
    and Meridian's own active/default profile. Pinning one here is what
    decouples OpenProgram's Claude account from the terminal Claude Code
    login (see docs/design/claude-code-meridian-profile.md).

    Resolution order:
      1. ``config.providers.claude-code.meridian_profile`` — set manually
         in config.json today; a WebUI control to write it is P1 (planned).
         Read per request so a change takes effect live.
      2. env ``CLAUDE_MAX_PROXY_PROFILE`` (alias ``MERIDIAN_PROFILE``).
      3. ``None`` — no header; Meridian falls back to its active/default
         profile or the keychain login (unchanged legacy behaviour).

    No validation that the named profile actually exists in Meridian —
    that needs a Meridian profiles API we don't have. A bad name makes
    Meridian either error (surfaced as a normal API error) or silently use
    its default; the WebUI picker (P1) will constrain the value to known
    profiles so this can't happen via the UI.
    """
    try:
        from openprogram.setup import _read_config

        pcfg = (_read_config().get("providers") or {}).get("claude-code") or {}
        # str() guards a hand-edited non-string value (e.g. a bare number)
        # from raising inside .strip() and being swallowed below.
        val = str(pcfg.get("meridian_profile") or "").strip()
        if val:
            return val
    except Exception:
        # config unreadable (fresh install, race) — fall through to env.
        pass
    val = (
        os.environ.get("CLAUDE_MAX_PROXY_PROFILE")
        or os.environ.get("MERIDIAN_PROFILE")
        or ""
    ).strip()
    return val or None


def inject_profile_header(model, headers: dict | None) -> dict:
    """Return ``headers`` plus the pinned ``x-meridian-profile`` for
    claude-code, if one is configured and the caller didn't set it.

    Called from ``openai_completions.stream_simple`` — the single layer
    every claude-code request passes through (the ``providers/stream.py``
    wrapper is bypassed by some callers, e.g. memory summarization). The
    gate on ``provider == "claude-code"`` plus the fact that only
    ``api == "openai-completions"`` models reach openai_completions means a
    CLI-api claude-code model (different wire) never gets a meaningless
    header. A caller-supplied ``x-meridian-profile`` wins (it's more
    specific than the global config binding). Always returns a fresh dict.
    """
    out = dict(headers or {})
    if (
        getattr(model, "provider", None) == "claude-code"
        and "x-meridian-profile" not in out
    ):
        profile = meridian_profile()
        if profile:
            out["x-meridian-profile"] = profile
    return out


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
        key = f"claude-code/{mid}"
        if key in MODELS:
            continue
        family = m["family"]
        reasoning = family in ("opus", "sonnet")
        # No thinking picker for any claude-code (meridian /
        # claude-max-api-proxy) model. Both proxies forward through the
        # Claude Code SDK / CLI, which only takes prompt + model +
        # sessionId; `reasoning_effort` (and max_tokens, temperature,
        # tools) are silently dropped. An effort control here would be
        # dead UI — the budget is fixed at
        # Claude Code CLI's default.
        thinking_levels: list[str] = []
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
            thinking_levels=thinking_levels,
            default_thinking_level=None,
            # Cost figures are intentionally zero: the Max plan is
            # flat-rate at the human-account level, the proxy doesn't
            # bill per token. Showing 0 in the UI is more honest than
            # populating API-key prices that don't apply here.
            cost=ModelCost(),
        )


# DISABLED: claude-code now connects DIRECT to api.anthropic.com (anthropic
# subscription OAuth) and its model list comes from a live Fetch against
# Anthropic's /v1/models. This Meridian-proxy seed (openai-completions wire,
# localhost:3456, hardcoded 200K aliases like claude-opus-4) only duplicated
# and shadowed the real fetched models in the UI. The function is kept for
# reference / a possible Meridian fallback, but is no longer auto-run.
# _augment_registry_with_max_proxy_models()
