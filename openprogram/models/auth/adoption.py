"""Adopting sign-ins other tools already hold on this machine.

Other CLIs' login files (Codex, Qwen, GitHub) are *sources*; adopting one
adds its credential to the provider's list, or renews the row of the same
account. ``openprogram providers adopt`` and ``/api/providers/adopt_all``
use these functions. A provider's key environment variables need no
adopting: each set one is already a read-only row (``store.env_rows``).
"""
from __future__ import annotations

from typing import Any

from .store import get_store
from .summary import payload_summary


def _sources() -> list[Any]:
    from openprogram.models.auth.sources import CodexCliSource, GhCliSource, QwenCliSource
    return [CodexCliSource(), QwenCliSource(), GhCliSource()]


def run_adopt_all() -> dict[str, Any]:
    """Adopt every source's credentials; returns a report, prints nothing.

    Idempotent: a credential whose secret the provider's list already holds
    is skipped, and a sign-in of an account already listed renews that row."""
    store = get_store()
    events: list[dict[str, Any]] = []
    adopted = skipped = errored = 0
    for src in _sources():
        try:
            creds = src.try_import(store.root)
        except Exception as e:
            events.append({"level": "error", "source_id": src.source_id, "error": str(e)})
            errored += 1
            continue
        for cred in creds:
            held = {c.payload.auth_value for c in store.stored(cred.provider_id).credentials}
            if cred.payload.auth_value and cred.payload.auth_value in held:
                skipped += 1
                continue
            try:
                kept = store.add_credential(cred)
                adopted += 1
                events.append({"level": "adopted", "provider_id": cred.provider_id,
                               "credential_id": kept.id, "preview": payload_summary(kept),
                               "source_id": src.source_id})
            except Exception as e:
                errored += 1
                events.append({"level": "error", "source_id": src.source_id, "error": str(e),
                               "provider_id": cred.provider_id})
    return {"adopted": adopted, "skipped": skipped, "errored": errored, "events": events}


def source_by_id(source_id: str):
    """The adoptable source named ``source_id`` (``codex_cli``, ``qwen_cli``, ``gh_cli``), or None."""
    return next((s for s in _sources() if s.source_id == source_id), None)


__all__ = ["run_adopt_all", "source_by_id"]
