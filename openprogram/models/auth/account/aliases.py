"""Provider alias table — short names users type on the CLI.

The canonical provider id matches the catalogue's naming (``anthropic``,
``amazon-bedrock``); on the CLI people type spoken-word shortcuts
(``claude``, ``gemini``, ``bedrock``). This module resolves those shortcuts.

Design:

* One-way alias → canonical. We never print the alias back; all output
  uses the canonical id so logs are unambiguous.
* Resolution is a pure function; no side effects, no registry mutation
  from user code.
* Aliases live here, not in each provider dir, so that conflicts are
  visible at review time (two providers can't claim the same alias
  without the conflict showing in one PR).

To add a new alias: edit :data:`_ALIASES`. That's it.
"""
from __future__ import annotations


_ALIASES: dict[str, str] = {
    # Spoken-word shortcuts
    "claude": "anthropic",
    "gemini": "google",
    "bedrock": "amazon-bedrock",
    "claude-max": "claude-code",
    # Back-compat: this provider (Alibaba's token-plan.cn OpenAI-compatible
    # endpoint) was called ``bailian`` before it was renamed to match
    # models.dev's ``alibaba-token-plan-cn``. Old agent.json / session /
    # enabled-model refs that still say ``bailian`` resolve to the same
    # registry entries and credentials.
    "bailian": "alibaba-token-plan-cn",
    # Keep identity mappings so round-tripping through resolve is safe.
    # (Canonical ids go through unchanged.)
}


def resolve(provider: str) -> str:
    """Return the canonical provider id for ``provider``.

    Unknown strings are returned unchanged — we don't second-guess
    provider names the user might be on the bleeding edge of. The CLI
    layer catches genuinely-wrong ids when the store lookup misses.
    """
    return _ALIASES.get(provider, provider)


def known_aliases() -> dict[str, str]:
    """Copy of alias → canonical mapping, for help text and `list`."""
    return dict(_ALIASES)


__all__ = ["resolve", "known_aliases"]
