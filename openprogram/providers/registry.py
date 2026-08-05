"""
openprogram.providers.registry — provider detection + the Runtime factory.

The canonical way to build a runtime:

    from openprogram.providers.registry import detect_provider, create_runtime

    provider, model = detect_provider()     # auto-detect best available
    rt = create_runtime()                   # runtime for the detected provider
    rt = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
    rt = create_runtime(provider="claude-code")   # Claude subscription OAuth
    rt = create_runtime(provider="deepseek", model="deepseek-chat")  # api-routed

``PROVIDERS`` maps ONLY the six first-class backends. Three (the
subscription/CLI-credential ones) carry a dedicated Runtime class in their
provider package; the other three (plain API-key HTTP providers) are served
by the base ``Runtime(model="<namespace>:<model>")`` with the key resolved
here — the factory stamps nothing extra because the base Runtime derives
its ``provider_id`` from the model namespace. Every other provider is
served the same base-Runtime way through the api_registry — the same path
the chat dispatcher uses.
"""

import os
import shutil


# -- Provider registry -------------------------------------------------------

# The six first-class provider ids. Two entry shapes:
#
#   "runtime_class": (module_path, class_name) — subscription/CLI-credential
#       backends whose auth adoption + per-provider headers need a dedicated
#       Runtime subclass.
#   "model_namespace" + "credential" — plain API-key HTTP providers built as
#       base ``Runtime(model="<namespace>:<model>", api_key=...)``. The
#       namespace is the registry prefix the models live under (the ``gemini``
#       provider streams ``google:<id>`` models); ``credential`` names the
#       AuthStore pool the key resolves from; ``credential_error`` is the
#       message raised when no key is anywhere.
#
# "default_model" is the fallback when neither caller nor config picks one.
PROVIDERS = {
    # Claude via a Claude subscription, connected DIRECT to
    # api.anthropic.com (Bearer OAuth + Claude Code beta headers) — same
    # shape as openai-codex direct-connecting to chatgpt.com. The wire is
    # the standard anthropic Messages API, which natively handles image
    # blocks (gui_agent multimodal preserved). The subscription token
    # resolves from the anthropic AuthStore pool.
    "claude-code": {
        "runtime_class": (
            "openprogram.providers.anthropic._claude_code_direct_runtime",
            "ClaudeCodeRuntime",
        ),
        "default_model": "claude-sonnet-4",
    },
    "openai-codex": {
        "runtime_class": (
            "openprogram.providers.openai_codex.runtime",
            "OpenAICodexRuntime",
        ),
        "default_model": "gpt-5.5",
    },
    "gemini-cli": {
        "runtime_class": (
            "openprogram.providers.google_gemini_cli.runtime",
            "GeminiCLIRuntime",
        ),
        "default_model": "gemini-2.5-flash",
    },
    "anthropic": {
        "model_namespace": "anthropic",
        "credential": "anthropic",
        "credential_error": (
            "No Anthropic credential. Add an API key in Settings → "
            "Providers, pass api_key=, or log in with a Claude "
            "subscription (claude login) so the OAuth token is adopted."
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
        from openprogram.paths import get_config_path
        config_path = get_config_path()
        import json
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        provider = config.get("default_provider")
        model = config.get("default_model")
        if provider:
            default_model = PROVIDERS.get(provider, {}).get("default_model")
            return provider, model or default_model
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return None


def detect_provider() -> tuple[str, str]:
    """Auto-detect the best available LLM provider.

    Detection priority:
      1. Env vars (AGENTIC_PROVIDER / AGENTIC_MODEL)
      2. Config file (~/.openprogram/config.json → default_provider / default_model)
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
    from openprogram.providers.env_api_keys import is_configured
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
        "  Claude via your Claude subscription (no API key):\n"
        "    5. Enable the claude-code provider in Settings -> LLM Providers,\n"
        "       then add a Claude account (the backend sets itself up):\n"
        "       openprogram providers claude-code accounts add\n"
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
    from openprogram.providers.env_api_keys import is_configured
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


def _api_routed_runtime(provider: str, model: str = None, **kwargs):
    """Build a Runtime for a provider that has no dedicated Runtime class
    (i.e. not in ``PROVIDERS``) but IS supported via its model's ``api``
    through the api_registry — every openai-/anthropic-compatible
    provider. The base ``Runtime("<provider>:<model>")`` resolves the
    model's wire api + base_url and streams via the registered api
    provider, the same path the chat dispatcher uses."""
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers.models import get_model
    from openprogram.providers.enabled_models import ENABLED_MODELS

    if not model:
        cands = [m for m in ENABLED_MODELS.values() if m.provider == provider]
        if cands:
            model = cands[0].id
    if not model:
        raise ValueError(
            f"Provider {provider!r} has no registered models — pass an "
            f"explicit model, or run `openprogram providers available "
            f"{provider}` / fetch its models first."
        )
    # Community / fetched models live in the user's config, not the static
    # registry — register so the model's api + base_url resolve here too
    # (mirrors the chat path's resolve_model).
    if get_model(provider, model) is None:
        try:
            from openprogram.providers.enabled_models import register_model_from_config
            register_model_from_config(provider, model)
        except Exception:
            pass
    return Runtime(model=f"{provider}:{model}", **kwargs)


def _http_api_key_for(entry: dict) -> str | None:
    """Resolve the API key for a ``model_namespace`` PROVIDERS entry.

    Anthropic goes through the unified resolver — a plain api-key OR a
    subscription OAuth token (``sk-ant-oat``, from a Claude Code login
    adopted into the AuthStore); the wire switches to Bearer + Claude
    Code beta headers for OAuth tokens. The other pools take api-key-
    shaped credentials only (an OAuth token in a Bearer header 401s).
    """
    pool = entry["credential"]
    if pool == "anthropic":
        from openprogram.auth.resolver import resolve_api_key_sync
        return resolve_api_key_sync(pool)
    from openprogram.providers.env_api_keys import resolve_provider_key
    return resolve_provider_key(pool)


def create_runtime(provider: str = None, model: str = None, **kwargs):
    """Create a Runtime instance with auto-detection or explicit provider.

    Args:
        provider:  Provider name (e.g. "anthropic", "claude-code",
                   "openai", "gemini-cli"). Pass "auto" or None to
                   auto-detect the best available provider via
                   detect_provider().
        model:     Model name override.
        **kwargs:  Forwarded to the Runtime constructor.

    Returns:
        A Runtime instance ready to use. Every runtime carries an
        authoritative ``provider_id`` attribute (derived from its model
        namespace, or set by the subscription runtime classes).
    """
    import importlib

    if provider and provider != "auto":
        if provider not in PROVIDERS:
            # ``PROVIDERS`` is NOT the list of supported providers — it
            # only holds the 6 first-class backends (claude-code,
            # openai-codex, gemini-cli, anthropic, openai, gemini). Every
            # other provider — deepseek, groq, openrouter, minimax, kimi,
            # everything models.dev knows — is supported through its
            # model's ``api`` + the api_registry, exactly how the chat
            # dispatcher streams them. Route those through the base
            # Runtime instead of failing, so create_runtime() matches
            # chat coverage.
            return _api_routed_runtime(provider, model, **kwargs)
        entry = PROVIDERS[provider]
    else:
        provider, detected_model = detect_provider()
        entry = PROVIDERS[provider]
        # detect_provider returns None for CLI providers ("we found
        # the binary but don't have an opinion on which model") — the
        # table default below covers that.
        model = model or detected_model

    use_model = model or entry["default_model"]

    runtime_class = entry.get("runtime_class")
    if runtime_class:
        module_path, class_name = runtime_class
        cls = getattr(importlib.import_module(module_path), class_name)
        return cls(model=use_model, **kwargs)

    # Plain API-key HTTP provider: the base Runtime + the api_registry wire
    # IS the runtime. Resolve the key up front so a missing credential fails
    # here with guidance instead of at first exec.
    from openprogram.agentic_programming.runtime import Runtime
    api_key = kwargs.pop("api_key", None) or _http_api_key_for(entry)
    if not api_key:
        raise ValueError(entry["credential_error"])
    return Runtime(
        model=f"{entry['model_namespace']}:{use_model}", api_key=api_key, **kwargs
    )


__all__ = [
    "PROVIDERS",
    "detect_provider",
    "create_runtime",
    "check_providers",
]
