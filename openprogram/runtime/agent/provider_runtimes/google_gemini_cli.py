"""GeminiCLIRuntime — thin Runtime wrapper over the HTTP provider.

Reuses the ``gemini`` CLI's logged-in OAuth (``~/.gemini/oauth_creds.json``)
but talks to Cloud Code Assist (``cloudcode-pa.googleapis.com``) directly via
the registered ``gemini-subscription`` stream provider — no subprocess.

Mirrors :class:`.openai_codex.OpenAICodexRuntime`:

- CredentialProvider-first credential acquisition with one-shot import from
  ``~/.gemini/oauth_creds.json`` if no pool exists yet
  (:mod:`openprogram.models.providers.adapters.google_gemini_cli.credentials`)
- model id resolved via the ``gemini-subscription/<id>`` registry entries
- streaming + tool-loop + exec-tree recording flow through the default
  ``Runtime`` → ``AgentSession`` → provider path

Usage:
    from openprogram.runtime.agent.runtime.factory import create_runtime
    rt = create_runtime(provider="gemini-cli", model="gemini-2.5-flash")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.models.auth.credential_provider import get_credential_provider
from openprogram.models.providers.adapters.google_gemini_cli.credentials import require_sign_in

from ..runtime import Runtime


_KNOWN_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
]


class GeminiCLIRuntime(Runtime):
    """Runtime burning a Gemini CLI (Google account) subscription via HTTP.

    Args:
        model:   Gemini model id (default ``gemini-2.5-flash``). Must
                 match a ``gemini-subscription/<id>`` entry in the registry.
        system:  Optional system prompt forwarded as ``instructions``.

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, yolo, cli_path).
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        system: Optional[str] = None,
        **_ignored: Any,
    ) -> None:
        # Only that a sign-in exists: one held for a limit is chosen, waited
        # for or reported per request by the credential layer.
        require_sign_in(get_credential_provider())
        # Each request goes out with the sign-in the credential layer
        # chooses (``models/auth/selection.py``).
        super().__init__(model=f"gemini-subscription:{model}")
        self.system = system

    def list_models(self) -> list[str]:
        return list(_KNOWN_GEMINI_MODELS)


__all__ = ["GeminiCLIRuntime"]
