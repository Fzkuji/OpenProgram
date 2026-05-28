"""Provider-level metadata: display labels, env-var mappings, default
API routes, configured-status detection.

All static tables in one place so adding a new provider only touches
this module + a fetcher + (optionally) a setup hint. The functions
``_label`` / ``_is_configured`` keep the same names they had in the
old monolith so import sites that grab them directly keep working.
"""
from __future__ import annotations

import shutil
from typing import Any


# Display labels for provider ids. Anything not listed falls back to
# prettified id ("amazon-bedrock" -> "Amazon Bedrock").
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
    "minimax": "openai-completions",
    "minimax-cn": "openai-completions",
    "vercel-ai-gateway": "openai-completions",
    "opencode": "openai-completions",
    "github-copilot": "openai-completions",
    "xai": "openai-completions",
    "zai": "openai-completions",
    "deepseek": "openai-completions",
}


def _prettify(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _label(provider_id: str) -> str:
    return _PROVIDER_LABELS.get(provider_id, _prettify(provider_id))


def _is_configured(provider_id: str) -> bool:
    """Is this provider usable (key present, CLI binary found, or local
    daemon responding).

    Three shapes of detection:

    * CLI-backed providers — presence of their binary on PATH.
    * Two special-case providers — ``openai-codex`` (looks for
      ``~/.codex/auth.json``) and ``claude-code`` (HEAD against the
      Meridian / claude-max-api daemon's health endpoint).
    * Everything else — env-var key set, or non-empty
      ``~/.agentic/config.json :: api_keys.<env_var>`` row.
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
    env = _ENV_API_KEYS.get(provider_id)
    if env is None:
        return True  # assume true for providers without a standard key var
    from openprogram.webui.server import _get_api_key  # re-use helper
    return bool(_get_api_key(env))
