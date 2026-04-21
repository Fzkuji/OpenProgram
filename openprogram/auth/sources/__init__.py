"""External credential sources.

A *source* discovers credentials that already exist elsewhere on the user's
machine — environment variables, other CLIs' on-disk auth stores — and
offers to adopt them. Every source answers two questions:

  * ``try_import(profile_root)`` — given a fresh/empty profile, what
    credentials can I import right now? Returns zero or more
    :class:`Credential` objects, pre-populated with enough ``source`` /
    ``metadata`` provenance that later rotation or revocation knows where
    they came from.
  * ``removal_steps(cred)`` — if the user says "forget this credential",
    which concrete things need cleaning up? Files we own get deleted
    directly (``executable=True``); external CLI stores and env vars get
    surfaced as instructions the user must run themselves
    (``executable=False``). Without this contract a revoked credential
    silently re-hydrates on the next ``try_import`` — the exact bug
    hermes-agent's source docstring warns about.

Sources never write the store themselves. They produce :class:`Credential`
objects and hand them back; a higher layer decides which pool to drop
them into. This keeps the "discover" / "register" / "use" phases cleanly
separable — you can preview what a source would import without committing.
"""
from __future__ import annotations

from .codex_cli import CodexCliSource
from .claude_code import ClaudeCodeSource
from .env import EnvApiKeySource
from .gh_cli import GhCliSource
from .qwen_cli import QwenCliSource

__all__ = [
    "CodexCliSource",
    "ClaudeCodeSource",
    "EnvApiKeySource",
    "GhCliSource",
    "QwenCliSource",
]
