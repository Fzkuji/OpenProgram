"""Environment-variable API-key source.

Handles the long-suffering ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` /
``GOOGLE_API_KEY`` pattern. One source instance per ``(provider_id,
env_var_name)`` pair — providers that accept multiple env var names
(``GOOGLE_API_KEY`` vs ``GEMINI_API_KEY``) register one source per alias
and each independently offers to import.

Why this is a source and not a login method:
the user didn't *log in* — the secret was already sitting in their
shell. We're only adopting it. That distinction matters for the removal
contract: we can't ``unset`` their shell for them, so removal is
instructional (``executable=False``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..types import (
    ApiKeyPayload,
    Credential,
    CredentialSource,
    RemovalStep,
)


@dataclass
class EnvApiKeySource:
    """Reads one named env var, turns it into an ``api_key`` credential.

    ``profile_id`` is a constant — env keys aren't per-profile, they're a
    single global value in the user's shell. The default profile adopts it.
    """

    provider_id: str
    env_var: str
    profile_id: str = "default"
    # Sometimes the env value has a ``Bearer `` prefix from someone copying
    # from a curl. Stripped by default; turn off if a provider really does
    # accept the literal prefix.
    strip_bearer_prefix: bool = True

    @property
    def source_id(self) -> str:
        return f"env:{self.env_var}"

    def try_import(self, profile_root: Path) -> list[Credential]:
        raw = os.environ.get(self.env_var)
        if not raw:
            return []
        value = raw.strip()
        if self.strip_bearer_prefix and value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value:
            return []
        return [
            Credential(
                provider_id=self.provider_id,
                profile_id=self.profile_id,
                kind="api_key",
                payload=ApiKeyPayload(api_key=value),
                source=self.source_id,
                metadata={"env_var": self.env_var},
                # Env-sourced keys are not read-only per se — nothing
                # refreshes them. But we mark them as such so a rotation
                # attempt raises loudly instead of silently no-op'ing.
                read_only=True,
            )
        ]

    def removal_steps(self, cred: Credential) -> list[RemovalStep]:
        # We can't unset a user's shell env. Surface the exact command
        # they need to run — shells vary, so include both POSIX and
        # fish/Windows hints in the description.
        return [
            RemovalStep(
                description=(
                    f"Unset {self.env_var} in your shell (e.g. "
                    f"`unset {self.env_var}` for bash/zsh, "
                    f"`set -e {self.env_var}` for fish, or remove the "
                    f"line from your shell rc / `.env` file). Otherwise "
                    f"it will be re-imported on the next restart."
                ),
                executable=False,
                kind="env",
                target=self.env_var,
            )
        ]
