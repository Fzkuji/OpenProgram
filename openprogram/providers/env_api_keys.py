"""
Provider API-key resolution.

LLM provider credentials live in exactly ONE place: the AuthStore under
``~/.openprogram`` — written by the settings UI ("add a key") and by the
CLI (``openprogram auth login <provider> --api-key``). Environment
variables are NOT consulted; there is no config.json fallback.

The two exceptions are Amazon Bedrock and Google Vertex, whose
authentication is the cloud SDK's own credential chain (AWS SigV4 /
GCP ADC) rather than a bearer key.

The env-var NAME tables below are retained purely as display labels /
identifiers for the UI and models.dev catalogue — they are no longer
read from ``os.environ``.
"""
from __future__ import annotations

import os
from pathlib import Path

# Maps provider name → environment variable name
PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "gemini-subscription": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",  # Changed from VERCEL_AI_GATEWAY_API_KEY
    "azure-openai-responses": "AZURE_OPENAI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "zai": "ZAI_API_KEY",
    "huggingface": "HF_TOKEN",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "opencode-go": "OPENCODE_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def resolve_provider_key(provider: str) -> str | None:
    """Resolve a provider's API key — the runtime path.

    Single source: the AuthStore (settings UI "add a key", or
    ``openprogram auth login <provider> --api-key``). It takes
    api-key-shaped credentials ONLY — OAuth tokens are excluded because
    the wires that call this put the result in ``x-api-key`` /
    ``Authorization: Bearer <key>`` headers, and an OAuth access_token
    there just 401s; OAuth providers have their own transports
    (claude-code daemon, codex OAuth). Bedrock/Vertex return the
    ``"<authenticated>"`` cloud-credential sentinel when the AWS / GCP
    SDK credential chain is satisfied — those have no bearer key.
    """
    if provider == "amazon-bedrock":
        return "<authenticated>" if _bedrock_chain_ok() else None
    if provider == "google-vertex":
        return "<authenticated>" if _vertex_adc_ok() else None
    try:
        from openprogram.auth.resolver import resolve_store_api_key_sync
        return resolve_store_api_key_sync(provider)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Env-var NAME tables — display labels / identifiers only.
#
# The settings UI shows "API key: DEEPSEEK_API_KEY" style labels and the
# models.dev catalogue keys community providers by these names. They are
# NOT read from ``os.environ``: provider keys resolve from the AuthStore
# exclusively (see resolve_provider_key above). config.json ``api_keys``
# remains in use only by the web-search / TTS key flows (their own storage).
# ─────────────────────────────────────────────────────────────────────────────

# provider_id → accepted env-var names, in precedence order. Merges the four
# previously-divergent maps; multi-name entries reconcile real conflicts:
#   google      — three historical names (configs store any of them)
#   anthropic   — OAuth token wins over the API key
#   minimax-cn  — runtime used MINIMAX_CN_API_KEY, webui used MINIMAX_API_KEY
#   kimi-coding — runtime used KIMI_API_KEY, webui used MOONSHOT_API_KEY
#   github-copilot — three token sources
_PROVIDER_ENV_VARS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"],
    "gemini-subscription": ["GEMINI_API_KEY"],
    "github-copilot": ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"],
    "groq": ["GROQ_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "vercel-ai-gateway": ["AI_GATEWAY_API_KEY"],
    "azure-openai-responses": ["AZURE_OPENAI_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
    "zai": ["ZAI_API_KEY"],
    "huggingface": ["HF_TOKEN"],
    "minimax": ["MINIMAX_API_KEY"],
    "minimax-cn": ["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"],
    # MiniMax "Token Plan" (the coding-plan subscription) is the SAME account
    # and SAME API key as the region's pay-as-you-go provider above — MiniMax
    # has no separate subscription login. Map each Token Plan to its region
    # sibling's env(s) so: (1) the key resolves at runtime offline too (the
    # models.dev fallback in env_vars_for returns [] on a cold cache, which
    # otherwise broke auth), and (2) the web form classifies them as api-key
    # providers (add_mode) and shows the key field.
    "minimax-coding-plan": ["MINIMAX_API_KEY"],
    "minimax-cn-coding-plan": ["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"],
    "opencode": ["OPENCODE_API_KEY"],
    "opencode-go": ["OPENCODE_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
    "kimi": ["KIMI_API_KEY"],
    "moonshot": ["MOONSHOT_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
}

# Providers whose credential is a cloud-credential chain (SigV4 / ADC), not a
# bearer key — resolve_provider_key returns the "<authenticated>" sentinel
# for them when the chain is satisfied.
_CLOUD_CRED_PROVIDERS = frozenset({"amazon-bedrock", "google-vertex"})


def env_vars_for(provider_id: str) -> list[str]:
    """Key-name labels for ``provider_id`` (display / identifier use only —
    never read from ``os.environ``).

    A provider not in the static map above is a community / models.dev
    one (e.g. ``minimax-cn-coding-plan``); its name lives in the
    models.dev catalogue. Lazy + guarded so this lower layer stays
    import-free at load time."""
    names = list(_PROVIDER_ENV_VARS.get(provider_id, []))
    if names:
        return names
    try:
        from openprogram.webui._model_catalog.providers import _env_var_for
        ev = _env_var_for(provider_id)
        if ev:
            return [ev]
    except Exception:
        pass
    return []




def _bedrock_chain_ok() -> bool:
    return bool(
        os.environ.get("AWS_PROFILE")
        or (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    )


def _vertex_adc_ok() -> bool:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not project or not location:
        return False
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit and Path(explicit).is_file():
        return True
    default_adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    return default_adc.is_file()


def is_configured(provider_id: str) -> bool:
    """True when the provider has working credentials — an AuthStore key OR a
    satisfied cloud-credential chain (Bedrock AWS chain / Vertex ADC)."""
    return resolve_provider_key(provider_id) is not None


def provider_id_for_env_var(env_var: str) -> str | None:
    """Reverse of :func:`env_vars_for` — the provider an env-var name belongs to
    (first match in table order, so the canonical owner of a shared name wins:
    ``GEMINI_API_KEY`` → ``google``, ``KIMI_API_KEY`` → ``kimi-coding``).
    Returns ``None`` for non-LLM keys (search providers etc.)."""
    for pid, names in _PROVIDER_ENV_VARS.items():
        if env_var in names:
            return pid
    return None

