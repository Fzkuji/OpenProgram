"""
openprogram.legacy_providers — Built-in Runtime implementations for popular LLM providers.

Each provider is an optional dependency. Import will give a clear error
if the required SDK is not installed.

Available providers:
    AnthropicRuntime       — Anthropic Claude API (text + image, prompt caching)
    OpenAIRuntime          — OpenAI GPT API (text + image, response_format)
    GeminiRuntime          — Google Gemini API (text + image)
    ClaudeCodeRuntime       — Claude via local ``claude-max-api-proxy`` daemon (HTTP),
                             for Max-plan users who don't have a paid API key.
    ChatGPTSubscriptionRuntime — OpenAI Codex HTTP API (ChatGPT subscription OAuth, reads ~/.codex/auth.json)
    GeminiSubscriptionRuntime  — Google Gemini HTTP API (Google account OAuth, reads ~/.gemini/oauth_creds.json)

Usage:
    from openprogram.legacy_providers import AnthropicRuntime
    rt = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")

    from openprogram.legacy_providers import OpenAIRuntime
    rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")

    from openprogram.legacy_providers import GeminiRuntime
    rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")

    from openprogram.legacy_providers import ChatGPTSubscriptionRuntime
    rt = ChatGPTSubscriptionRuntime(model="gpt-5.5-mini")

Auto-detection:
    from openprogram.legacy_providers import detect_provider, create_runtime

    provider, model = detect_provider()     # auto-detect best available
    rt = create_runtime()                   # create runtime with auto-detection
    rt = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
"""

import os
import shutil


# -- Provider registry -------------------------------------------------------

# Maps provider name -> (class_name, module_path, default_model)
PROVIDERS = {
    # Claude via a local `claude-max-api-proxy` daemon (HTTP). Replaces
    # the previous CLI-spawning `claude-code` provider; tools come from
    # OpenProgram's own registry instead of the CLI's built-ins.
    "claude-code":        ("ClaudeCodeRuntime",             "openprogram.providers.anthropic._max_proxy_runtime",  "claude-sonnet-4-6"),
    "chatgpt-subscription": ("ChatGPTSubscriptionRuntime", "openprogram.legacy_providers.openai_codex",           "gpt-5.5"),
    "gemini-cli":        ("GeminiSubscriptionRuntime",    "openprogram.providers.google_gemini_cli.runtime",     "gemini-2.5-flash"),
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
            return "chatgpt-subscription", None

    return None


def _load_provider_config() -> tuple[str, str] | None:
    """Load provider preference from env vars or ~/.agentic/config.json.

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
        with open(config_path) as f:
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
      2. Config file (~/.agentic/config.json → default_provider / default_model)
      3. Caller environment (inside Claude Code? Codex? → use the same)
      4. Available CLI providers (claude → codex → gemini)
      5. Available API keys (ANTHROPIC_API_KEY → OPENAI_API_KEY → GOOGLE_API_KEY)

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
        return "chatgpt-subscription", None
    if shutil.which("gemini"):
        return "gemini-cli", "gemini-2.5-flash"

    # 5. API providers (need keys)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", "claude-sonnet-4-6"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", "gpt-4.1"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY"):
        return "gemini", "gemini-2.5-flash"

    raise RuntimeError(
        "No LLM provider found. Set up one of the following:\n"
        "\n"
        "  CLI providers (no API key needed):\n"
        "    1. Codex CLI:        npm install -g @openai/codex && codex auth\n"
        "    2. Gemini CLI:       npm install -g @google/gemini-cli\n"
        "\n"
        "  API providers (set environment variable):\n"
        "    3. Anthropic:  export ANTHROPIC_API_KEY=sk-ant-...\n"
        "    4. OpenAI:     export OPENAI_API_KEY=sk-...\n"
        "    5. Gemini:     export GOOGLE_API_KEY=...\n"
        "                    (or GOOGLE_GENERATIVE_AI_API_KEY=...)\n"
        "\n"
        "  Claude via Max plan (HTTP proxy):\n"
        "    6. Install + launch the proxy, then use provider=claude-max:\n"
        "       npm install -g claude-max-api-proxy && claude-max-api-proxy\n"
        "\n"
        "  Or set explicitly:\n"
        "    export AGENTIC_PROVIDER=openai\n"
        "    export AGENTIC_MODEL=gpt-5.5\n"
    )


def check_providers() -> dict:
    """Check availability of all providers.

    Returns a dict with status of each provider:
        {
            "chatgpt-subscription": {"available": True, "method": "CLI", "model": "gpt-5.5"},
            "openai": {"available": True, "method": "API", "model": "gpt-4.1"},
            ...
        }
    """
    results = {}
    cli_checks = {
        "chatgpt-subscription": "codex",
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

    for name, env_vars in api_checks.items():
        _, _, default_model = PROVIDERS[name]
        if isinstance(env_vars, str):
            env_vars = [env_vars]
        has_key = any(os.environ.get(v) for v in env_vars)
        results[name] = {
            "available": has_key,
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
            available = ", ".join(sorted(PROVIDERS.keys()) + ["auto"])
            raise ValueError(
                f"Unknown provider: {provider!r}. Available: {available}"
            )
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
    if name in ("ClaudeCodeRuntime", "ClaudeCodeRuntime"):
        from openprogram.providers.anthropic._max_proxy_runtime import (
            ClaudeCodeRuntime,
        )
        return ClaudeCodeRuntime
    if name in ("ChatGPTSubscriptionRuntime", "ChatGPTSubscriptionRuntime"):
        from openprogram.legacy_providers.openai_codex import ChatGPTSubscriptionRuntime
        return ChatGPTSubscriptionRuntime
    if name in ("GeminiSubscriptionRuntime", "GeminiSubscriptionRuntime"):
        from openprogram.providers.google_gemini_cli.runtime import (
            GeminiSubscriptionRuntime,
        )
        return GeminiSubscriptionRuntime
    raise AttributeError(f"module 'openprogram.legacy_providers' has no attribute {name!r}")


__all__ = [
    "PROVIDERS",
    "detect_provider",
    "create_runtime",
    "AnthropicRuntime",
    "OpenAIRuntime",
    "GeminiRuntime",
    "ClaudeCodeRuntime",
    "ChatGPTSubscriptionRuntime",
    "GeminiSubscriptionRuntime",
]
