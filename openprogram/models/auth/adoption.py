"""Adopting credentials that already exist on this machine.

Other CLIs' login files (Codex, Qwen, GitHub) and provider API-key
environment variables are *sources*; adopting one copies its credential into
the store. ``openprogram providers adopt`` and the server's
``/api/providers/adopt_all`` route both use these functions.
"""
from __future__ import annotations

from typing import Any

from .account.accounts import get_account_manager
from .store import get_store
from .summary import payload_summary


def run_adopt_all(account: str) -> dict[str, Any]:
    """Pure batch-adopt — no stdout, returns a structured report.

    Used by ``openprogram providers adopt --all`` and the
    ``POST /api/providers/adopt_all`` REST route.

    Idempotent: a second run skips credentials whose source label
    already appears in the destination pool, so the event-var path
    doesn't accumulate duplicates.
    """
    from openprogram.models.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.models.providers.settings.env_api_keys import PROVIDER_ENV_VARS

    store = get_store()
    pm = get_account_manager()
    account_obj = pm.get_account(account)

    sources: list[Any] = [
        CodexCliSource(account_id=account),
        QwenCliSource(account_id=account),
        GhCliSource(),
    ]
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(
            EnvApiKeySource(provider_id=provider, env_var=env_var, account_id=account),
        )

    events: list[dict[str, Any]] = []
    adopted = 0
    skipped = 0
    errored = 0
    for src in sources:
        try:
            creds = src.try_import(account_obj.root)
        except Exception as e:
            events.append({
                "level": "error",
                "source_id": src.source_id,
                "error": str(e),
            })
            errored += 1
            continue
        for cred in creds:
            cred.account_id = account
            existing = store.find_pool(cred.provider_id, cred.account_id)
            if existing is not None and any(
                c.source == cred.source for c in existing.credentials
            ):
                skipped += 1
                continue
            try:
                store.add_credential(cred)
                adopted += 1
                events.append({
                    "level": "adopted",
                    "provider_id": cred.provider_id,
                    "preview": payload_summary(cred),
                    "source_id": src.source_id,
                })
            except Exception as e:
                errored += 1
                events.append({
                    "level": "error",
                    "source_id": src.source_id,
                    "error": str(e),
                    "provider_id": cred.provider_id,
                })

    return {
        "adopted": adopted,
        "skipped": skipped,
        "errored": errored,
        "events": events,
        "account": account,
    }


def source_by_id(source_id: str, account: str):
    """The adoptable source named ``source_id`` (``codex_cli``, ``env:OPENAI_API_KEY`` …), or None."""
    from openprogram.models.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.models.providers.settings.env_api_keys import PROVIDER_ENV_VARS

    if source_id == "codex_cli":
        return CodexCliSource(account_id=account)
    if source_id == "qwen_cli":
        return QwenCliSource(account_id=account)
    if source_id == "gh_cli":
        return GhCliSource()
    if source_id.startswith("env:"):
        env_var = source_id[4:]
        provider = next(
            (p for p, v in PROVIDER_ENV_VARS.items() if v == env_var), None,
        )
        if provider is None:
            return None
        return EnvApiKeySource(provider_id=provider, env_var=env_var, account_id=account)
    return None


__all__ = ["run_adopt_all", "source_by_id"]
