"""
Environment variable API key resolution — mirrors packages/ai/src/env-api-keys.ts
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


def get_env_api_key(provider: str) -> str | None:
    """Resolve a provider's API key — the runtime path.

    Delegates to the canonical :func:`resolve_api_key` (env > config.json), so a
    key saved through the web UI is found at runtime even after a worker restart
    cleared it from ``os.environ`` — the restart bug. Anthropic OAuth>key and
    the GitHub-Copilot token sources are handled by the canonical resolver's
    env-var table. Bedrock/Vertex keep the ``"<authenticated>"`` cloud-credential
    sentinel for back-compat with their runtime adapters (those migrate to
    :func:`is_configured` separately).
    """
    if provider == "amazon-bedrock":
        return "<authenticated>" if _bedrock_chain_ok() else None
    if provider == "google-vertex":
        return "<authenticated>" if _vertex_adc_ok() else None
    return resolve_api_key(provider)


# ─────────────────────────────────────────────────────────────────────────────
# Canonical credential resolution (see
# docs/design/providers/api-key-resolution-unification.md).
#
# One table of accepted env-var names per provider (precedence order), one
# resolver (env → config.json), one is_configured (incl. cloud-cred chains),
# one reverse map. Every other resolver/map in the codebase becomes a thin
# wrapper over these. Added additively in step 1 — callers migrate in later
# steps; the legacy ``PROVIDER_ENV_VARS`` + ``get_env_api_key`` above stay until
# then.
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
# bearer key — resolve_api_key returns None for them; is_configured carries the
# answer (the logic that used to mint the "<authenticated>" sentinel).
_CLOUD_CRED_PROVIDERS = frozenset({"amazon-bedrock", "google-vertex"})


def env_vars_for(provider_id: str) -> list[str]:
    """Accepted env-var names for ``provider_id``, in precedence order.

    Single source of truth, replacing ``PROVIDER_ENV_VARS`` /
    ``_model_catalog.providers._ENV_API_KEYS`` / the inline maps.

    A provider not in the static map above is a community / models.dev
    one (e.g. ``minimax-cn-coding-plan``). Its env-var name lives in the
    models.dev catalogue — the same name the settings UI shows as "API
    key env: …" and stores the saved key under. Fall back to it so that
    key resolves at RUNTIME too, not just in the UI; without this the
    model registers + lists fine but every turn fails auth. Lazy +
    guarded so this lower layer stays import-free at load time."""
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


# Config-read cache keyed by file mtime — keeps resolve_api_key off the
# filesystem on the per-stream hot path while still picking up a freshly-saved
# key without a restart.
_config_cache: dict = {"mtime": None, "api_keys": {}}


def _config_api_keys() -> dict:
    try:
        from openprogram.paths import get_config_path
        path = get_config_path()
        mtime = path.stat().st_mtime
    except Exception:
        return {}
    if _config_cache["mtime"] != mtime:
        try:
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
            _config_cache["api_keys"] = (data.get("api_keys") or {})
        except Exception:
            _config_cache["api_keys"] = {}
        _config_cache["mtime"] = mtime
    return _config_cache["api_keys"]


def resolve_api_key(provider_id: str, *, allow_config: bool = True) -> str | None:
    """The real, usable key/token for ``provider_id``, or ``None``.

    Order: each env var in :func:`env_vars_for` (first hit wins), then — when
    ``allow_config`` — the same names under config.json ``api_keys`` (cached).
    Cloud-credential providers (Bedrock/Vertex) return ``None`` here; their
    state is :func:`is_configured`, not a key. Never returns a sentinel.

    The config fallback is what fixes the restart bug: a key saved through the
    web UI lives in config.json but is absent from ``os.environ`` after a worker
    restart — runtime resolution now finds it regardless."""
    if provider_id in _CLOUD_CRED_PROVIDERS:
        return None
    names = env_vars_for(provider_id)
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    if allow_config:
        cfg = _config_api_keys()
        for name in names:
            val = cfg.get(name)
            if val:
                return val
    return None


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
    """True when the provider has working credentials — a resolvable key OR a
    satisfied cloud-credential chain (Bedrock AWS chain / Vertex ADC). Replaces
    the scattered ``bool(_get_api_key(env))`` / ``_is_configured`` presence
    checks."""
    if resolve_api_key(provider_id) is not None:
        return True
    if provider_id == "amazon-bedrock":
        return _bedrock_chain_ok()
    if provider_id == "google-vertex":
        return _vertex_adc_ok()
    return False


def provider_id_for_env_var(env_var: str) -> str | None:
    """Reverse of :func:`env_vars_for` — the provider an env-var name belongs to
    (first match in table order, so the canonical owner of a shared name wins:
    ``GEMINI_API_KEY`` → ``google``, ``KIMI_API_KEY`` → ``kimi-coding``).
    Returns ``None`` for non-LLM keys (search providers etc.)."""
    for pid, names in _PROVIDER_ENV_VARS.items():
        if env_var in names:
            return pid
    return None

