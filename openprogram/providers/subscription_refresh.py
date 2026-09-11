"""Background refresh loop for account-scoped subscription model catalogues."""
from __future__ import annotations

import os
import random
import threading

from .subscription_catalog import SUBSCRIPTION_PROVIDERS, catalog_is_stale

_DEFAULT_INTERVAL_S = 6 * 60 * 60


def refresh_stale_catalogues(*, max_age_s: float = _DEFAULT_INTERVAL_S) -> list[str]:
    """Refresh configured stale subscription providers; never raise."""
    refreshed: list[str] = []
    try:
        from openprogram.providers.metadata import is_configured
        from openprogram.webui._model_listing import fetch_models_remote
    except Exception:
        return refreshed
    for provider_id in sorted(SUBSCRIPTION_PROVIDERS):
        try:
            if not is_configured(provider_id) or not catalog_is_stale(provider_id, max_age_s):
                continue
            result = fetch_models_remote(provider_id)
            if isinstance(result, dict) and not result.get("error"):
                refreshed.append(provider_id)
        except Exception:
            continue
    return refreshed


def start_subscription_catalog_refresher() -> tuple[threading.Event, threading.Thread]:
    """Start one daemon using stale-while-refresh semantics."""
    interval = max(300.0, float(os.environ.get(
        "OPENPROGRAM_SUBSCRIPTION_CATALOG_INTERVAL_S", _DEFAULT_INTERVAL_S,
    )))
    stop = threading.Event()

    def run() -> None:
        # Avoid competing with provider warm-up and channel startup.
        if stop.wait(2.0):
            return
        while not stop.is_set():
            refresh_stale_catalogues(max_age_s=interval)
            jitter = random.uniform(0.9, 1.1)
            if stop.wait(interval * jitter):
                return

    thread = threading.Thread(
        target=run, daemon=True, name="subscription-catalog-refresh",
    )
    thread.start()
    return stop, thread


__all__ = ["refresh_stale_catalogues", "start_subscription_catalog_refresher"]
