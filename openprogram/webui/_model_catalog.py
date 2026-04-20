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

# Importing this module triggers runtime-level registry augmentation
# (adds Codex-route models like gpt-5.4 that aren't in models_generated.py).
from openprogram.providers.openai_codex import runtime as _codex_runtime  # noqa: F401


# Display labels for provider ids. Anything not listed falls back to
# prettified id ("amazon-bedrock" -> "Amazon Bedrock").
_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openai-codex": "ChatGPT Codex",
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


# Providers whose base URL speaks the OpenAI-compatible /v1/models listing
# (Bearer auth, standard {data:[{id:...}]} response). Everything else either
# has no public listing or uses a custom auth / response shape.
_FETCH_MODELS_PROVIDERS = frozenset({
    # Straight OpenAI-compatible /v1/models, Bearer auth, {data:[{id}]}:
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
    # Excluded deliberately:
    #   anthropic      — /v1/models uses x-api-key header, not Bearer
    #   google*        — custom endpoints / OAuth
    #   azure-*        — needs deployment name not model id
    #   amazon-bedrock — AWS SigV4
    #   openai-codex   — ChatGPT backend, no public listing (403)
    #   github-copilot — private OAuth with custom headers
    #   opencode       — not verified; add if/when tested
})


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
        custom = pcfg.get("custom_models") or []
        enabled_ids = set(pcfg.get("enabled_models") or [])
        all_ids = {m.id for m in models} | {c.get("id") for c in custom if c.get("id")}
        default_base = models[0].base_url if models and models[0].base_url else ""
        result.append({
            "id": pid,
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _ENV_API_KEYS.get(pid),
            "default_base_url": default_base,
            "base_url": pcfg.get("base_url") or "",
            "use_responses_api": bool(pcfg.get("use_responses_api", False)),
            "supports_fetch": pid in _FETCH_MODELS_PROVIDERS,
            "model_count": len(models) + len(custom),
            "enabled_model_count": sum(1 for mid in all_ids if mid in enabled_ids),
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
    """All models for a provider + their enabled flag (from config).

    Sources merged:
      - Static registry (from openprogram.providers)
      - Dynamic custom_models the user pulled via /api/providers/<name>/fetch-models
        or added by hand (stored under config.providers[<name>].custom_models).
    """
    from openprogram.providers import get_models
    from openprogram.providers.types import Model, ModelCost

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    enabled_ids = set(pcfg.get("enabled_models") or [])

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for m in get_models(provider_id):
        seen.add(m.id)
        out.append(_model_to_dict(m, m.id in enabled_ids))

    # Custom models: just {id, name?, context_window?} dicts from the user.
    for raw in pcfg.get("custom_models", []):
        mid = raw.get("id") or ""
        if not mid or mid in seen:
            continue
        # Lift into a pseudo-Model dict with whatever the user supplied.
        out.append({
            "id": mid,
            "name": raw.get("name", mid),
            "api": raw.get("api", "custom"),
            "context_window": int(raw.get("context_window", 0)) or 0,
            "max_tokens": int(raw.get("max_tokens", 0)) or 0,
            "vision": bool(raw.get("vision", False)),
            "reasoning": bool(raw.get("reasoning", False)),
            "tools": bool(raw.get("tools", True)),
            "enabled": mid in enabled_ids,
            "custom": True,
        })

    return out


def get_provider_config(provider_id: str) -> dict[str, Any]:
    """Expose per-provider user config (base_url override, toggles)."""
    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    return {
        "base_url": pcfg.get("base_url") or "",
        "use_responses_api": bool(pcfg.get("use_responses_api", False)),
    }


def set_provider_config(provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        if "base_url" in patch:
            bu = (patch.get("base_url") or "").strip()
            if bu:
                pcfg["base_url"] = bu
            else:
                pcfg.pop("base_url", None)
        if "use_responses_api" in patch:
            pcfg["use_responses_api"] = bool(patch.get("use_responses_api"))
        _write_providers_cfg(cfg)
    return get_provider_config(provider_id)


def add_custom_models(provider_id: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a list of model descriptors into custom_models (dedup by id)."""
    if not models:
        return {"provider": provider_id, "added": 0, "total": 0}
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        existing = {m.get("id"): m for m in pcfg.get("custom_models", []) if m.get("id")}
        added = 0
        for raw in models:
            mid = (raw.get("id") or "").strip()
            if not mid:
                continue
            if mid not in existing:
                existing[mid] = raw
                added += 1
            else:
                # Shallow merge new hints into the existing entry.
                existing[mid].update({k: v for k, v in raw.items() if v is not None})
        pcfg["custom_models"] = list(existing.values())
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "added": added, "total": len(existing)}


def remove_custom_model(provider_id: str, model_id: str) -> dict[str, Any]:
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        before = len(pcfg.get("custom_models", []))
        pcfg["custom_models"] = [m for m in pcfg.get("custom_models", []) if m.get("id") != model_id]
        # Also drop from enabled list, if present.
        if "enabled_models" in pcfg:
            pcfg["enabled_models"] = [mid for mid in pcfg["enabled_models"] if mid != model_id]
        _write_providers_cfg(cfg)
    return {"provider": provider_id, "model": model_id, "removed": True}


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


def _resolve_base_url(provider_id: str) -> str | None:
    """Resolved base URL: user override → Model.base_url → provider default."""
    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    if pcfg.get("base_url"):
        return pcfg["base_url"].rstrip("/")
    # Fallback: first model's base_url in the registry
    from openprogram.providers import get_models
    ms = get_models(provider_id)
    if ms and ms[0].base_url:
        return ms[0].base_url.rstrip("/")
    return None


def _resolve_api_key(provider_id: str) -> str | None:
    """Resolved API key for a provider (env var > config api_keys)."""
    env = _ENV_API_KEYS.get(provider_id)
    if env:
        import os
        val = os.environ.get(env)
        if val:
            return val
        # Fall back to ~/.agentic/config.json api_keys
        from openprogram.webui.server import _load_config
        return _load_config().get("api_keys", {}).get(env) or None
    return None


def fetch_models_remote(provider_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Hit the provider's OpenAI-compatible `/v1/models` endpoint and return
    what's there. Works for any provider that speaks OpenAI Completions
    protocol (openrouter, groq, cerebras, mistral, huggingface, kimi,
    minimax, opencode, vercel-ai-gateway, ...).

    Returns dict: {"fetched": N, "added": N, "models": [ids...]} on success,
    {"error": "..."} on failure.
    """
    if provider_id not in _FETCH_MODELS_PROVIDERS:
        return {"error": (
            f"{_label(provider_id)} has no public /v1/models endpoint. "
            "Models are curated manually for this provider."
        )}

    import httpx

    api_key = _resolve_api_key(provider_id)
    if api_key is None and _ENV_API_KEYS.get(provider_id):
        return {"error": f"No API key for {provider_id} (set {_ENV_API_KEYS[provider_id]})"}

    base = _resolve_base_url(provider_id)
    if not base:
        return {"error": f"No base URL resolvable for {provider_id}"}

    url = base + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except httpx.RequestError as e:
        return {"error": f"Request failed: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    items = data.get("data") or data.get("models") or []
    if not isinstance(items, list):
        return {"error": "Unexpected /models response shape"}

    models: list[dict[str, Any]] = []
    for it in items:
        if isinstance(it, str):
            models.append({"id": it, "name": it})
            continue
        if not isinstance(it, dict):
            continue
        mid = it.get("id") or it.get("name")
        if not mid:
            continue
        # OpenRouter and friends include extras; keep id+name and basics.
        entry = {
            "id": mid,
            "name": it.get("name") or mid,
        }
        ctx = it.get("context_length") or it.get("context_window") or it.get("contextWindow")
        if ctx:
            try: entry["context_window"] = int(ctx)
            except Exception: pass
        if it.get("vision") or "vision" in str(it.get("architecture", {})).lower():
            entry["vision"] = True
        if it.get("reasoning"):
            entry["reasoning"] = True
        models.append(entry)

    result = add_custom_models(provider_id, models)
    return {
        "provider": provider_id,
        "fetched": len(models),
        "added": result["added"],
        "total_custom": result["total"],
    }


def test_provider(provider_id: str, model: str | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Send a one-shot tiny PING to verify api_key + base_url work.

    Uses OpenAI Chat Completions shape (most universal). Returns
    {"ok": True, "latency_ms": ...} or {"ok": False, "error": "..."}.
    """
    import time as _time
    import httpx

    api_key = _resolve_api_key(provider_id)
    if api_key is None and _ENV_API_KEYS.get(provider_id):
        return {"ok": False, "error": f"No API key set ({_ENV_API_KEYS[provider_id]})"}

    base = _resolve_base_url(provider_id)
    if not base:
        return {"ok": False, "error": "No base URL resolvable"}

    if not model:
        # Pick the first enabled or first available model.
        cfg = _read_providers_cfg()
        enabled = (cfg.get(provider_id, {}).get("enabled_models") or [])
        if enabled:
            model = enabled[0]
        else:
            from openprogram.providers import get_models
            ms = get_models(provider_id)
            if not ms:
                return {"ok": False, "error": "No model available to test with"}
            model = ms[0].id

    url = base + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"} if api_key else {"Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "PING"}],
        "max_tokens": 4,
    }
    t0 = _time.time()
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=timeout)
        latency_ms = int((_time.time() - t0) * 1000)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}", "latency_ms": latency_ms}
        return {"ok": True, "latency_ms": latency_ms, "model": model}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Request failed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _read_providers_cfg() -> dict[str, dict[str, Any]]:
    from openprogram.webui.server import _load_config
    return _load_config().get("providers", {})


def _write_providers_cfg(providers_cfg: dict[str, dict[str, Any]]) -> None:
    from openprogram.webui.server import _load_config, _save_config
    cfg = _load_config()
    cfg["providers"] = providers_cfg
    _save_config(cfg)
