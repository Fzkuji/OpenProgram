"""Codex client identity and on-demand model registration.

The Codex backend gates newer model ids behind a recognised client identity
(``originator: codex_cli_rs`` plus a minimum ``version``), and dispatch only
resolves ids present in ``ENABLED_MODELS``. This module owns both: the
client version every Codex request presents, and the registration of one
advertised model id into the registry.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__).warning


# Client version we present to the Codex backend. The models endpoint gates
# some ids behind a minimum client version (``gpt-5.6-*`` needs ≥ 0.144.0), and
# dispatch only serves a model to a recognised ``originator: codex_cli_rs`` at
# or above that version — so this string must track a real released CLI.
_CODEX_CLIENT_VERSION = "0.153.4"


@lru_cache(maxsize=1)
def codex_client_version() -> str:
    """Resolve the official local Codex version, with a bundled fallback."""
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        payload = json.loads((root / "models_cache.json").read_text(encoding="utf-8"))
        cached = payload.get("client_version") if isinstance(payload, dict) else None
        if isinstance(cached, str) and re.fullmatch(r"\d+\.\d+\.\d+", cached):
            return cached
    except (OSError, ValueError, TypeError):
        pass
    executable = shutil.which("codex")
    if executable:
        try:
            completed = subprocess.run(
                [executable, "--version"], capture_output=True, text=True,
                timeout=2, check=False,
            )
            match = re.search(r"\b(\d+\.\d+\.\d+)\b", completed.stdout)
            if match:
                return match.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    return _CODEX_CLIENT_VERSION


def _codex_supports_xhigh(model_id: str) -> bool:
    return any(tag in model_id for tag in (
        "gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6",
    ))


def _display_name_for_codex_model(model_id: str) -> str:
    parts = model_id.replace("gpt-", "").split("-")
    head = "GPT-" + parts[0]
    tail = " ".join(p.capitalize() if p != "codex" else "Codex" for p in parts[1:])
    return (head + " " + tail).strip()


def ensure_codex_model_registered(mid: str) -> None:
    """Register one Codex model id into the static ENABLED_MODELS registry so the
    runtime can actually resolve it.

    A still-prefixed id (``openai-codex:gpt-5.5``) must NEVER be registered:
    it becomes a picker-visible ghost row (``_display_name_for_codex_model``
    prettifies it to "GPT-openai Codex:5.5") that shadows the real model.
    Callers are expected to pass a BARE id; refuse ``:`` ids with a warning
    naming the caller so the runtime surfaces the honest "Unknown model"
    instead of a half-working ghost.

    The Codex Runtime adapter resolves a model id against the ENABLED_MODELS dict —
    a codex id that isn't registered raises ``Unknown model`` at dispatch, with
    no custom-model fallback on this path. So anything we *list* must also be
    *registered* here. Idempotent; missing fields (cost / context) ride on the
    template until enrichment fills the listing.

    Used by the authenticated model-catalogue fetch and by the runtime miss
    path, so newly advertised ids work without a source update."""
    if ":" in mid:
        import traceback
        caller = "".join(traceback.format_stack(limit=3)[:-1]).strip()
        _log(f"[codex] refusing to register model id with ':' (prefixed?): "
             f"{mid!r} — pass a bare id. Caller:\n{caller}")
        return

    from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
    from openprogram.models.providers.settings.thinking_spec import derive_thinking_fields

    template = next(
        (m for m in ENABLED_MODELS.values()
         if m.provider == "openai-codex" and m.api == "openai-codex"),
        None,
    )
    if template is None:
        return  # registry has no Codex entries at all; nothing to mirror.

    key = f"openai-codex/{mid}"
    display = _display_name_for_codex_model(mid)
    model = ENABLED_MODELS.get(key) or template.model_copy(update={"id": mid, "name": display})
    levels, default, variant = derive_thinking_fields(
        "openai-codex",
        mid,
        True,
        _codex_supports_xhigh(mid),
    )
    from openprogram.models.providers.catalog.enabled_models import default_fast
    ENABLED_MODELS[key] = model.model_copy(update={
        "id": mid,
        "name": model.name or display,
        "fast": default_fast(mid),
        "reasoning": True,
        "thinking_levels": levels,
        "default_thinking_level": default,
        "thinking_variant": variant,
    })


# No import-time registry seeding. The registry is built from config spec
# rows only (docs/design/providers/models/overview.md §4.2).
