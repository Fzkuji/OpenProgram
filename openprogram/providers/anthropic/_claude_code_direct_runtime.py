"""claude-code provider — direct subscription-OAuth runtime.

Replaces the Meridian-daemon-backed ``ClaudeCodeRuntime``
(``_max_proxy_runtime``) with a direct connection to
``api.anthropic.com``, mirroring how ``openai-codex`` connects straight
to ``chatgpt.com/backend-api`` with a subscription OAuth token.

How it works:

  * The model is mapped onto the ``anthropic:<id>`` namespace so the
    standard Anthropic Messages wire (``providers/anthropic/anthropic.py``)
    handles it. That wire sniffs the ``sk-ant-oat`` token prefix and
    switches to Bearer auth + Claude Code identity headers
    (``anthropic-beta: claude-code-20250219,oauth-2025-04-20,…``,
    ``user-agent: claude-cli/<ver>``) — exactly what a Claude
    subscription needs, no Meridian proxy.

  * The token resolves from the ``anthropic`` AuthStore pool
    (``resolve_api_key_sync``), which covers a plain api-key, an adopted
    OAuth credential, and a ``cli_delegated`` pointer at Claude Code's
    own ``~/.claude/.credentials.json`` (re-read every call so the CLI's
    rotations propagate for free).

The provider name stays ``claude-code`` (registry entry, WebUI/CLI);
only the wire underneath changed.
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


# Family alias → newest anthropic catalog id. The runtime accepts a bare
# alias (``claude-opus-4``), a proxy alias, or any version-suffixed /
# env-detected id; we fold onto the family so callers always land on a
# real, current model rather than letting an unknown id 404.
_FAMILY_DEFAULT = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}


import re as _re

# A bare family alias: claude-<family>-<major>, nothing more specific. These
# get expanded to a current default. A more-specific id (extra version digit
# or a date, e.g. claude-opus-4-8 / claude-opus-4-5-20251101) is passed
# through verbatim — we TRUST it (Anthropic's API rejects a bad id) rather
# than folding it onto the local catalog, which lags new releases (4.7/4.8)
# and would silently downgrade the user's pick.
_BARE_FAMILY_RE = _re.compile(r"^claude-(opus|sonnet|haiku)-\d+$")


def _normalize_model(model: str) -> str:
    """Expand a bare family alias to a current default; pass anything more
    specific through untouched.

    ``claude-opus-4``      → ``claude-opus-4-6`` (bare alias, expanded)
    ``claude-opus-4-8``    → ``claude-opus-4-8`` (specific, trusted as-is)
    ``claude-opus-4-5-20251101`` → unchanged
    Unknown/garbage → Sonnet default (constructor fallback).
    """
    m = (model or "").strip().lower()
    if not m:
        return _FAMILY_DEFAULT["sonnet"]
    bare = _BARE_FAMILY_RE.match(m)
    if bare:
        return _FAMILY_DEFAULT[bare.group(1)]
    # Anything that names a Claude family but is more specific than the bare
    # alias: trust it verbatim (covers 4.7 / 4.8 / fable / dated ids the
    # local catalog hasn't caught up to).
    if m.startswith("claude-") or m.startswith("claude"):
        return m
    if "opus" in m:
        return _FAMILY_DEFAULT["opus"]
    if "haiku" in m:
        return _FAMILY_DEFAULT["haiku"]
    if "sonnet" in m:
        return _FAMILY_DEFAULT["sonnet"]
    return _FAMILY_DEFAULT["sonnet"]


def ensure_anthropic_model_registered(mid: str) -> str:
    """Make sure ``anthropic/<mid>`` exists in the ENABLED_MODELS registry, so the
    runtime can resolve it even when the local catalog lags a new release
    (4.7 / 4.8 / fable-5 aren't in enabled_models yet). Mirrors codex's
    ``ensure_codex_model_registered``. Idempotent. Returns ``mid``.

    Copies an existing anthropic Claude entry as the template (same wire,
    base_url, modalities) and overrides id/name. Context window defaults to
    the family's known size — the catalog enrichment refines it later.
    """
    from openprogram.providers.enabled_models import ENABLED_MODELS

    key = f"anthropic/{mid}"
    if key in ENABLED_MODELS:
        return mid
    template = next(
        (m for m in ENABLED_MODELS.values()
         if m.provider == "anthropic" and m.api == "anthropic-messages"
         and m.id.startswith("claude")),
        None,
    )
    if template is None:
        return mid  # no anthropic entries to mirror; let the runtime raise.
    # The [1m] opt-in suffix ALWAYS means a 1M window. Otherwise newer
    # generations (4.6+/fable) default to 1M; older stay at the template's.
    if "[1m]" in mid:
        ctx = 1_000_000
    elif any(t in mid for t in ("4-6", "4-7", "4-8", "fable")):
        ctx = 1_000_000
    else:
        ctx = template.context_window
    from openprogram.providers.thinking_spec import derive_thinking_fields
    levels, default, variant = derive_thinking_fields(
        "anthropic", mid, True, True,
    )
    ENABLED_MODELS[key] = template.model_copy(update={
        "id": mid,
        "name": mid,
        "context_window": ctx,
        "reasoning": True,
        "thinking_levels": levels,
        "default_thinking_level": default,
        "thinking_variant": variant,
    })
    return mid


class ClaudeCodeRuntime(Runtime):
    """Runtime for the ``claude-code`` provider — direct OAuth, no proxy.

    Keeps the class name ``ClaudeCodeRuntime`` so the registry entry and
    any importer are unchanged; the implementation now drives the
    ``anthropic:<id>`` direct wire instead of the local Meridian daemon.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4",
        max_retries: int = 2,
        base_url: Optional[str] = None,  # noqa: ARG002 — kept for API parity
        **_unused,
    ) -> None:
        # Validate a credential EXISTS, but do NOT pin the token onto the
        # runtime. The subscription OAuth token expires (~8h); pinning it at
        # construction means a long-lived runtime (worker reuses one across
        # turns) keeps sending a stale token — the wire then skips re-resolve
        # (opts.api_key is set) so it never refreshes, and Anthropic 400s
        # "credit balance too low" (treating the expired token as a plain
        # pay-as-you-go call). Leaving api_key unpinned makes the anthropic
        # wire re-resolve (and AuthManager-refresh) on EVERY turn.
        if not api_key:
            from openprogram.auth.resolver import resolve_api_key_sync
            if not resolve_api_key_sync("anthropic"):
                raise ValueError(
                    "No Claude credential. Log in with a Claude subscription "
                    "so its OAuth token is adopted, or add an Anthropic API "
                    "key in Settings → Providers."
                )
        resolved = _normalize_model(model)
        # Register the id if the local catalog doesn't have it yet (new
        # releases the direct subscription serves but enabled_models lags).
        ensure_anthropic_model_registered(resolved)
        super().__init__(
            model=f"anthropic:{resolved}",
            api_key=api_key,  # None unless caller passed one — wire re-resolves
            max_retries=max_retries,
        )


__all__ = ["ClaudeCodeRuntime"]
