"""Provider alias table — short names users type on the CLI.

The canonical provider id is long and punctuated (``chatgpt-subscription``,
``gemini-subscription``, ``github-copilot``) so the WebUI model picker
stays unambiguous. On the CLI though, people type what they say out
loud: ``codex``, ``claude``, ``gemini``, ``copilot``. This module
resolves those shortcuts.

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
    "codex": "chatgpt-subscription",
    "claude": "anthropic",
    "gemini": "gemini-subscription",
    "copilot": "github-copilot",
    "bedrock": "amazon-bedrock",
    "vertex": "google-vertex",
    # Common typos / dropped hyphens
    "openai-codex-cli": "chatgpt-subscription",
    # Legacy provider name kept as an alias so any session config /
    # external script still referencing "claude-code" continues to
    # resolve auth (it now routes through the same anthropic creds).
    "claude-code": "anthropic",
    "claude-code": "anthropic",
    "claude-code": "anthropic",
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
