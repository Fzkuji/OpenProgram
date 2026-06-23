"""
openprogram.providers.registry — Built-in Runtime implementations for popular LLM providers.

Each provider is an optional dependency. Import will give a clear error
if the required SDK is not installed.

Available providers:
    AnthropicRuntime       — Anthropic Claude API (text + image, prompt caching)
    OpenAIRuntime          — OpenAI GPT API (text + image, response_format)
    GeminiRuntime          — Google Gemini API (text + image)
    ClaudeCodeRuntime       — Claude subscription, direct to api.anthropic.com
                             (Bearer OAuth + Claude Code beta headers), for
                             users who log in with a Claude plan instead of a
                             paid API key. No Meridian proxy.
    OpenAICodexRuntime — OpenAI Codex HTTP API (ChatGPT subscription OAuth, reads ~/.codex/auth.json)
    GeminiCLIRuntime  — Google Gemini HTTP API (Google account OAuth, reads ~/.gemini/oauth_creds.json)

Usage:
    from openprogram.providers.registry import AnthropicRuntime
    rt = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")

    from openprogram.providers.registry import OpenAIRuntime
    rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")

    from openprogram.providers.registry import GeminiRuntime
    rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")

    from openprogram.providers.registry import OpenAICodexRuntime
    rt = OpenAICodexRuntime(model="gpt-5.5-mini")

Auto-detection:
    from openprogram.providers.registry import detect_provider, create_runtime

    provider, model = detect_provider()     # auto-detect best available
    rt = create_runtime()                   # create runtime with auto-detection
    rt = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
"""

import os
import shutil


# -- Provider registry -------------------------------------------------------

# Maps provider name -> (class_name, module_path, default_model)
PROVIDERS = {
    # Claude via a Claude subscription, connected DIRECT to
    # api.anthropic.com (Bearer OAuth + Claude Code beta headers) — same
    # shape as openai-codex direct-connecting to chatgpt.com. No Meridian
    # daemon: the wire is the standard anthropic Messages API, which
    # natively handles image blocks (gui_agent multimodal preserved). The
    # subscription token resolves from the anthropic AuthStore pool.
    # (The old Meridian-proxy runtime has been removed entirely.)
    "claude-code":        ("ClaudeCodeRuntime",             "openprogram.providers.anthropic._claude_code_direct_runtime",  "claude-sonnet-4"),
    "openai-codex": ("OpenAICodexRuntime", "openprogram.providers.openai_codex.runtime",           "gpt-5.5"),
    "gemini-cli":        ("GeminiCLIRuntime",    "openprogram.providers.google_gemini_cli.runtime",     "gemini-2.5-flash"),
    "anthropic":        ("AnthropicRuntime",       "openprogram.providers.anthropic.runtime",             "claude-sonnet-4-6"),
    "openai":           ("OpenAIRuntime",          "openprogram.providers.openai_responses.runtime",      "gpt-4.1"),
    "gemini":           ("GeminiRuntime",          "openprogram.providers.google.runtime",                "gemini-2.5-flash"),
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
        default_model = PROVIDERS.get(provider, (None, None, None))[2]
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
            default_model = PROVIDERS.get(provider, (None, None, None))[2]
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
        _, _, default_model = PROVIDERS[name]
        results[name] = {
            "available": shutil.which(binary) is not None,
            "method": "CLI",
            "model": default_model,
        }

    # Availability via the canonical resolver (AuthStore / cloud chain).
    # The status names here map to the canonical provider ids
    # ("gemini" status row → "google").
    from openprogram.providers.env_api_keys import is_configured
    _canon = {"gemini": "google"}
    for name in api_checks:
        _, _, default_model = PROVIDERS[name]
        results[name] = {
            "available": is_configured(_canon.get(name, name)),
            "method": "API",
            "model": default_model,
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
    from openprogram.providers.models_generated import MODELS

    if not model:
        cands = [m for m in MODELS.values() if m.provider == provider]
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
            from openprogram.webui._runtime_management import (
                _register_custom_model_in_registry,
            )
            _register_custom_model_in_registry(provider, model)
        except Exception:
            pass
    return Runtime(model=f"{provider}:{model}", **kwargs)


def create_runtime(provider: str = None, model: str = None, **kwargs):
    """Create a Runtime instance with auto-detection or explicit provider.

    Args:
        provider:  Provider name (e.g. "anthropic", "claude-code",
                   "openai", "gemini-cli"). Pass "auto" or None to
                   auto-detect the best available provider via
                   detect_provider().
        model:     Model name override.
        **kwargs:  Forwarded to the provider Runtime constructor.

    Returns:
        A Runtime instance ready to use.
    """
    import importlib

    if provider and provider != "auto":
        if provider not in PROVIDERS:
            # ``PROVIDERS`` is NOT the list of supported providers — it
            # only holds the 6 backends that need a bespoke Runtime class
            # (OAuth / CLI delegation: claude-code, openai-codex,
            # gemini-cli, anthropic, openai, gemini). Every other provider
            # — deepseek, groq, openrouter, minimax, kimi, the whole
            # models.dev catalogue — is supported through its model's
            # ``api`` + the api_registry, exactly how the chat dispatcher
            # streams them. Route those through the base Runtime instead
            # of failing, so create_runtime() matches chat coverage.
            return _api_routed_runtime(provider, model, **kwargs)
        class_name, module_path, default_model = PROVIDERS[provider]
    else:
        detected, detected_model = detect_provider()
        class_name, module_path, table_default = PROVIDERS[detected]
        # detect_provider returns None for CLI providers ("we found
        # the binary but don't have an opinion on which model"). The
        # PROVIDERS table always carries a non-empty default for every
        # backend; prefer the detected value when present, otherwise
        # fall back to the table so we never hand the runtime a
        # ``model=None`` and crash at construction.
        default_model = detected_model or table_default
        provider = detected

    use_model = model or default_model

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(model=use_model, **kwargs)


# -- Lazy imports for direct class access ------------------------------------

def __getattr__(name):
    """Lazy imports — only load a provider when accessed."""
    if name == "AnthropicRuntime":
        from openprogram.providers.anthropic.runtime import AnthropicRuntime
        return AnthropicRuntime
    if name == "OpenAIRuntime":
        from openprogram.providers.openai_responses.runtime import OpenAIRuntime
        return OpenAIRuntime
    if name == "GeminiRuntime":
        from openprogram.providers.google.runtime import GeminiRuntime
        return GeminiRuntime
    if name == "ClaudeCodeRuntime":
        from openprogram.providers.anthropic._claude_code_direct_runtime import (
            ClaudeCodeRuntime,
        )
        return ClaudeCodeRuntime
    if name in ("OpenAICodexRuntime", "OpenAICodexRuntime"):
        from openprogram.providers.openai_codex.runtime import OpenAICodexRuntime
        return OpenAICodexRuntime
    if name in ("GeminiCLIRuntime", "GeminiCLIRuntime"):
        from openprogram.providers.google_gemini_cli.runtime import (
            GeminiCLIRuntime,
        )
        return GeminiCLIRuntime
    raise AttributeError(f"module 'openprogram.providers.registry' has no attribute {name!r}")


__all__ = [
    "PROVIDERS",
    "detect_provider",
    "create_runtime",
    "AnthropicRuntime",
    "OpenAIRuntime",
    "GeminiRuntime",
    "ClaudeCodeRuntime",
    "OpenAICodexRuntime",
    "GeminiCLIRuntime",
]
