"""
openprogram.models.providers.catalog.registry — the built-in providers and detection.

    from openprogram.models.providers.catalog.registry import detect_provider

    provider, model = detect_provider()     # auto-detect best available

``PROVIDERS`` maps ONLY the built-in backends. Building a Runtime from
them is the runtime's job: ``openprogram.runtime.agent.runtime.factory``
(``create_runtime``) reads this table, the detection below and
:func:`http_api_key_for`. The subscription/CLI-credential backends are
served by a dedicated Runtime class registered in
``openprogram.runtime.agent.provider_runtimes``; the plain API-key HTTP
providers by the base ``Runtime(model="<namespace>:<model>")`` with the
key resolved here. Every other provider is served the same base-Runtime
way through the api_registry — the same path the chat dispatcher uses.
"""

import os
import shutil


# -- Provider registry -------------------------------------------------------

# The built-in provider ids. Two entry shapes:
#
#   "model_namespace" only — subscription/CLI-credential backends whose auth
#       adoption + per-provider headers need a dedicated Runtime subclass; the
#       class is registered in the runtime layer's ``PROVIDER_RUNTIMES``
#       table (openprogram.runtime.agent.provider_runtimes).
#   "model_namespace" + "credential" — plain API-key HTTP providers built as
#       base ``Runtime(model="<namespace>:<model>", api_key=...)``. The
#       namespace is the registry prefix the models live under (the ``gemini``
#       provider streams ``google:<id>`` models); ``credential`` names the
#       AuthStore pool the key resolves from; ``credential_error`` is the
#       message raised when no key is anywhere.
#
# "default_model" is the fallback when neither caller nor config picks one.
PROVIDERS = {
    "openai-codex": {
        "model_namespace": "openai-codex",
        "default_model": "gpt-5.5",
    },
    "gemini-cli": {
        "model_namespace": "gemini-subscription",
        "default_model": "gemini-2.5-flash",
    },
    "anthropic": {
        "model_namespace": "anthropic",
        "credential": "anthropic",
        "credential_error": (
            "No Anthropic credential. Add an API key in Settings → "
            "Providers or pass api_key=."
        ),
        "default_model": "claude-sonnet-4-6",
    },
    "openai": {
        "model_namespace": "openai",
        "credential": "openai",
        "credential_error": (
            "OpenAI API key is required. Add one in Settings → "
            "Providers (or `openprogram providers login openai "
            "--api-key`), or pass api_key=."
        ),
        "default_model": "gpt-4.1",
    },
    "gemini": {
        "model_namespace": "google",
        "credential": "google",
        "credential_error": (
            "Google API key is required. Add one in Settings → "
            "Providers (or `openprogram providers login google "
            "--api-key`), or pass api_key=."
        ),
        "default_model": "gemini-2.5-flash",
    },
}


def _detect_caller_env() -> tuple[str, str] | None:
    """Detect if we're running inside a known LLM agent environment.

    Returns (provider, model) if detected, None otherwise.
    """
    # Running inside Codex CLI?
    if os.environ.get("CODEX_CLI") or os.environ.get("CODEX_SANDBOX_TYPE"):
        if shutil.which("codex"):
            return "openai-codex", None

    return None


def _load_provider_config() -> tuple[str, str] | None:
    """Load provider preference from env vars or ~/.openprogram/config.json.

    Priority: env vars > config file.
    Returns (provider, model) if configured, None otherwise.
    """
    # Environment variables
    provider = os.environ.get("AGENTIC_PROVIDER")
    model = os.environ.get("AGENTIC_MODEL")
    if provider:
        default_model = PROVIDERS.get(provider, {}).get("default_model")
        return provider, model or default_model

    # Config file
    try:
        from openprogram.state.paths import get_config_path
        config_path = get_config_path()
        import json
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        from openprogram.models.providers.catalog.last_used import last_used_in_config
        last = last_used_in_config(config)
        if last:
            return last
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return None


