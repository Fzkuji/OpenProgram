"""
Unified provider + model catalog for the webui.

Responsibilities:
- Enumerate API providers (from openprogram.providers registry) + CLI
  runtime providers (claude-code, gemini-cli) as one combined list.
- Enumerate models per provider with capability metadata
  (vision / reasoning / tools / context_window).
- Persist per-provider enabled flag and per-model enabled list in
  ~/.agentic/config.json under the "providers" key.
- Expose the "enabled models" view the chat page picker consumes.

Enable semantics:
- A provider is usable only if it's enabled AND configured (API key present
  or CLI binary available).
- Models follow a default-off convention: the "enabled_models" list is the
  whitelist. On first-enable of a provider we don't auto-populate; the user
  picks what they want, which keeps the picker focused.
- Legacy: if a provider has no entry in config, it's considered disabled.
"""
from __future__ import annotations

import shutil
import threading
from typing import Any


# Display labels for provider ids. Anything not listed falls back to
# prettified id ("amazon-bedrock" -> "Amazon Bedrock").
_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openai-codex": "ChatGPT Codex (Subscription)",
    "anthropic": "Anthropic",
    "google": "Google AI",
    "google-vertex": "Google Vertex AI",
    "google-gemini-cli": "Google Gemini (Cloud Code Assist)",
    "google-antigravity": "Google Antigravity",
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
    # CLI-backed:
    "claude-code": "Claude Code CLI",
    "gemini-cli": "Gemini CLI",
}


# CLI-backed providers aren't in the HTTP provider registry. We describe them
# here so the settings page can list them alongside registry providers.
_CLI_PROVIDERS = [
    {
        "id": "claude-code",
        "label": _PROVIDER_LABELS["claude-code"],
        "kind": "cli",
        "cli_binary": "claude",
        "api_key_env": None,
    },
    {
        "id": "gemini-cli",
        "label": _PROVIDER_LABELS["gemini-cli"],
        "kind": "cli",
        "cli_binary": "gemini",
        "api_key_env": None,
    },
]


_ENV_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "google-vertex": None,  # uses gcloud ADC
    "google-gemini-cli": None,  # uses OAuth
    "google-antigravity": None,
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
}


_cache_lock = threading.Lock()


def _prettify(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _label(provider_id: str) -> str:
    return _PROVIDER_LABELS.get(provider_id, _prettify(provider_id))


def _is_configured(provider_id: str) -> bool:
    """Is this provider usable (key present or CLI binary found)."""
    # CLI-backed: binary presence decides.
    for cli in _CLI_PROVIDERS:
        if cli["id"] == provider_id:
            return shutil.which(cli["cli_binary"]) is not None
    # openai-codex: reads ~/.codex/auth.json
    if provider_id == "openai-codex":
        from pathlib import Path
        return (Path.home() / ".codex" / "auth.json").exists()
    env = _ENV_API_KEYS.get(provider_id)
    if env is None:
        return True  # assume true for providers without a standard key var
    import os
    from openprogram.webui.server import _get_api_key  # re-use helper
    return bool(_get_api_key(env))


def _model_to_dict(model, enabled: bool) -> dict[str, Any]:
    inputs = list(getattr(model, "input", []) or [])
    return {
        "id": model.id,
        "name": getattr(model, "name", model.id),
        "api": model.api,
        "context_window": getattr(model, "context_window", 0) or 0,
        "max_tokens": getattr(model, "max_tokens", 0) or 0,
        "vision": "image" in inputs,
        "reasoning": bool(getattr(model, "reasoning", False)),
        "tools": True,  # all HTTP providers route tool_calls
        "enabled": enabled,
    }


def list_providers() -> list[dict[str, Any]]:
    """Unified provider list with enable/configure status and model counts."""
    from openprogram.providers import get_providers, get_models

    cfg = _read_providers_cfg()
    result: list[dict[str, Any]] = []

    # HTTP providers from registry
    seen: set[str] = set()
    for pid in get_providers():
        seen.add(pid)
        pcfg = cfg.get(pid, {})
        models = get_models(pid)
        enabled_ids = set(pcfg.get("enabled_models") or [])
        result.append({
            "id": pid,
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _ENV_API_KEYS.get(pid),
            "model_count": len(models),
            "enabled_model_count": sum(1 for m in models if m.id in enabled_ids),
        })

    # CLI-backed providers (not in registry)
    for cli in _CLI_PROVIDERS:
        pid = cli["id"]
        if pid in seen:
            continue
        pcfg = cfg.get(pid, {})
        result.append({
            "id": pid,
            "label": cli["label"],
            "kind": "cli",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "cli_binary": cli["cli_binary"],
            "api_key_env": None,
            "model_count": 0,  # CLI runtimes decide model at runtime
            "enabled_model_count": 0,
        })

    # Sort: enabled first, then by label.
    result.sort(key=lambda p: (not p["enabled"], p["label"].lower()))
    return result


def list_models_for_provider(provider_id: str) -> list[dict[str, Any]]:
    """All models for a provider + their enabled flag (from config)."""
    from openprogram.providers import get_models

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    enabled_ids = set(pcfg.get("enabled_models") or [])
    models = get_models(provider_id)
    return [_model_to_dict(m, m.id in enabled_ids) for m in models]


def list_enabled_models() -> list[dict[str, Any]]:
    """Flat list of all enabled models across enabled providers.

    Used by the chat page model picker.
    """
    from openprogram.providers import get_providers, get_models

    cfg = _read_providers_cfg()
    out: list[dict[str, Any]] = []
    for pid in get_providers():
        pcfg = cfg.get(pid, {})
        if not pcfg.get("enabled"):
            continue
        enabled_ids = set(pcfg.get("enabled_models") or [])
        if not enabled_ids:
            continue
        if not _is_configured(pid):
            continue
        for m in get_models(pid):
            if m.id not in enabled_ids:
                continue
            entry = _model_to_dict(m, True)
            entry["provider"] = pid
            entry["provider_label"] = _label(pid)
            out.append(entry)
    return out


def toggle_provider(provider_id: str, enabled: bool) -> dict[str, Any]:
    """Enable/disable a whole provider."""
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        pcfg["enabled"] = bool(enabled)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "enabled": bool(enabled)}


def toggle_model(provider_id: str, model_id: str, enabled: bool) -> dict[str, Any]:
    """Add/remove model_id in provider's enabled_models whitelist."""
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        lst = pcfg.setdefault("enabled_models", [])
        if enabled and model_id not in lst:
            lst.append(model_id)
        elif not enabled and model_id in lst:
            lst.remove(model_id)
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "model": model_id, "enabled": bool(enabled)}


def _read_providers_cfg() -> dict[str, dict[str, Any]]:
    from openprogram.webui.server import _load_config
    return _load_config().get("providers", {})


def _write_providers_cfg(providers_cfg: dict[str, dict[str, Any]]) -> None:
    from openprogram.webui.server import _load_config, _save_config
    cfg = _load_config()
    cfg["providers"] = providers_cfg
    _save_config(cfg)
