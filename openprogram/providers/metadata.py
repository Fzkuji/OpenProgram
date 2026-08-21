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

The public lookup functions (``label_for``, ``env_var_for``,
``default_api_for``) check overrides first and fall through to
``sources.models_dev``. That's why the override dicts are short.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any


# Display labels for provider ids. Anything not listed falls back to
# models.dev's ``name`` field, then a prettified id ("amazon-bedrock"
# -> "Amazon Bedrock") as a last resort.
PROVIDER_LABELS: dict[str, str] = {
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
    # labels apply via label_for() now that the community tier routes through
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
    # Zen 的姊妹线路：Go 订阅走 /zen/go/v1，模型集与计费都和 Zen 分开。
    "opencode-go": "OpenCode Go",
    "deepseek": "DeepSeek",
    # Claude via local HTTP proxy daemon (replaces the old Claude Code
    # CLI provider). Tools come from OpenProgram's own registry instead
    # of the CLI's built-ins.
    "claude-code": "Claude Code",
    "xai-subscription": "Grok Subscription",
    # CLI-backed:
    "gemini-cli": "Gemini CLI",
}


# CLI-backed providers aren't in the HTTP provider registry. Currently
# empty: 'gemini-cli' historically lived here, but it shares one
# Runtime + auth flow with 'gemini-subscription' (the Cloud Code Assist
# endpoint), so listing it separately just duplicated the row. New
# CLI-only backends with no HTTP counterpart can be added back here.
CLI_PROVIDERS: list[dict[str, Any]] = []


