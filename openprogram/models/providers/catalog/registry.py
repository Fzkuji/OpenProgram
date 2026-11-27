"""
openprogram.models.providers.catalog.registry — the built-in providers and detection.

    from openprogram.models.providers.catalog.registry import detect_provider

    provider, model = detect_provider()     # auto-detect best available

``PROVIDERS`` maps ONLY the built-in backends. Building a Runtime from
them is the runtime's job: ``openprogram.runtime.agent.runtime.factory``
(``create_runtime``) reads this table, the detection below and
:func:`http_api_key_for`. Each is a plain API-key HTTP provider served by
the base ``Runtime(model="<namespace>:<model>")`` with the key resolved
here. A subscription is used by running the vendor's own program as an
outside-agent harness (``openprogram.runtime.harnesses``), not here. Every other provider is served the same base-Runtime
way through the api_registry — the same path the chat dispatcher uses.
"""

import os


# -- Provider registry -------------------------------------------------------

# The built-in provider ids, each a plain API-key HTTP provider built as
#       base ``Runtime(model="<namespace>:<model>", api_key=...)``. The
#       namespace is the registry prefix the models live under (the ``gemini``
#       provider streams ``google:<id>`` models); ``credential`` names the
#       AuthStore pool the key resolves from; ``credential_error`` is the
#       message raised when no key is anywhere.
#
# "default_model" is the fallback when neither caller nor config picks one.
PROVIDERS = {
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
      3. AuthStore API keys (anthropic → openai → google)

    Returns:
        (provider_name, default_model) — e.g. ("anthropic", "claude-sonnet-4-6")

    Raises:
        RuntimeError if no provider is found.
    """
    # 1-2. User config (env vars or config file)
    result = _load_provider_config()
    if result:
        return result

    # 3. API providers — a key saved in the AuthStore (settings UI or
    #    ``openprogram providers login <provider> --api-key``).
    from openprogram.models.auth.env_api_keys import is_configured
    if is_configured("anthropic"):
        return "anthropic", "claude-sonnet-4-6"
    if is_configured("openai"):
        return "openai", "gpt-4.1"
    if is_configured("google"):
        return "gemini", "gemini-2.5-flash"

    raise RuntimeError(
        "No LLM provider found. Add an API key (stored under ~/.openprogram):\n"
        "\n"
        "    Web UI:   Settings -> Model Services -> pick one -> add a key\n"
        "    CLI:      openprogram providers login <provider> --api-key\n"
        "                 (e.g. openprogram providers login deepseek --api-key)\n"
        "\n"
    )


def check_providers() -> dict:
    """Check availability of all providers.

    Returns a dict with status of each provider:
        {
            "openai": {"available": True, "method": "API", "model": "gpt-4.1"},
            ...
        }
    """
    results = {}
    api_checks = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": ["GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"],
    }

    # Availability via the canonical resolver (AuthStore / cloud chain).
    # The status names here map to the canonical provider ids
    # ("gemini" status row → "google").
    from openprogram.models.auth.env_api_keys import is_configured
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
    """The key a request of a ``model_namespace`` PROVIDERS entry would use
    now, or ``None`` (API-key shaped credentials only; Anthropic takes API
    keys only, AGENTS.md)."""
    from openprogram.models.auth.env_api_keys import resolve_provider_key
    return resolve_provider_key(entry["credential"])


__all__ = [
    "PROVIDERS",
    "detect_provider",
    "check_providers",
    "http_api_key_for",
]
