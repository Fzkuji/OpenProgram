"""Runtime adapters for providers whose auth needs a dedicated Runtime class.

Most providers run on the base ``Runtime(model="<namespace>:<model>")``. The
subscription / CLI-credential backends adopt a CLI login and attach
per-provider headers, so each has a ``Runtime`` subclass here. The provider
packages under ``openprogram.models.providers`` keep only the model, auth and
wire code those subclasses call.

``PROVIDER_RUNTIMES`` is the registration table the Runtime factory
(``openprogram.runtime.agent.runtime.factory.create_runtime``) reads: provider id
→ (module in this package, class name). Modules load on first use.
"""
from __future__ import annotations

import importlib

PROVIDER_RUNTIMES: dict[str, tuple[str, str]] = {
    "openai-codex": ("openai_codex", "OpenAICodexRuntime"),
    "gemini-cli": ("google_gemini_cli", "GeminiCLIRuntime"),
}


def runtime_class_for(provider: str) -> type | None:
    """The dedicated Runtime class for ``provider``, or None when the base
    Runtime serves it."""
    entry = PROVIDER_RUNTIMES.get(provider)
    if entry is None:
        return None
    module_name, class_name = entry
    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, class_name)


__all__ = ["PROVIDER_RUNTIMES", "runtime_class_for"]