# Providers whose base URL speaks the OpenAI-compatible /v1/models
# listing (Bearer auth, standard {data:[{id:...}]} response). Everything
# else either has no public listing or uses a custom auth / response
# shape and so ships its own ``providers/<name>/list_models.py`` instead.
FETCH_MODELS_PROVIDERS = frozenset({
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


ENV_API_KEYS: dict[str, str | None] = {
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
    "opencode-go": "OPENCODE_API_KEY",
    "openai-codex": None,  # OAuth via ~/.codex/auth.json
    "xai-subscription": None,  # SuperGrok / X Premium+ OAuth
    "deepseek": "DEEPSEEK_API_KEY",
}


# Manual override for the ``api`` (wire / stream-function id) a
# provider's fetched/custom models route through. **Normally empty.**
# ``default_api_for`` derives the api from the provider's own static
# models (``enabled_models``), which always WINS for a single-api
# provider — so a fetched row matches the static catalogue and a stale
# entry here can't re-introduce drift. This table is therefore consulted
# ONLY when derivation is impossible:
#   * a multi-api provider (enabled_models lists several wires) that
#     needs a deliberate default routing choice; or
#   * a community provider with no static row AND no ``…/anthropic``
#     base for the heuristic to catch.
# To fix a single-api provider that's mislabelled, fix ``enabled_models``
# (regenerate) — an entry here would be ignored. Values must match a
# string registered in ``providers/register.py::register_builtins`` (a
# "custom" stamp has no stream function and drops the model from chat).
PROVIDER_DEFAULT_API: dict[str, str] = {}


def _shipped_provider_ids() -> set[str]:
    """Provider ids shipped in this package (the sidebar's static tier).

    Enumerated from ``providers/<dir>/provider.json`` files that declare an
    ``endpoints`` map — that's what separates a real dispatchable provider
    from a wire-format metadata dir (``openai_completions`` etc. ship a
    provider.json with thinking specs only). Plus ``claude-code``, which has
    no dir of its own (it rides the anthropic runtime / credential pool) but
    must still get a sidebar row on a fresh install.
    """
    return set(shipped_provider_ids()) | {"claude-code"}


def prettify_provider_id(provider_id: str) -> str:
    return " ".join(w.capitalize() for w in provider_id.replace("_", "-").split("-"))


def _models_dev_info(provider_id: str) -> dict[str, Any]:
    """Cached lookup against ``sources.models_dev.provider_info``.

    Lazy imported because ``sources`` is a sibling subpackage and a
    top-level import would form a circular-import path on cold start
    (sources → providers → sources). Always returns a dict to
    simplify call sites; ``{}`` on cache miss / network failure.
    """
    try:
        from openprogram.providers.sources import models_dev
    except Exception:
        return {}
    try:
        return models_dev.provider_info(provider_id) or {}
    except Exception:
        return {}


def label_for(provider_id: str) -> str:
    """Manual override → models.dev → prettified id. Manual override is
    only useful when we want a different display name from upstream
    (rare — most fall through to models.dev's clean ``name`` field)."""
    if provider_id in PROVIDER_LABELS:
        return PROVIDER_LABELS[provider_id]
    md = _models_dev_info(provider_id)
    if md.get("label"):
        return md["label"]
    return prettify_provider_id(provider_id)


def env_var_for(provider_id: str) -> str | None:
    """Env var name holding the API key for ``provider_id``. Same
    override-first semantics as ``label_for`` — manual ``ENV_API_KEYS``
    entry wins (so we can pin "claude-code → None" even if a future
    models.dev edit gives it an env), otherwise models.dev's first
    ``env[]`` entry, otherwise ``None``."""
    if provider_id in ENV_API_KEYS:
        return ENV_API_KEYS[provider_id]
    md = _models_dev_info(provider_id)
    return md.get("env_var")


def synthesized_env_var(provider_id: str) -> str:
    """Synthesised ``<ID>_API_KEY`` env-var LABEL for a custom provider with no
    real env-var mapping (e.g. ``frontier-intelligence`` →
    ``FRONTIER_INTELLIGENCE_API_KEY``). DISPLAY ONLY: runtime credentials
    resolve from the AuthStore keyed by provider id, never from this env var.
    Kept out of ``env_var_for`` so it can't leak into ``env_vars_for`` and
    flip ``is_configured`` for community providers."""
    slug = "".join(c if (c.isalnum() or c == "-") else "-" for c in provider_id)
    slug = slug.strip("-").upper().replace("-", "_")
    return f"{slug}_API_KEY" if slug else "API_KEY"


def _static_apis_for(provider_id: str) -> set[str]:
    """The set of wire ``api`` ids the provider's OWN static-registry
    models declare (``{}`` for a community-only provider).

    Prefers the self-contained ``providers/<p>/provider.json`` metadata
    (no ``ENABLED_MODELS`` read, breaks the providers<->webui circular dep);
    falls back to the legacy ``enabled_models.ENABLED_MODELS`` scan while both
    sources coexist during the migration.
    """
    try:
        apis = provider_apis(provider_id)
        if apis:
            return apis
    except Exception:
        pass
    try:
        from openprogram.providers.enabled_models import ENABLED_MODELS
        return {m.api for m in ENABLED_MODELS.values() if m.provider == provider_id}
    except Exception:
        return set()


def default_api_for(provider_id: str) -> str | None:
    """Registered ``api`` id a provider's fetched/custom models dispatch
    through. **Derived**, not hand-maintained per provider, so a fetched
    row always routes the same way as that provider's static rows and
    can't drift:

      1. The provider's OWN static models' wire api, when unambiguous —
         the source of truth (``enabled_models``). This means a fetched
         model is stamped the SAME ``api`` as the static catalogue, so it
         can't route worse than the rows that already work.
      2. A manual override (``PROVIDER_DEFAULT_API``) — kept only for
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
    if provider_id in PROVIDER_DEFAULT_API:
        return PROVIDER_DEFAULT_API[provider_id]
    base = (default_base_url_for(provider_id) or "").rstrip("/")
    if base.endswith("/anthropic") or base.endswith("/anthropic/v1"):
        return "anthropic-messages"
    return None


def default_base_url_for(provider_id: str) -> str | None:
    """Default API base URL for ``provider_id``. Pulled straight from
    models.dev's ``api`` field; static-registry ``Model.base_url`` is
    still preferred when the runtime has one baked in."""
    md = _models_dev_info(provider_id)
    return md.get("base_url")


def doc_url_for(provider_id: str) -> str | None:
    """Provider docs URL (for the "Get an API key →" link in the
    setup hint). models.dev only — there's no manual table for this."""
    md = _models_dev_info(provider_id)
    return md.get("doc_url")


def auth_store_has_credential(provider_id: str) -> bool:
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
        from openprogram.auth.credential_provider import get_credential_provider
        from openprogram.auth.aliases import resolve as _canon
        target = _canon(provider_id)
        store = get_credential_provider().store
        return any(
            _canon(p.provider_id) == target and p.credentials
            for p in store.list_pools()
        )
    except Exception:
        return False


def is_configured(provider_id: str) -> bool:
    """Is this provider usable (credential in our store, key present, CLI
    binary found, or local daemon responding).

    Detection tiers, in order:

    * **Auth store** — a credential saved by any native login (the
      authoritative signal; see :func:`auth_store_has_credential`).
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
    if auth_store_has_credential(provider_id):
        return True
    # CLI-backed: binary presence decides.
    for cli in CLI_PROVIDERS:
        if cli["id"] == provider_id:
            return shutil.which(cli["cli_binary"]) is not None
    # openai-codex also counts when the Codex CLI's shared auth file exists
    # (~/.codex/auth.json) — same OpenAI account, even if we haven't stored
    # our own credential yet. (The store case is handled above.)
    if provider_id == "openai-codex":
        from pathlib import Path
        return (Path.home() / ".codex" / "auth.json").exists()
    # claude-code runs direct on the anthropic subscription — configured
    # when the anthropic credential pool holds a token (no daemon probe).
    if provider_id == "claude-code":
        return auth_store_has_credential("anthropic")
    # Key-based providers (incl. the Bedrock/Vertex cloud-credential chains):
    # the canonical is_configured — env-var key, or a satisfied cloud
    # chain. See docs/design/providers/auth/api-key-resolution-unification.md.
    from openprogram.providers.env_api_keys import (
        env_vars_for, is_configured as key_is_configured)
    if env_vars_for(provider_id) or provider_id in ("amazon-bedrock", "google-vertex"):
        return key_is_configured(provider_id)
    # Custom (user-added) provider: no env-var mapping and no key concept in the
    # catalogue, so the community fall-through below would report it configured
    # unconditionally. It's configured only with a real credential — a pool
    # entry (the AuthStore check above) or its synthesised env var being set.
    from openprogram.providers.storage import _is_custom_provider
    if _is_custom_provider(provider_id):
        import os
        return bool(os.environ.get(synthesized_env_var(provider_id)))
    # Community / models.dev provider: a saved key would have hit the
    # AuthStore check at the top. Providers with no key concept at all
    # (no env-var name in the catalogue) are conservatively "configured"
    # so the UI doesn't show a red dot the user can't act on.
    env = env_var_for(provider_id)
    return env is None


# ── provider.json endpoint metadata ──────────────────────────────
_ROOT = Path(__file__).parent
_logger = logging.getLogger(__name__)


def _read_provider_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("provider metadata must be an object")
        return value
    except FileNotFoundError:
        return None
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        _logger.warning(
            "provider metadata load failed",
            extra={
                "source": "provider_json",
                "path": str(path),
                "error_type": type(exc).__name__,
            },
        )
        return None


def _provider_dir(provider_id: str) -> Path | None:
    # Same hyphen->underscore resolution as provider_models._provider_dir, but
    # WITHOUT importing webui (that would re-create the cycle this module breaks).
    # Read-only: never creates a dir.
    for name in (provider_id, provider_id.replace("-", "_")):
        d = _ROOT / name
        if (d / "provider.json").is_file():
            return d
    return None


# Providers that ship no dir of their own because they ride another
# provider's wire. ``claude-code`` is the Claude subscription connected
# direct to api.anthropic.com through the anthropic Messages wire + the
# anthropic credential pool (providers/anthropic/_claude_code_direct_runtime.py,
# providers/anthropic/anthropic.py::_pool). Without this its model rows get
# stamped api="openai-completions" + a hostless base_url, and every
# stream_simple/complete_simple call dies with the nonsense
# ``ValueError: unknown url type: '/chat/completions'`` instead of talking to
# Anthropic. Resolved here, in the single dir lookup, so provider_apis(),
# provider_base_url() and resolved_endpoints() all agree.
_WIRE_SIBLING: dict[str, str] = {"claude-code": "anthropic"}


def _endpoints(provider_id: str) -> dict:
    d = _provider_dir(_WIRE_SIBLING.get(provider_id, provider_id))
    if d is None:
        return {}
    metadata = _read_provider_json(d / "provider.json")
    return (metadata.get("endpoints") or {}) if metadata is not None else {}


def resolved_endpoints(provider_id: str) -> dict:
    """Endpoint map for a provider, resolving the empty-dir gap.

    Community / token-plan providers (``minimax-cn-coding-plan``) and
    alias-only providers (``chatgpt-subscription`` → ``openai-codex``) ship
    an EMPTY provider dir — no ``provider.json`` — so their own endpoints are
    ``{}`` and rows built off them get a hostless base_url. Fill order:

      1. The provider's own ``provider.json`` endpoints (unchanged path).
      2. Its alias target's endpoints (``chatgpt-subscription`` inherits
         ``openai-codex``'s api + base_url, so the row routes to the codex
         transport that resolves credentials under the canonical id).
      3. A synthetic ``{"default": {api, base_url}}`` from models.dev's
         base_url — with ``anthropic-messages`` when that base is an
         ``/anthropic`` endpoint, else ``openai-completions``.
    """
    eps = _endpoints(provider_id)
    if eps:
        return eps
    try:
        from openprogram.auth.aliases import resolve as _canon
        canon = _canon(provider_id)
    except Exception:
        canon = provider_id
    if canon != provider_id:
        eps = _endpoints(canon)
        if eps:
            return eps
    # A ``-coding-plan`` token-plan provider shares its region sibling's
    # wire (same account, same endpoint) — e.g. ``minimax-cn-coding-plan``
    # → ``minimax-cn``. Derive OFFLINE from the sibling's provider.json
    # before touching models.dev: the models.dev catalogue is an in-memory,
    # network-fetched, TTL cache that is EMPTY at cold import (and the
    # registry snapshots at import), so a network-only path silently yields
    # a hostless base_url in a fresh process.
    if provider_id.endswith("-coding-plan"):
        eps = _endpoints(provider_id[: -len("-coding-plan")])
        if eps:
            return eps
    base = (default_base_url_for(provider_id) or "").rstrip("/")
    if not base:
        return {}
    api = "anthropic-messages" if (
        base.endswith("/anthropic") or base.endswith("/anthropic/v1")
    ) else "openai-completions"
    return {"default": {"api": api, "base_url": base}}


def shipped_provider_ids() -> list[str]:
    """Ids of every provider shipped as ``providers/<dir>/provider.json``
    with a non-empty ``endpoints`` map. Dirs whose provider.json carries
    only wire-format metadata (thinking specs — ``openai_completions``,
    ``openai_responses``, ``google_gemini_cli``) are not providers a user
    can enable and are excluded by the endpoints check."""
    out: list[str] = []
    for d in sorted(_ROOT.iterdir()):
        f = d / "provider.json"
        if not f.is_file():
            continue
        j = _read_provider_json(f)
        if j is None:
            continue
        if j.get("endpoints"):
            out.append(j.get("id") or d.name.replace("_", "-"))
    return out


def provider_endpoints(provider_id: str) -> dict:
    """Full endpoint map {name: {api, base_url}}, resolving empty provider
    dirs via alias / models.dev (see :func:`resolved_endpoints`)."""
    return resolved_endpoints(provider_id)


def provider_apis(provider_id: str) -> set[str]:
    return {e.get("api") for e in _endpoints(provider_id).values() if e.get("api")}


def provider_base_url(provider_id: str) -> str | None:
    eps = _endpoints(provider_id)
    ep = eps.get("default") or (next(iter(eps.values())) if eps else None)
    return ep.get("base_url") if ep else None
