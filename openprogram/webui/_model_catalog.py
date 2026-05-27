"""
Unified provider + model catalog for the webui.

Responsibilities:
- Enumerate API providers (from openprogram.providers registry) + CLI
  runtime providers (gemini-cli) as one combined list.
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

# Importing these modules triggers runtime-level registry augmentation:
# - openai_codex runtime adds Codex-route models (gpt-5.5 family)
# - anthropic._claude_max_proxy_registry adds Claude models under the
#   "claude-code" provider so list_enabled_models can find them
from openprogram.providers.openai_codex import runtime as _codex_runtime  # noqa: F401
from openprogram.providers.anthropic import _claude_max_proxy_registry as _cmp_registry  # noqa: F401


# Display labels for provider ids. Anything not listed falls back to
# prettified id ("amazon-bedrock" -> "Amazon Bedrock").
_PROVIDER_LABELS = {
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
    # Claude via local HTTP proxy daemon (replaces the old Claude Code CLI
    # provider). Tools come from OpenProgram's own registry instead of the
    # CLI's built-ins.
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
    #   openai-codex — ChatGPT backend, no public listing (403)
    #   github-copilot — private OAuth with custom headers
    #   opencode       — not verified; add if/when tested
})


_ENV_API_KEYS = {
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
}


_cache_lock = threading.Lock()


def _prettify(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _label(provider_id: str) -> str:
    return _PROVIDER_LABELS.get(provider_id, _prettify(provider_id))


def _is_configured(provider_id: str) -> bool:
    """Is this provider usable (key present, CLI binary found, or
    local daemon responding)."""
    # CLI-backed: binary presence decides.
    for cli in _CLI_PROVIDERS:
        if cli["id"] == provider_id:
            return shutil.which(cli["cli_binary"]) is not None
    # openai-codex: reads ~/.codex/auth.json
    if provider_id == "openai-codex":
        from pathlib import Path
        return (Path.home() / ".codex" / "auth.json").exists()
    # claude-code: there's no env-key path; the daemon is "ready"
    # when its HTTP endpoint answers. Quick 0.5s probe — failure means
    # the user hasn't started ``meridian`` (or claude-max-api) yet.
    if provider_id == "claude-code":
        import os, urllib.request, urllib.error
        # Both meridian and claude-max-api-proxy default to port 3456.
        # Strip a trailing /v1 because /health lives at root.
        url = (os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456").rstrip("/")
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
    import os
    from openprogram.webui.server import _get_api_key  # re-use helper
    return bool(_get_api_key(env))


# Provider-specific setup instructions surfaced in the web UI's
# detail pane. Plain text with two minor conventions:
#   * Backticked spans render as inline <code>.
#   * Lines beginning with ``$ `` render as a command row (copy-able).
_SETUP_HINTS: dict[str, str] = {
    "claude-code": (
        "Claude Code auth lives in the Claude Code keychain — there's no API\n"
        "key to paste here. Install the Claude Code CLI, sign in once, install\n"
        "the Meridian proxy daemon, then keep `meridian` running while you use\n"
        "this provider:\n"
        "\n"
        "$ npm install -g @anthropic-ai/claude-code\n"
        "$ claude auth login\n"
        "$ npm install -g @rynfar/meridian --ignore-scripts\n"
        "$ meridian\n"
        "\n"
        "`meridian` is a plain foreground daemon — don't close that terminal\n"
        "while you're using Claude here. To stop it just `Ctrl+C` or close\n"
        "the window; run `meridian` again next time you want Claude back.\n"
        "Deliberately not wrapped in a service / scheduled task — the\n"
        "lifecycle stays yours.\n"
        "\n"
        "Windows: pass `--ignore-scripts` to the Meridian install. Its\n"
        "postinstall hook calls a POSIX one-liner (`2>/dev/null || true`)\n"
        "that crashes under `cmd.exe`; skipping it doesn't affect runtime.\n"
        "Older docs also say `claude login`; the current CLI moved it to\n"
        "`claude auth login`.\n"
        "\n"
        "Meridian listens on `http://127.0.0.1:3456` by default. Override with\n"
        "`CLAUDE_MAX_PROXY_URL=http://host:port` if 3456 is taken.\n"
        "\n"
        "Once the daemon is up, this section flips to \"Detected\" and you\n"
        "can enable the provider above. The proxy exposes an OpenAI-\n"
        "compatible `/v1/chat/completions` endpoint and routes traffic\n"
        "through your Claude Code OAuth session, so no extra API key is\n"
        "needed.\n"
        "\n"
        "Legacy: the older `claude-max-api-proxy` package also works on the\n"
        "same port for text-only traffic, but mangles multipart image\n"
        "content — prefer Meridian for any agent that sends screenshots\n"
        "(e.g. `gui_agent`)."
    ),
    "openai-codex": (
        "OpenAI Codex reuses your ChatGPT Plus / Pro / Team / Enterprise\n"
        "subscription via OAuth — there's no API key to paste here.\n"
        "\n"
        "Run the PKCE login from a terminal. A browser tab will open to\n"
        "`auth.openai.com`; sign in with the account that holds your\n"
        "subscription and approve the scope:\n"
        "\n"
        "$ openprogram providers login openai-codex --method pkce_oauth\n"
        "\n"
        "The callback lands on `localhost:1455` — don't have another `codex`\n"
        "or `pi-ai` process holding that port. Tokens are saved to\n"
        "`~/.openprogram/openai-codex/default.json` and auto-refreshed.\n"
        "\n"
        "Once login completes this section flips to \"Configured\" and the\n"
        "Connectivity probe below will go green. Requests stream against\n"
        "`chatgpt.com/backend-api/codex/responses`, so traffic to that host\n"
        "must be reachable from your network — corporate proxies that block\n"
        "consumer ChatGPT will block Codex too.\n"
        "\n"
        "If you're on a bare OpenAI API key (pay-per-token) instead of a\n"
        "ChatGPT subscription, use the regular **OpenAI** provider instead\n"
        "of this one — they're separate billing paths."
    ),
    "anthropic": (
        "Anthropic API uses a static key issued from the Console:\n"
        "\n"
        "$ open https://console.anthropic.com/settings/keys\n"
        "\n"
        "Create a key, then either paste it into the field below or set\n"
        "the env var `ANTHROPIC_API_KEY=sk-ant-...` and restart\n"
        "`openprogram --web`. Either source works; the field takes\n"
        "precedence when both are set.\n"
        "\n"
        "This is the metered pay-per-token path. If you have an\n"
        "Anthropic Pro / Team subscription and want to route through\n"
        "your existing Claude session instead, use the **Claude Code**\n"
        "provider (Meridian proxy, no API key needed)."
    ),
    "gemini-subscription": (
        "Gemini CLI reuses your Gemini Advanced / Workspace subscription\n"
        "via Google Cloud Code Assist — no API key to paste.\n"
        "\n"
        "Install Google's `gemini` CLI and log in there once. OpenProgram\n"
        "auto-detects ``~/.gemini/oauth_creds.json`` and refreshes via\n"
        "the bundled refresh flow:\n"
        "\n"
        "$ npm install -g @google/gemini-cli\n"
        "$ gemini\n"
        "$ openprogram providers discover\n"
        "$ openprogram providers adopt gemini_cli\n"
        "\n"
        "If you only want a bare Google AI Studio API key instead\n"
        "(pay-per-token), use the **Google AI** provider — that one\n"
        "takes `GOOGLE_GENERATIVE_AI_API_KEY` and skips the OAuth dance."
    ),
    "github-copilot": (
        "GitHub Copilot uses your existing Copilot subscription's OAuth\n"
        "token — no API key, no separate billing.\n"
        "\n"
        "Log in via the GitHub CLI once and OpenProgram will pick the\n"
        "token up automatically:\n"
        "\n"
        "$ winget install GitHub.cli         # or: brew install gh\n"
        "$ gh auth login --scopes copilot\n"
        "$ openprogram providers discover\n"
        "$ openprogram providers adopt github_copilot\n"
        "\n"
        "Subscription state is checked on every request — the moment\n"
        "your Copilot trial / plan lapses the connectivity check goes\n"
        "red here. Re-running `gh auth refresh` is enough to recover."
    ),
}


def _setup_hint(provider_id: str) -> str | None:
    return _SETUP_HINTS.get(provider_id)


def _model_to_dict(model, enabled: bool) -> dict[str, Any]:
    inputs = list(getattr(model, "input", []) or [])
    return {
        "id": model.id,
        "name": getattr(model, "name", model.id),
        "api": model.api,
        "context_window": getattr(model, "context_window", 0) or 0,
        "max_tokens": getattr(model, "max_tokens", 0) or 0,
        "vision": "image" in inputs,
        "video": "video" in inputs,
        "audio": "audio" in inputs,
        "reasoning": bool(getattr(model, "reasoning", False)),
        # Thinking UX capability (see providers/thinking_catalog.py). Empty
        # `thinking_levels` → UI hides the menu for this model.
        "thinking_levels": list(getattr(model, "thinking_levels", []) or []),
        "default_thinking_level": getattr(model, "default_thinking_level", None),
        "thinking_variant": getattr(model, "thinking_variant", None),
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
        entry = {
            "id": pid,
            "label": _label(pid),
            "kind": "api",
            "enabled": bool(pcfg.get("enabled", False)),
            "configured": _is_configured(pid),
            "api_key_env": _ENV_API_KEYS.get(pid),
            "default_base_url": default_base,
            "base_url": pcfg.get("base_url") or "",
            "use_responses_api": bool(pcfg.get("use_responses_api", False)),
            "supports_fetch": (pid in _FETCH_MODELS_PROVIDERS) or (pid in _FETCHERS),
            "model_count": len(models) + len(custom),
            "enabled_model_count": sum(1 for mid in all_ids if mid in enabled_ids),
        }
        hint = _setup_hint(pid)
        if hint:
            entry["setup_hint"] = hint
            # Note: we no longer force ``api_key_env = None`` when a
            # hint is present. That override was originally there for
            # claude-code (whose Meridian proxy doesn't take an API
            # key), but it also clobbered ``anthropic``'s hint — which
            # IS API-key based and needs both the hint AND the paste
            # field. The ``_ENV_API_KEYS`` dict already carries the
            # correct null/non-null state per provider, so trust it.
        result.append(entry)

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

    Override behavior: once the user has clicked "Fetch models"
    successfully (``pcfg["models_fetched"] = True``), we treat upstream
    as authoritative — builtin-registry rows that aren't in the
    fetched-or-manual set are hidden. This is what makes a Fetch click
    feel like "replace" instead of "append": before this guard, every
    Fetch left the legacy aliases (``claude-opus-4`` / ``claude-sonnet-4``)
    next to the real upstream ids (``claude-opus-4-7`` /
    ``claude-sonnet-4-6``), which the user correctly described as the
    list "not getting overwritten properly".
    """
    from openprogram.providers import get_models
    from openprogram.providers.types import Model, ModelCost

    cfg = _read_providers_cfg()
    pcfg = cfg.get(provider_id, {})
    enabled_ids = set(pcfg.get("enabled_models") or [])
    fetched_only = bool(pcfg.get("models_fetched"))
    custom_ids: set[str] = {
        m.get("id") for m in (pcfg.get("custom_models") or []) if m.get("id")
    }

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for m in get_models(provider_id):
        # When upstream's answer is on file, only emit the builtin row
        # if it's still in upstream's list (or a manual addition). Lets
        # us keep one source of truth without forcing users to nuke
        # ``models_generated.py``.
        if fetched_only and m.id not in custom_ids:
            continue
        seen.add(m.id)
        out.append(_model_to_dict(m, m.id in enabled_ids))

    # Custom models: just {id, name?, context_window?} dicts from the user.
    from openprogram.providers.thinking_catalog import derive_thinking_fields
    # Default API to dispatch through for this provider — see
    # ``_PROVIDER_DEFAULT_API`` for the rationale. Falls back to
    # ``"custom"`` (the legacy unrouteable sentinel) for providers
    # not in the map so we don't silently mis-route an unknown one.
    default_api = _PROVIDER_DEFAULT_API.get(provider_id, "custom")
    for raw in pcfg.get("custom_models", []):
        mid = raw.get("id") or ""
        if not mid or mid in seen:
            continue
        reasoning = bool(raw.get("reasoning", False))
        # Derive thinking capability from override table + defaults so custom
        # models picked up via fetch-models still get a sensible picker.
        levels, default_lv, variant = derive_thinking_fields(
            provider_id, mid, reasoning, bool(raw.get("supports_xhigh", False))
        )
        out.append({
            "id": mid,
            "name": raw.get("name", mid),
            "api": raw.get("api") or default_api,
            "context_window": int(raw.get("context_window", 0)) or 0,
            "max_tokens": int(raw.get("max_tokens", 0)) or 0,
            "vision": bool(raw.get("vision", False)),
            "reasoning": reasoning,
            "thinking_levels": levels,
            "default_thinking_level": default_lv,
            "thinking_variant": variant,
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


def replace_fetched_models(provider_id: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the fetched-from-upstream model set for a provider, leaving
    any manually-added rows alone.

    "Fetch models" is the user saying "tell me what the upstream provider
    actually serves right now". When upstream's answer drifts (rename, new
    family, dropped variant) the previous answer is wrong — we shouldn't
    leave stale rows hanging around. ``add_custom_models`` was union /
    merge semantics, which manifested in the UI as a Claude Code provider
    showing both ``claude-opus-4`` (the legacy alias from the builtin
    registry, re-emitted by an older fetch) and ``claude-opus-4-7`` (the
    real current id) side by side.

    Rows marked ``_source: "fetched"`` are owned by this function and
    rotate on each fetch. Anything without that marker is a manual
    addition and we leave it untouched. Also flips
    ``pcfg["models_fetched"] = True`` so the list endpoint knows to
    hide builtin-registry rows that upstream's fresh answer doesn't
    endorse — otherwise the legacy ``claude-opus-4`` row keeps showing
    up even after a successful fetch, since it comes from
    ``providers/models_generated.py`` not config.
    """
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        prior = pcfg.get("custom_models", []) or []
        # Keep manual entries; rotate out anything we previously fetched.
        kept_manual = [m for m in prior if m.get("_source") != "fetched"]
        kept_ids = {m.get("id") for m in kept_manual if m.get("id")}
        new_rows: list[dict[str, Any]] = []
        for m in models:
            mid = (m.get("id") or "").strip()
            if not mid or mid in kept_ids:
                # Manual override of an upstream id wins — don't overwrite
                # what the user typed in by hand.
                continue
            row = dict(m)
            row["_source"] = "fetched"
            new_rows.append(row)
        pcfg["custom_models"] = kept_manual + new_rows
        # Prune enabled_models that no longer correspond to a visible
        # row. After a rename like ``claude-opus-4`` → ``claude-opus-4-7``,
        # the old id is dead — leaving it in enabled_models means the
        # picker tries to instantiate a model that the runtime can't
        # resolve. Visible row = any new fetched row + any manual row.
        visible_ids = {r["id"] for r in (new_rows + kept_manual) if r.get("id")}
        prior_enabled = list(pcfg.get("enabled_models") or [])
        pcfg["enabled_models"] = [mid for mid in prior_enabled if mid in visible_ids]
        dropped_enabled = [mid for mid in prior_enabled if mid not in visible_ids]
        pcfg["models_fetched"] = True
        _write_providers_cfg(cfg)
        return {
            "provider": provider_id,
            "added": len(new_rows),
            "removed": len(prior) - len(kept_manual),  # rotated-out fetched rows
            "total": len(pcfg["custom_models"]),
            "dropped_enabled": dropped_enabled,
        }


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

    Walks two sources to find enabled models:

    1. The static registry (``get_models(pid)``) — the canonical builtin
       catalogue from ``providers/models_generated.py``.
    2. The ``custom_models`` list under each provider's config entry —
       rows the user pulled via Fetch Models or added by hand. Without
       this second pass, a freshly-fetched id like ``claude-sonnet-4-6``
       (which doesn't exist in the static registry) gets toggled
       enabled, persists to config, but silently never appears in the
       chat picker — a particularly invisible failure mode because
       ``/api/providers/<id>/models`` happily lists the row with
       ``enabled: true``.
    """
    from openprogram.providers import get_providers, get_models
    from openprogram.providers.thinking_catalog import derive_thinking_fields

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
        emitted_ids: set[str] = set()
        fetched_only = bool(pcfg.get("models_fetched"))
        custom_ids = {
            m.get("id") for m in (pcfg.get("custom_models") or []) if m.get("id")
        }
        for m in get_models(pid):
            if m.id not in enabled_ids:
                continue
            # After a Fetch, the user's upstream answer takes precedence
            # over the static catalogue — hide builtin rows that the
            # fetch didn't reaffirm (matches ``list_models_for_provider``).
            if fetched_only and m.id not in custom_ids:
                continue
            entry = _model_to_dict(m, True)
            entry["provider"] = pid
            entry["provider_label"] = _label(pid)
            out.append(entry)
            emitted_ids.add(m.id)

        # Now the second pass: custom_models that the registry doesn't
        # know about. Build a minimal ``Model``-shaped dict that the
        # chat dispatcher accepts via ``api: <default_api>``.
        default_api = _PROVIDER_DEFAULT_API.get(pid, "custom")
        for raw in (pcfg.get("custom_models") or []):
            mid = raw.get("id") or ""
            if not mid or mid not in enabled_ids or mid in emitted_ids:
                continue
            reasoning = bool(raw.get("reasoning", False))
            levels, default_lv, variant = derive_thinking_fields(
                pid, mid, reasoning, bool(raw.get("supports_xhigh", False))
            )
            inputs = []
            if raw.get("vision"):
                inputs.append("image")
            entry = {
                "id": mid,
                "name": raw.get("name", mid),
                "api": raw.get("api") or default_api,
                "context_window": int(raw.get("context_window", 0)) or 0,
                "max_tokens": int(raw.get("max_tokens", 0)) or 0,
                "vision": bool(raw.get("vision", False)),
                "video": False,
                "audio": False,
                "reasoning": reasoning,
                "thinking_levels": levels,
                "default_thinking_level": default_lv,
                "thinking_variant": variant,
                "tools": bool(raw.get("tools", True)),
                "enabled": True,
                "provider": pid,
                "provider_label": _label(pid),
                "custom": True,
            }
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


# ─── Per-provider fetchers ──────────────────────────────────────────────
#
# Each fetcher takes (provider_id, timeout) and returns either
#   * a list of native model items (dicts with {id, name?, ...}), or
#   * {"error": "..."}
# fetch_models_remote then normalises each item to the internal entry
# schema (id / name / context_window / vision / reasoning / ...).

def _fetch_openai_compat(provider_id: str, timeout: float) -> Any:
    """OpenAI-compatible /v1/models: GET base + '/models', Bearer auth."""
    import httpx
    api_key = _resolve_api_key(provider_id)
    if api_key is None and _ENV_API_KEYS.get(provider_id):
        return {"error": f"No API key for {provider_id} (set {_ENV_API_KEYS[provider_id]})"}
    base = _resolve_base_url(provider_id)
    if not base:
        return {"error": f"No base URL resolvable for {provider_id}"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(base + "/models", headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    items = data.get("data") or data.get("models") or []
    return items if isinstance(items, list) else {"error": "unexpected response shape"}


def _fetch_anthropic(provider_id: str, timeout: float) -> Any:
    """Anthropic /v1/models — x-api-key header, returns {data:[{id,...}]}."""
    import httpx
    api_key = _resolve_api_key(provider_id)
    if not api_key:
        return {"error": "No ANTHROPIC_API_KEY set"}
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("data") or []):
        mid = it.get("id")
        if not mid:
            continue
        out.append({
            "id": mid,
            "name": it.get("display_name") or mid,
            # Anthropic doesn't expose context_window in /v1/models, but
            # registry has it. Caller's normalization stage backfills.
        })
    return out


def _fetch_google(provider_id: str, timeout: float) -> Any:
    """Google AI Studio (generativelanguage.googleapis.com) /v1beta/models."""
    import httpx
    api_key = _resolve_api_key(provider_id)
    if not api_key:
        return {"error": "No GOOGLE_GENERATIVE_AI_API_KEY set"}
    try:
        r = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("models") or []):
        # name field is "models/gemini-2.5-flash" — strip prefix.
        raw = it.get("name") or ""
        mid = raw.split("/", 1)[1] if "/" in raw else raw
        if not mid:
            continue
        methods = it.get("supportedGenerationMethods") or []
        # Filter to chat-style models; skip embeddings, AQA, etc.
        if methods and "generateContent" not in methods:
            continue
        entry = {
            "id": mid,
            "name": it.get("displayName") or mid,
        }
        ctx = it.get("inputTokenLimit")
        if ctx:
            entry["context_window"] = int(ctx)
        out.append(entry)
    return out


def _fetch_bedrock(provider_id: str, timeout: float) -> Any:
    """Amazon Bedrock list_foundation_models via boto3."""
    try:
        import boto3
    except ImportError:
        return {"error": "boto3 not installed (pip install boto3)"}
    try:
        client = boto3.client("bedrock")
        resp = client.list_foundation_models()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for m in resp.get("modelSummaries", []):
        mid = m.get("modelId")
        if not mid:
            continue
        # Filter to TEXT input/output models. Skip image/embedding.
        if "TEXT" not in (m.get("inputModalities") or []):
            continue
        if "TEXT" not in (m.get("outputModalities") or []):
            continue
        out.append({
            "id": mid,
            "name": m.get("modelName") or mid,
        })
    return out


def _fetch_github_copilot(provider_id: str, timeout: float) -> Any:
    """GitHub Copilot /v1/models — needs the per-session bearer token
    from the github-copilot provider's token cache. Live fetch only
    works if a chat session has been opened recently (token in cache)."""
    import httpx
    try:
        from openprogram.providers.github_copilot.token_cache import (
            get_cached_token,
        )
        token = get_cached_token()
    except Exception:
        token = None
    if not token:
        return {"error": (
            "Copilot needs a live session token. Open a chat with any "
            "Copilot model once so the token cache populates, then retry."
        )}
    try:
        r = httpx.get(
            "https://api.githubcopilot.com/models",
            headers={
                "Authorization": f"Bearer {token}",
                "Copilot-Integration-Id": "vscode-chat",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = []
    for it in (data.get("data") or []):
        mid = it.get("id")
        if not mid:
            continue
        caps = it.get("capabilities", {}) or {}
        entry = {
            "id": mid,
            "name": it.get("name") or mid,
        }
        if caps.get("supports", {}).get("vision"):
            entry["vision"] = True
        ctx = caps.get("limits", {}).get("max_context_window_tokens")
        if ctx:
            entry["context_window"] = int(ctx)
        out.append(entry)
    return out


def _fetch_claude_code(provider_id: str, timeout: float) -> Any:
    """Claude Code proxy daemon — OpenAI-compatible /v1/models.

    The proxy speaks OpenAI Completions protocol and exposes the
    Claude models available through the user's Claude Code session.
    No API key needed; the proxy reuses the OAuth credentials in the
    Claude Code keychain.
    """
    import os, httpx
    base = (os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = httpx.get(base + "/v1/models", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": (
            f"Proxy not reachable at {base}. Is `claude-max-api` running? ({e})"
        )}
    items = data.get("data") or data.get("models") or []
    return items if isinstance(items, list) else {"error": "unexpected response shape"}


def _fetch_codex_static(provider_id: str, timeout: float) -> Any:
    """OpenAI Codex (openai-codex) — no public list API.

    The ChatGPT backend returns 403 on /models. Instead, re-emit the
    registry's curated Codex catalog so the Fetch button at least
    refreshes from upstream registry edits. Useful when we ship new
    model rows in models_generated.py — users hit Fetch to see them.
    """
    from openprogram.providers.models_generated import MODELS
    out = []
    for v in MODELS.values():
        if v.provider != "openai-codex":
            continue
        out.append({
            "id": v.id,
            "name": v.name,
            "context_window": v.context_window,
            "vision": "image" in (v.input or []),
            "reasoning": bool(v.reasoning),
        })
    if not out:
        return {"error": "No Codex models in registry"}
    return out


# Provider id → fetcher function. Providers in _FETCH_MODELS_PROVIDERS
# (OpenAI-compatible) use _fetch_openai_compat by default; explicit
# entries here override.
_FETCHERS: dict[str, Any] = {
    "anthropic": _fetch_anthropic,
    "claude-code": _fetch_claude_code,  # local proxy, no API key
    "openai-codex": _fetch_codex_static,
    "google": _fetch_google,
    "amazon-bedrock": _fetch_bedrock,
    "github-copilot": _fetch_github_copilot,
}


def fetch_models_remote(provider_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Dispatch to a provider-specific fetcher and merge into custom_models.

    The fetcher returns either a list of model dicts (success) or a dict
    with an "error" key (failure). Each provider needs its own auth +
    response-shape handling — OpenAI-compatible providers share one
    fetcher, anthropic/google/bedrock/copilot each have their own.

    Returns dict: {"fetched": N, "added": N, "models": [ids...]} on success,
    {"error": "..."} on failure.
    """
    fetcher = _FETCHERS.get(provider_id)
    if fetcher is None and provider_id in _FETCH_MODELS_PROVIDERS:
        fetcher = _fetch_openai_compat
    if fetcher is None:
        return {"error": (
            f"{_label(provider_id)} has no list-models API available. "
            "Models are curated manually for this provider."
        )}

    raw = fetcher(provider_id, timeout)
    if isinstance(raw, dict) and "error" in raw:
        return raw
    items = raw if isinstance(raw, list) else []
    if not items:
        return {"error": "No models returned"}

    from openprogram.providers.thinking_catalog import derive_thinking_fields

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
        reasoning_hint = bool(it.get("reasoning"))
        if reasoning_hint:
            entry["reasoning"] = True
        # Derive thinking capability so newly-discovered models come through
        # with a working picker. Static data only — still re-derived at read
        # time in list_models_for_provider to pick up override-table edits.
        levels, default_lv, variant = derive_thinking_fields(
            provider_id, mid, reasoning_hint
        )
        if levels:
            entry["thinking_levels"] = levels
            if default_lv:
                entry["default_thinking_level"] = default_lv
            if variant:
                entry["thinking_variant"] = variant
        models.append(entry)

    # Fetch is authoritative: replace the previous fetched set rather
    # than merge into it. ``replace_fetched_models`` preserves any
    # rows the user added by hand (no ``_source: "fetched"`` marker)
    # so a power user can still pin a row that upstream doesn't list.
    result = replace_fetched_models(provider_id, models)
    return {
        "provider": provider_id,
        "fetched": len(models),
        "added": result["added"],
        "removed": result["removed"],
        "total_custom": result["total"],
        "dropped_enabled": result.get("dropped_enabled", []),
    }


# Which registered API id each provider's models route through. The
# entries here must match strings registered in
# ``providers/register.py::register_builtins``. Used to stamp
# fetched / custom rows with a working ``api`` so the chat dispatcher
# can find a stream function for them — without this the fetch flow
# silently produces ``api: "custom"`` rows that the model picker
# happily lists but the chat path can't run, since the api registry
# has no "custom" entry. Fetched ``claude-sonnet-4-6`` looked usable
# in the UI but silently dropped out of ``/api/models/enabled`` for
# exactly this reason.
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
}


_CODEX_RESPONSES_PROVIDERS = frozenset({"openai-codex"})


def test_provider(provider_id: str, model: str | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Send a one-shot tiny PING to verify api_key + base_url work.

    Uses OpenAI Chat Completions shape for most providers (most
    universal), but routes ChatGPT-subscription / Codex providers
    through ``/codex/responses`` instead. The Codex backend doesn't
    expose ``/chat/completions`` — Cloudflare's anti-abuse rules on
    chatgpt.com return a 403 for that path while letting the Responses
    API through fine. Hitting the wrong path made the catalog UI
    report a healthy Codex provider as "blocked by Cloudflare" even
    though the actual chat traffic worked end-to-end.

    Returns ``{"ok": True, "latency_ms": ...}`` or
    ``{"ok": False, "error": "..."}``.
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

    use_codex_shape = provider_id in _CODEX_RESPONSES_PROVIDERS

    if use_codex_shape:
        # ChatGPT subscription path: Responses API on chatgpt.com.
        # ``store=False`` keeps the request light and avoids polluting
        # the user's Codex run history with PING messages. ``stream``
        # MUST be true — the Codex Responses backend rejects non-stream
        # calls with HTTP 400 "Stream must be set to true". We don't
        # actually consume the stream; we just need the initial status
        # line (200 vs 4xx) to know if auth + model selection is OK.
        url = base.rstrip("/") + "/codex/responses"
        body = {
            "model": model,
            "input": [{"role": "user",
                       "content": [{"type": "input_text", "text": "PING"}]}],
            "instructions": "",
            "stream": True,
            "store": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "originator": "openprogram",
            "OpenAI-Beta": "responses=experimental",
        }
        # openai-codex has no api_key_env — its credential lives in the
        # OAuth pool. ``_resolve_api_key`` only checks env + config so
        # it returns None here; we have to ask AuthManager. Also
        # surface chatgpt-account-id when available so OpenAI's side
        # gets a clean account mapping.
        if not api_key:
            try:
                from openprogram.auth.manager import get_manager
                cred = get_manager().acquire_sync(provider_id)
                api_key = getattr(cred.payload, "access_token", None) or None
                account_id = (getattr(cred.payload, "extra", None) or {}).get("account_id", "")
                if account_id:
                    headers["chatgpt-account-id"] = account_id
            except Exception as _e:
                return {"ok": False,
                        "error": f"No usable Codex credential. "
                                 f"Run `openprogram providers login openai-codex`."}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        url = base.rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "PING"}],
            "max_tokens": 4,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    t0 = _time.time()
    try:
        if use_codex_shape:
            # Codex responds with SSE — a bare ``httpx.post`` would block
            # reading the stream until close. Use ``client.stream`` so
            # we read headers, capture the status, and tear the
            # connection down immediately.
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url, headers=headers, json=body) as r:
                    latency_ms = int((_time.time() - t0) * 1000)
                    if r.status_code != 200:
                        err_body = b"".join(r.iter_bytes()).decode("utf-8", errors="replace")
                        return {"ok": False,
                                "error": f"HTTP {r.status_code}: {err_body[:200]}",
                                "latency_ms": latency_ms}
            return {"ok": True, "latency_ms": latency_ms, "model": model}

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
