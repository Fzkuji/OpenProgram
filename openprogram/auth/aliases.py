"""Provider alias table — short names users type on the CLI.

The canonical provider id matches OpenAI's official naming where one
exists (``openai-codex``, ``gemini-subscription``, ``github-copilot``);
on the CLI people type spoken-word shortcuts (``codex``, ``claude``,
``gemini``, ``copilot``). This module resolves those shortcuts.

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
    "codex": "openai-codex",
    "claude": "anthropic",
    "gemini": "gemini-subscription",
    "copilot": "github-copilot",
    "bedrock": "amazon-bedrock",
    # Common typos / dropped hyphens
    "openai-codex-cli": "openai-codex",
    # Back-compat: ``chatgpt-subscription`` was the canonical id for the
    # OpenAI Codex provider before the rename. Any older agent.json,
    # session record, env override, or external script that still
    # references it keeps resolving to the same runtime / credentials.
    "chatgpt-subscription": "openai-codex",
    # Legacy runtime-prefix back-compat. Older sessions still carry
    # ``claude-max:claude-opus-4`` model strings (from when the runtime
    # tagged things after the underlying claude-max-api-proxy daemon).
    # Map to the canonical ``claude-code`` provider id so model lookups
    # resolve to the same registry entries.
    "claude-max": "claude-code",
    "gemini-cli": "gemini-subscription",
    "github-copilot-cli": "github-copilot",
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
    """Snapshot of alias → canonical mapping, for help text and `list`."""
    return dict(_ALIASES)


__all__ = ["resolve", "known_aliases"]
