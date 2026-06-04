"""Provider-level metadata: display labels, env-var mappings, default
API routes, configured-status detection.

Two-layer source of truth:

  1. **Community catalogue** (``sources.models_dev``) — 135 providers
     with normalised ``label`` / ``env_var`` / ``base_url`` / ``doc_url``.
     This is the *default* answer for any provider we don't have a
     manual override for. New providers on models.dev appear
     automatically the moment the in-process 1h TTL cache refreshes.

  2. **Manual overrides** below — only entries here that *differ from
     what models.dev says* genuinely need to live in code. The legacy
     full-set tables (21 providers, hand-maintained) collapsed down
     to the handful where we want to:
       * pin a friendlier label (e.g. "MiniMax (CN)" vs upstream's "Minimax"),
       * declare a provider that isn't on models.dev,
       * route a fetched row through a non-standard API id.

The public lookup functions (``_label``, ``_env_var_for``,
``_default_api_for``) check overrides first and fall through to
``sources.models_dev``. That's why the override dicts are short.
"""
from __future__ import annotations

import shutil
from typing import Any


# Display labels for provider ids. Anything not listed falls back to
# models.dev's ``name`` field, then a prettified id ("amazon-bedrock"
# -> "Amazon Bedrock") as a last resort.
_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "openai-codex": "OpenAI Codex",
    "anthropic": "Anthropic",
    "google": "Google AI",
    "gemini-subscription": "Gemini CLI",
    "azure-openai-responses": "Azure OpenAI",
    "amazon-bedrock": "Amazon Bedrock",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "cerebras": "Cerebras",
    "mistral": "Mistral",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax (CN)",
    "huggingface": "HuggingFace",
    "github-copilot": "GitHub Copilot",
    "kimi-coding": "Kimi Coding",
    "vercel-ai-gateway": "Vercel AI Gateway",
    "opencode": "OpenCode",
    "deepseek": "DeepSeek",
    # Claude via local HTTP proxy daemon (replaces the old Claude Code
    # CLI provider). Tools come from OpenProgram's own registry instead
    # of the CLI's built-ins.
    "claude-code": "Claude Code",
    # CLI-backed:
    "gemini-cli": "Gemini CLI",
}


# CLI-backed providers aren't in the HTTP provider registry. Currently
# empty: 'gemini-cli' historically lived here, but it shares one
# Runtime + auth flow with 'gemini-subscription' (the Cloud Code Assist
# endpoint), so listing it separately just duplicated the row. New
# CLI-only backends with no HTTP counterpart can be added back here.
_CLI_PROVIDERS: list[dict[str, Any]] = []


# Providers whose base URL speaks the OpenAI-compatible /v1/models
# listing (Bearer auth, standard {data:[{id:...}]} response). Everything
# else either has no public listing or uses a custom auth / response
# shape and so has a dedicated entry in ``fetchers._FETCHERS`` instead.
_FETCH_MODELS_PROVIDERS = frozenset({
    "openai",
    "openrouter",
    "groq",
    "cerebras",
    "mistral",
    "huggingface",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "vercel-ai-gateway",
    "deepseek",
    # Excluded deliberately:
    #   anthropic      — /v1/models uses x-api-key header, not Bearer
    #   google*        — custom endpoints / OAuth
    #   azure-*        — needs deployment name not model id
    #   amazon-bedrock — AWS SigV4
    #   openai-codex   — ChatGPT backend, no public listing (403)
    #   github-copilot — private OAuth with custom headers
    #   opencode       — not verified; add if/when tested
})


_ENV_API_KEYS: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "gemini-subscription": None,  # uses OAuth via gemini CLI
    "azure-openai-responses": "AZURE_OPENAI_API_KEY",
    "amazon-bedrock": None,  # AWS credentials chain
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_API_KEY",
    "huggingface": "HF_TOKEN",
    "github-copilot": None,  # OAuth
    "kimi-coding": "MOONSHOT_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "opencode": None,
    "openai-codex": None,  # OAuth via ~/.codex/auth.json
    "deepseek": "DEEPSEEK_API_KEY",
}


# Which registered API id each provider's models route through. The
# entries here must match strings registered in
# ``providers/register.py::register_builtins``. Used to stamp fetched /
# custom rows with a working ``api`` so the chat dispatcher can find a
# stream function for them — without this the fetch flow silently
# produces ``api: "custom"`` rows that the model picker happily lists
# but the chat path can't run, since the api registry has no "custom"
# entry. Fetched ``claude-sonnet-4-6`` looked usable in the UI but
# silently dropped out of ``/api/models/enabled`` for exactly this
# reason.
_PROVIDER_DEFAULT_API: dict[str, str] = {
    "anthropic": "anthropic-messages",
    "claude-code": "openai-completions",  # Meridian proxy speaks OpenAI Chat Completions
    "openai": "openai-completions",
    "openai-codex": "openai-codex",
    "google": "google-generative-ai",
    "gemini-subscription": "gemini-subscription",
    "amazon-bedrock": "bedrock-converse-stream",
    "azure-openai-responses": "azure-openai-responses",
    "openrouter": "openai-completions",
    "groq": "openai-completions",
    "cerebras": "openai-completions",
    "mistral": "openai-completions",
    "huggingface": "openai-completions",
    "kimi-coding": "openai-completions",
    # MiniMax ships its API in Anthropic Messages wire format (base_url
    # ends in /anthropic; see models_generated). Stamping fetched/custom
    # rows openai-completions sent chat to POST /chat/completions, which
    # 404s on api.minimax(i).com/anthropic — keep this in lockstep with
    # models_generated's api='anthropic-messages' so fetched rows run.
    "minimax": "anthropic-messages",
    "minimax-cn": "anthropic-messages",
    "vercel-ai-gateway": "openai-completions",
    "opencode": "openai-completions",
    "github-copilot": "openai-completions",
    "xai": "openai-completions",
    "zai": "openai-completions",
    "deepseek": "openai-completions",
}


