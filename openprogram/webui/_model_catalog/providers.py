"""Provider-level metadata: display labels, env-var mappings, default
API routes, configured-status detection.

Two-layer source of truth:

  1. **Community catalogue** (``sources.models_dev``) — 135 providers
     with normalised ``label`` / ``env_var`` / ``base_url`` / ``doc_url``.
     This is the *default* answer for any provider we don't have a
     manual override for. New providers on models.dev appear
     automatically the moment the in-process 1h TTL cache refreshes.

  2. **Manual overrides** below — only entries here that *differ from
     what models.dev says* genuinely need to live in code. The legacy
     full-set tables (21 providers, hand-maintained) collapsed down
     to the handful where we want to:
       * pin a friendlier label (e.g. "MiniMax (CN)" vs upstream's "Minimax"),
       * declare a provider that isn't on models.dev,
       * route a fetched row through a non-standard API id.

The public lookup functions (``_label``, ``_env_var_for``,
``_default_api_for``) check overrides first and fall through to
``sources.models_dev``. That's why the override dicts are short.
"""
from __future__ import annotations

import shutil
from typing import Any


# Display labels for provider ids. Anything not listed falls back to
# models.dev's ``name`` field, then a prettified id ("amazon-bedrock"
# -> "Amazon Bedrock") as a last resort.
_PROVIDER_LABELS: dict[str, str] = {
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
    # MiniMax ships two regions (minimax.io = International, minimaxi.com
    # = China) × two billing modes (pay-as-you-go API vs the "Token Plan"
    # coding-plan subscription). Label by region word, not the bare domain
    # — "International" / "CN" reads at a glance where ".io" vs ".com"
    # doesn't. (The two -coding-plan ids come from models.dev; their
    # labels apply via _label() now that the community tier routes through
    # the override map too.)
    "minimax": "MiniMax (International)",
    "minimax-cn": "MiniMax (CN)",
    "minimax-coding-plan": "MiniMax Token Plan (International)",
    "minimax-cn-coding-plan": "MiniMax Token Plan (CN)",
    "huggingface": "HuggingFace",
    "github-copilot": "GitHub Copilot",
    "kimi-coding": "Kimi Coding",
    "vercel-ai-gateway": "Vercel AI Gateway",
    "opencode": "OpenCode",
    "deepseek": "DeepSeek",
    # Claude via local HTTP proxy daemon (replaces the old Claude Code
    # CLI provider). Tools come from OpenProgram's own registry instead
    # of the CLI's built-ins.
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


# Providers whose base URL speaks the OpenAI-compatible /v1/models
# listing (Bearer auth, standard {data:[{id:...}]} response). Everything
# else either has no public listing or uses a custom auth / response
# shape and so has a dedicated entry in ``fetchers._FETCHERS`` instead.
_FETCH_MODELS_PROVIDERS = frozenset({
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
    "deepseek",
    # Excluded deliberately:
    #   anthropic      — /v1/models uses x-api-key header, not Bearer
    #   google*        — custom endpoints / OAuth
    #   azure-*        — needs deployment name not model id
    #   amazon-bedrock — AWS SigV4
    #   openai-codex   — ChatGPT backend, no public listing (403)
    #   github-copilot — private OAuth with custom headers
    #   opencode       — not verified; add if/when tested
})


_ENV_API_KEYS: dict[str, str | None] = {
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
    # Token Plan (subscription) shares the region account's key — same as the
    # plain-API sibling above. Pin here too so the listing's api_key_env /
    # base resolution don't depend on the models.dev catalogue being reachable.
    "minimax-coding-plan": "MINIMAX_API_KEY",
    "minimax-cn-coding-plan": "MINIMAX_API_KEY",
    "huggingface": "HF_TOKEN",
    "github-copilot": None,  # OAuth
    "kimi-coding": "MOONSHOT_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "opencode": None,
    "openai-codex": None,  # OAuth via ~/.codex/auth.json
    "deepseek": "DEEPSEEK_API_KEY",
}


# Manual override for the ``api`` (wire / stream-function id) a
# provider's fetched/custom models route through. **Normally empty.**
# ``_default_api_for`` derives the api from the provider's own static
# models (``models_generated``), which always WINS for a single-api
# provider — so a fetched row matches the static catalogue and a stale
# entry here can't re-introduce drift. This table is therefore consulted
# ONLY when derivation is impossible:
#   * a multi-api provider (models_generated lists several wires) that
#     needs a deliberate default routing choice; or
#   * a community provider with no static row AND no ``…/anthropic``
#     base for the heuristic to catch.
# To fix a single-api provider that's mislabelled, fix ``models_generated``
# (regenerate) — an entry here would be ignored. Values must match a
# string registered in ``providers/register.py::register_builtins`` (a
# "custom" stamp has no stream function and drops the model from chat).
_PROVIDER_DEFAULT_API: dict[str, str] = {}


def _prettify(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _models_dev_info(provider_id: str) -> dict[str, Any]:
    """Cached lookup against ``sources.models_dev.provider_info``.

    Lazy imported because ``sources`` is a sibling subpackage and a
    top-level import would form a circular-import path on cold start
    (sources → providers → sources). Always returns a dict to
    simplify call sites; ``{}`` on cache miss / network failure.
    """
    try:
        from .sources import models_dev
    except Exception:
        return {}
    try:
        return models_dev.provider_info(provider_id) or {}
    except Exception:
        return {}


def _label(provider_id: str) -> str:
    """Manual override → models.dev → prettified id. Manual override is
    only useful when we want a different display name from upstream
    (rare — most fall through to models.dev's clean ``name`` field)."""
    if provider_id in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider_id]
    md = _models_dev_info(provider_id)
    if md.get("label"):
        return md["label"]
    return _prettify(provider_id)


def _env_var_for(provider_id: str) -> str | None:
    """Env var name holding the API key for ``provider_id``. Same
    override-first semantics as ``_label`` — manual ``_ENV_API_KEYS``
    entry wins (so we can pin "claude-code → None" even if a future
    models.dev edit gives it an env), otherwise models.dev's first
    ``env[]`` entry, otherwise ``None``."""
    if provider_id in _ENV_API_KEYS:
        return _ENV_API_KEYS[provider_id]
    md = _models_dev_info(provider_id)
    return md.get("env_var")


def _static_apis_for(provider_id: str) -> set[str]:
    """The set of wire ``api`` ids the provider's OWN static-registry
    models declare (``{}`` for a community-only provider)."""
    try:
        from openprogram.providers.models_generated import MODELS
        return {m.api for m in MODELS.values() if m.provider == provider_id}
    except Exception:
        return set()


def _default_api_for(provider_id: str) -> str | None:
    """Registered ``api`` id a provider's fetched/custom models dispatch
    through. **Derived**, not hand-maintained per provider, so a fetched
    row always routes the same way as that provider's static rows and
    can't drift:

      1. The provider's OWN static models' wire api, when unambiguous —
         the source of truth (``models_generated``). This means a fetched
         model is stamped the SAME ``api`` as the static catalogue, so it
         can't route worse than the rows that already work.
      2. A manual override (``_PROVIDER_DEFAULT_API``) — kept only for
         the irreducible cases: multi-api providers that need a routing
         choice, or community providers with no static row.
      3. Community heuristic: an Anthropic-compatible endpoint
         conventionally lives at ``…/anthropic[/v1]``. Detect it from the
         community base so a NEW Anthropic-wire provider works with zero
         code — the same gap that silently broke MiniMax's Token-Plan
         rows.

    Returns ``None`` when nothing matches; callers default to
    ``"openai-completions"`` for unknown OpenAI-compatible providers."""
    apis = _static_apis_for(provider_id)
    if len(apis) == 1:
        return next(iter(apis))
    if provider_id in _PROVIDER_DEFAULT_API:
        return _PROVIDER_DEFAULT_API[provider_id]
    base = (_default_base_url_for(provider_id) or "").rstrip("/")
    if base.endswith("/anthropic") or base.endswith("/anthropic/v1"):
        return "anthropic-messages"
    return None


def _default_base_url_for(provider_id: str) -> str | None:
    """Default API base URL for ``provider_id``. Pulled straight from
    models.dev's ``api`` field; static-registry ``Model.base_url`` is
    still preferred when the runtime has one baked in."""
    md = _models_dev_info(provider_id)
    return md.get("base_url")


def _doc_url_for(provider_id: str) -> str | None:
    """Provider docs URL (for the "Get an API key →" link in the
    setup hint). models.dev only — there's no manual table for this."""
    md = _models_dev_info(provider_id)
    return md.get("doc_url")


def _auth_store_has_credential(provider_id: str) -> bool:
    """True when OpenProgram's own auth store holds a credential for
    ``provider_id`` (alias-aware).

    Every native login — codex PKCE OAuth, github-copilot device-code,
    anthropic / gemini-subscription CLI-import — writes a Credential into
    this store (see ``auth/methods/``), and the store-backed runtimes
    authenticate from it (``manager.acquire_sync``). So a credential here
    is the most authoritative "configured" signal there is, independent of
    whether an env var or external CLI dotfile was also populated.
    """
    try:
        from openprogram.auth.manager import get_manager
        from openprogram.auth.aliases import resolve as _canon
        target = _canon(provider_id)
        store = get_manager().store
        return any(
            _canon(p.provider_id) == target and p.credentials
            for p in store.list_pools()
        )
    except Exception:
        return False


def _is_configured(provider_id: str) -> bool:
    """Is this provider usable (credential in our store, key present, CLI
    binary found, or local daemon responding).

    Detection tiers, in order:

    * **Auth store** — a credential saved by any native login (the
      authoritative signal; see :func:`_auth_store_has_credential`).
    * CLI-backed providers — presence of their binary on PATH.
    * Two special-case providers — ``openai-codex`` (also accepts the
      shared ``~/.codex/auth.json``) and ``claude-code`` (HEAD against the
      Meridian / claude-max-api daemon's health endpoint).
    * Everything else — env-var key set.
    """
    # Authoritative: a credential in OpenProgram's own auth store. This is
    # where every native login (OAuth / device-code / CLI-import) lands and
    # what the store-backed runtimes (codex, gemini-subscription,
    # github-copilot, imported anthropic) read. Without this, a web/CLI
    # login that never also set an env var / external dotfile was reported
    # unconfigured and the chat model picker silently dropped its models.
    if _auth_store_has_credential(provider_id):
        return True
    # CLI-backed: binary presence decides.
    for cli in _CLI_PROVIDERS:
        if cli["id"] == provider_id:
            return shutil.which(cli["cli_binary"]) is not None
    # openai-codex also counts when the Codex CLI's shared auth file exists
    # (~/.codex/auth.json) — same OpenAI account, even if we haven't stored
    # our own credential yet. (The store case is handled above.)
    if provider_id == "openai-codex":
        from pathlib import Path
        return (Path.home() / ".codex" / "auth.json").exists()
    # claude-code: there's no env-key path; the daemon is "ready" when
    # its HTTP endpoint answers. Quick 0.5s probe — failure means the
    # user hasn't started ``meridian`` (or claude-max-api) yet.
    if provider_id == "claude-code":
        import os
        import urllib.error
        import urllib.request
        # Both meridian and claude-max-api-proxy default to port 3456.
        # Strip a trailing /v1 because /health lives at root.
        url = (
            os.environ.get("CLAUDE_MAX_PROXY_URL") or "http://localhost:3456"
        ).rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        try:
            with urllib.request.urlopen(url + "/health", timeout=0.5):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            return False
    # Key-based providers (incl. the Bedrock/Vertex cloud-credential chains):
    # the canonical is_configured — env-var key, or a satisfied cloud
    # chain. See docs/design/providers/api-key-resolution-unification.md.
    from openprogram.providers.env_api_keys import env_vars_for, is_configured
    if env_vars_for(provider_id) or provider_id in ("amazon-bedrock", "google-vertex"):
        return is_configured(provider_id)
    # Community / models.dev provider with a single env-var name we don't have
    # in the canonical table: check the env var.
    env = _env_var_for(provider_id)
    if env is None:
        # OAuth / no-key / unknown — conservatively "configured" so the UI
        # doesn't show a red dot the user can't act on.
        return True
    import os
    return bool(os.environ.get(env))
