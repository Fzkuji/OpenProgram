"""External catalogues that enrich fetched model rows with metadata
provider APIs don't expose themselves (context window, output cap,
pricing, modalities, …).

The goal is to stop hand-curating per-model dicts inside the fetchers.
Instead, every fetcher returns whatever the upstream ``/v1/models``
endpoint actually serves — usually just the id list — and the enrichers
in this package fill in the rest from community-maintained sources.

Sources tried in order, first hit wins:

  1. ``models_dev`` — the JSON catalogue at ``models.dev/api.json``.
     Maintained by sst.dev / OpenCode community; covers most major
     providers with full context / output / cost / modality info,
     updates roughly daily. This is the cleanest single source.

Adding another source: implement ``lookup(provider_id, model_id) ->
dict | None`` with the normalised field schema below, and append it
to ``_SOURCES`` in this module.

Normalised field schema returned by every source:

* ``name`` — display name (str)
* ``context_window`` — input context budget in tokens (int)
* ``max_tokens`` — per-completion output cap in tokens (int)
* ``reasoning`` — model supports thinking-trace output (bool)
* ``vision`` — model accepts image input (bool)
* ``tools`` — model supports tool / function calling (bool)
* ``input_cost`` — USD per 1M input tokens (float)
* ``output_cost`` — USD per 1M output tokens (float)
* ``cache_read_cost`` — USD per 1M cache-hit input tokens (float)

Any subset of these is fine; missing keys are not filled in.
"""
from __future__ import annotations

from typing import Any

from . import models_dev


_SOURCES = [models_dev]


def enrich(provider_id: str, model_id: str) -> dict[str, Any]:
    """Look up ``(provider_id, model_id)`` across configured sources,
    return the first non-empty hit. Returns an empty dict when no source
    has the model — caller can then fall back to whatever defaults make
    sense (or leave the row sparse and let the UI show blanks)."""
    for src in _SOURCES:
        try:
            hit = src.lookup(provider_id, model_id)
        except Exception:
            # Source-level failure (network, parse) must not break the
            # fetch — degrade gracefully to the next source / no
            # enrichment.
            hit = None
        if hit:
            return hit
    return {}


__all__ = ["enrich"]