def _prettify(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _models_dev_info(provider_id: str) -> dict[str, Any]:
    """Cached lookup against ``sources.models_dev.provider_info``.

    Lazy imported because ``sources`` is a sibling subpackage and a
    top-level import would form a circular-import path on cold start
    (sources → providers → sources). Always returns a dict to
    simplify call sites; ``{}`` on cache miss / network failure.
    """
    try:
        from .sources import models_dev
    except Exception:
        return {}
    try:
        return models_dev.provider_info(provider_id) or {}
    except Exception:
        return {}


def _label(provider_id: str) -> str:
    """Manual override → models.dev → prettified id. Manual override is
    only useful when we want a different display name from upstream
    (rare — most fall through to models.dev's clean ``name`` field)."""
    if provider_id in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider_id]
    md = _models_dev_info(provider_id)
    if md.get("label"):
        return md["label"]
    return _prettify(provider_id)


def _env_var_for(provider_id: str) -> str | None:
    """Env var name holding the API key for ``provider_id``. Same
    override-first semantics as ``_label`` — manual ``_ENV_API_KEYS``
    entry wins (so we can pin "claude-code → None" even if a future
    models.dev edit gives it an env), otherwise models.dev's first
    ``env[]`` entry, otherwise ``None``."""
    if provider_id in _ENV_API_KEYS:
        return _ENV_API_KEYS[provider_id]
    md = _models_dev_info(provider_id)
    return md.get("env_var")


def _default_api_for(provider_id: str) -> str | None:
    """Registered ``api`` id this provider's models dispatch through.
    Only the manual table is consulted today — models.dev exposes
    ``npm`` (the SDK) but not "which of *our* registered API stream
    functions handles this provider", which is OpenProgram-internal.
    Returns ``None`` when unmapped; callers default to
    ``"openai-completions"`` for unknown OpenAI-compatible providers."""
    return _PROVIDER_DEFAULT_API.get(provider_id)


def _default_base_url_for(provider_id: str) -> str | None:
    """Default API base URL for ``provider_id``. Pulled straight from
    models.dev's ``api`` field; static-registry ``Model.base_url`` is
    still preferred when the runtime has one baked in."""
    md = _models_dev_info(provider_id)
    return md.get("base_url")


def _doc_url_for(provider_id: str) -> str | None:
    """Provider docs URL (for the "Get an API key →" link in the
    setup hint). models.dev only — there's no manual table for this."""
    md = _models_dev_info(provider_id)
    return md.get("doc_url")


def _is_configured(provider_id: str) -> bool:
    """Is this provider usable (key present, CLI binary found, or local
    daemon responding).

    Three shapes of detection:

    * CLI-backed providers — presence of their binary on PATH.
    * Two special-case providers — ``openai-codex`` (looks for
      ``~/.codex/auth.json``) and ``claude-code`` (HEAD against the
      Meridian / claude-max-api daemon's health endpoint).
    * Everything else — env-var key set, or non-empty
      ``~/.openprogram/config.json :: api_keys.<env_var>`` row.
    """
    # CLI-backed: binary presence decides.
    for cli in _CLI_PROVIDERS:
        if cli["id"] == provider_id:
            return shutil.which(cli["cli_binary"]) is not None
    # openai-codex: reads ~/.codex/auth.json
    if provider_id == "openai-codex":
        from pathlib import Path
        return (Path.home() / ".codex" / "auth.json").exists()
    # claude-code: there's no env-key path; the daemon is "ready" when
    # its HTTP endpoint answers. Quick 0.5s probe — failure means the
    # user hasn't started ``meridian`` (or claude-max-api) yet.
    if provider_id == "claude-code":
        import os
        import urllib.error
        import urllib.request
        # Both meridian and claude-max-api-proxy default to port 3456.
        # Strip a trailing /v1 because /health lives at root.
        url = (
            os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456"
        ).rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        try:
            with urllib.request.urlopen(url + "/health", timeout=0.5):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            return False
    # Key-based providers (incl. the Bedrock/Vertex cloud-credential chains):
    # the canonical is_configured — env > config.json key, or a satisfied cloud
    # chain. See docs/design/providers/api-key-resolution-unification.md.
    from openprogram.providers.env_api_keys import (
        env_vars_for,
        is_configured,
        _config_api_keys,
    )
    if env_vars_for(provider_id) or provider_id in ("amazon-bedrock", "google-vertex"):
        return is_configured(provider_id)
    # Community / models.dev provider with a single env-var name we don't have
    # in the canonical table: check it (env > config), as before.
    env = _env_var_for(provider_id)
    if env is None:
        # OAuth / no-key / unknown — conservatively "configured" so the UI
        # doesn't show a red dot the user can't act on.
        return True
    import os
    return bool(os.environ.get(env) or _config_api_keys().get(env))
