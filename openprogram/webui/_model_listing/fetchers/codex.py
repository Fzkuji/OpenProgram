"""OpenAI Codex (``openai-codex``) model fetcher.

The ChatGPT/Codex backend has **no** public list-models API — ``/models``
403s behind Cloudflare — so we can't query it directly. Instead we treat the
community catalogue (``models.dev``, which tracks the OpenAI family) as the
source of truth for a Fetch:

  1. take the OpenAI catalogue (``openai-codex`` aliases to ``openai`` in
     ``models_dev``), pulled live behind models.dev's shared 1h cache,
  2. keep the Codex/ChatGPT-subscription-runnable GPT-5.x reasoning family,
  3. register each surfaced id so the runtime can actually dispatch it, and
  4. let ``fetch_models_remote`` enrich the rest from models.dev.

This replaces the old "re-emit the in-code registry" behaviour, so a Fetch
reflects what the upstream catalogue lists today rather than a hand-kept list.

Two consequences worth knowing:

  * Ids that exist in our in-code registry but **not** in models.dev (e.g. a
    ``-codex`` tier models.dev doesn't track) are not surfaced by a Fetch. The
    live catalogue is treated as authoritative; pin such a model by hand if you
    need it.
  * If models.dev is unreachable we return an **error** rather than falling
    back to the in-code registry — re-emitting the seed would resurrect ids the
    catalogue doesn't list and, on a re-fetch, overwrite a good saved list with
    a stale one. An errored Fetch leaves the existing ``custom_models`` intact.
"""
from __future__ import annotations

from typing import Any


def _is_codex_runnable(mid: str, info: dict[str, Any]) -> bool:
    """Deterministic filter over models.dev's ``openai`` ids selecting the
    Codex/ChatGPT-subscription family.

    The dotted ``gpt-5.x`` reasoning family, minus the ``chat-latest`` aliases
    and the ``nano`` tier (not served by the Codex subscription). The trailing
    dot in ``gpt-5.`` is deliberate — it keeps the dotted minor families and
    excludes the original no-dot ``gpt-5`` / ``gpt-5-codex`` / ``gpt-5-mini``
    rows. (Rule verified against the live catalogue; see the
    codex-live-fetch-design investigation.)"""
    return (
        mid.startswith("gpt-5.")
        and bool(info.get("reasoning"))
        and "chat" not in mid
        and "nano" not in mid
    )


def _fetch_codex_live(provider_id: str, timeout: float) -> Any:
    """Live Codex fetch via the models.dev OpenAI catalogue.

    Returns ``{"error": ...}`` when the catalogue is unreachable so the
    orchestrator leaves the saved model list untouched (rather than rotating in
    a stale/in-code fallback)."""
    from ..sources import models_dev

    try:
        catalogue = models_dev.list_models("openai-codex")  # alias -> "openai"
    except Exception:
        catalogue = {}

    if not catalogue:
        return {
            "error": (
                "could not reach the models.dev catalogue — your existing "
                "Codex model list was kept. Try Fetch again when online."
            )
        }

    from openprogram.providers.openai_codex.runtime import (
        ensure_codex_model_registered,
    )

    out: list[dict[str, Any]] = []
    for mid in sorted(catalogue):
        info = catalogue[mid] or {}
        if not _is_codex_runnable(mid, info):
            continue
        # A listed id must also be dispatchable — register it in the static
        # ENABLED_MODELS registry (idempotent) so OpenAICodexRuntime can resolve it.
        ensure_codex_model_registered(mid)
        out.append({
            "id": mid,
            "name": info.get("name") or mid,
            "context_window": info.get("context_window") or 0,
            "vision": bool(info.get("vision")),
            "reasoning": bool(info.get("reasoning")),
        })

    if not out:
        return {"error": "models.dev returned no Codex-runnable models"}
    return out
