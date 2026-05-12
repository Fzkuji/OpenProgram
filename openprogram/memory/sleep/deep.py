"""Deep phase — refresh core.md from wiki state.

Under the hybrid schema the wiki no longer carries structured
``Claim`` records — content is prose with `[[wikilinks]]`. The
claim-promotion logic that lived here previously is gone; writes
to the wiki go through the agentic ``ingest`` pipeline instead.

What deep still owns:

  1. Regenerate ``core.md`` from the wiki (deterministic).
  2. (TODO) Detect journal residue and trigger synthetic ingest.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .. import core

logger = logging.getLogger(__name__)


def run(*, llm: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    try:
        core.refresh_from_wiki()
    except Exception as e:  # noqa: BLE001
        logger.warning("deep: core refresh failed: %s", e)
        return {"phase": "deep", "skipped": f"core refresh failed: {e}"}
    return {"phase": "deep", "promoted": 0, "core_refreshed": True}