def detect_provider() -> tuple[str, str]:
    """Auto-detect the best available LLM provider.

    Detection priority:
      1. Env vars (AGENTIC_PROVIDER / AGENTIC_MODEL)
      2. Config file (~/.openprogram/config.json → last_used_model)
      3. Caller environment (inside Claude Code? Codex? → use the same)
      4. Available CLI providers (claude → codex → gemini)
      5. AuthStore API keys (anthropic → openai → google)

    Returns:
        (provider_name, default_model) — e.g. ("anthropic", "claude-sonnet-4-6")

    Raises:
        RuntimeError if no provider is found.
    """
    # 1-2. User config (env vars or config file)
    result = _load_provider_config()
    if result:
        return result

    # 3. Caller environment detection
    result = _detect_caller_env()
    if result:
        return result

    # 4. CLI providers (no API key needed)
    if shutil.which("codex"):
        return "openai-codex", None
    if shutil.which("gemini"):
        return "gemini-cli", "gemini-2.5-flash"

    # 5. API providers — a key saved in the AuthStore (settings UI or
    #    ``openprogram providers login <provider> --api-key``).
    from openprogram.models.providers.settings.env_api_keys import is_configured
    if is_configured("anthropic"):
        return "anthropic", "claude-sonnet-4-6"
    if is_configured("openai"):
        return "openai", "gpt-4.1"
    if is_configured("google"):
        return "gemini", "gemini-2.5-flash"

    raise RuntimeError(
        "No LLM provider found. Set up one of the following:\n"
        "\n"
        "  CLI providers (no API key needed):\n"
        "    1. Codex CLI:        npm install -g @openai/codex && codex auth\n"
        "    2. Gemini CLI:       npm install -g @google/gemini-cli\n"
        "\n"
        "  API providers (paste a key — stored under ~/.openprogram):\n"
        "    3. Web UI:   Settings -> LLM Providers -> pick one -> add a key\n"
        "    4. CLI:      openprogram providers login <provider> --api-key\n"
        "                 (e.g. openprogram providers login deepseek --api-key)\n"
        "\n"
    )


def check_providers() -> dict:
    """Check availability of all providers.

    Returns a dict with status of each provider:
        {
            "openai-codex": {"available": True, "method": "CLI", "model": "gpt-5.5"},
            "openai": {"available": True, "method": "API", "model": "gpt-4.1"},
            ...
        }
    """
    results = {}
    cli_checks = {
        "openai-codex": "codex",
        "gemini-cli": "gemini",
    }
    api_checks = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": ["GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"],
    }

    for name, binary in cli_checks.items():
        results[name] = {
            "available": shutil.which(binary) is not None,
            "method": "CLI",
            "model": PROVIDERS[name]["default_model"],
        }

    # Availability via the canonical resolver (AuthStore / cloud chain).
    # The status names here map to the canonical provider ids
    # ("gemini" status row → "google").
    from openprogram.models.providers.settings.env_api_keys import is_configured
    _canon = {"gemini": "google"}
    for name in api_checks:
        results[name] = {
            "available": is_configured(_canon.get(name, name)),
            "method": "API",
            "model": PROVIDERS[name]["default_model"],
        }

    # Mark which one would be auto-selected
    try:
        detected, _ = detect_provider()
        if detected in results:
            results[detected]["default"] = True
    except RuntimeError:
        pass

    return results


def http_api_key_for(entry: dict) -> str | None:
    """Resolve the API key for a ``model_namespace`` PROVIDERS entry.

    Anthropic goes through the unified resolver — a plain api-key OR a
    subscription OAuth token (``sk-ant-oat``, from a Claude Code login
    adopted into the AuthStore); the wire switches to Bearer + Claude
    Code beta headers for OAuth tokens. The other pools take api-key-
    shaped credentials only (an OAuth token in a Bearer header 401s).
    """
    pool = entry["credential"]
    if pool == "anthropic":
        from openprogram.models.auth.resolver import resolve_api_key_sync
        return resolve_api_key_sync(pool)
    from openprogram.models.providers.settings.env_api_keys import resolve_provider_key
    return resolve_provider_key(pool)


__all__ = [
    "PROVIDERS",
    "detect_provider",
    "check_providers",
    "http_api_key_for",
]
