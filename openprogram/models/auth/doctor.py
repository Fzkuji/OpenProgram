"""Credential health report: what is wrong with the stored credential pools.

:func:`run_doctor` only analyses and returns findings; ``openprogram
providers doctor`` prints them and the server's ``/api/providers/doctor``
route returns them as JSON.
"""
from __future__ import annotations

import time
from typing import Any

from .store import get_store
from .summary import format_duration


def run_doctor() -> dict[str, Any]:
    """Pure analysis — no printing, no process exit. Returns a dict with
    ``pools_checked`` (providers with credentials) and ``findings``.

    Checks: expired sign-ins, refresh availability, cooling and spent
    credentials, providers with nothing usable, duplicate credential ids,
    imports whose source file is gone.

    Used by ``openprogram providers doctor`` and the
    ``POST /api/providers/doctor`` REST route, so both share one source of truth.
    """
    from .credential_provider import get_provider_config

    store = get_store()
    pools = store.list_pools()

    findings: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, **extra: Any) -> None:
        findings.append({"level": level, "code": code, "message": message, **extra})

    if not pools:
        add("WARN", "no_pools",
            "No credentials configured. Run `openprogram providers setup`.")

    now_ms = int(time.time() * 1000)

    for pool in pools:
        cfg = get_provider_config(pool.provider_id)
        refresh_available = cfg.refresh is not None or cfg.async_refresh is not None

        # Dup credential_id detection (flags storage corruption).
        seen: dict[str, int] = {}
        for c in pool.credentials:
            seen[c.id] = seen.get(c.id, 0) + 1
        for cid, n in seen.items():
            if n > 1:
                add("ERROR", "duplicate_credential_id",
                    f"{pool.provider_id} has "
                    f"{n} credentials with id {cid!r}.",
                    provider=pool.provider_id)

        usable = 0
        for c in pool.credentials:
            # Source-file check — if we imported this from an external
            # path and that path is gone, the user likely `codex logout`-ed
            # and our pool is a stale mirror. Flag so `providers doctor`
            # catches it before the next API call raises a mysterious 401.
            src_path = (c.metadata or {}).get("source_path")
            if src_path:
                from pathlib import Path as _P
                if not _P(src_path).exists():
                    add("WARN", "missing_source_file",
                        f"{pool.provider_id} was imported "
                        f"from {src_path} which no longer exists. "
                        "Consider re-running login if the external CLI logged out.",
                        provider=pool.provider_id,
                        credential_id=c.id,
                        source_path=src_path)

            if not c.enabled:
                continue
            if c.status in ("invalid_key", "needs_login"):
                add("ERROR", c.status,
                    f"{pool.provider_id} credential {c.display_label()} is {c.status}"
                    + (f": {(c.last_error or {}).get('message')}" if c.last_error else "."),
                    provider=pool.provider_id, credential_id=c.id)
                continue
            # Cooldown check.
            cooldown_until = getattr(c, "cooldown_until_ms", 0) or 0
            if cooldown_until and cooldown_until > now_ms:
                add("WARN", "cooling_down" if c.status != "quota" else "quota",
                    f"{pool.provider_id} credential "
                    f"{c.id} cooling down for "
                    f"{format_duration(cooldown_until - now_ms)}.",
                    provider=pool.provider_id,
                    credential_id=c.id)
                continue

            # Expiry on oauth/device_code.
            if c.kind in ("oauth", "device_code"):
                exp = c.payload.data.get("expires_at_ms", 0) or 0
                if exp and exp <= now_ms:
                    if refresh_available or c.read_only:
                        # Read-only: external CLI owns refresh; surface
                        # as WARN because next call will either refresh
                        # or raise AuthReadOnlyError clearly.
                        add("WARN", "expired_token",
                            f"{pool.provider_id} access "
                            "token expired; will refresh on next use."
                            + (" (read-only — external CLI)" if c.read_only else ""),
                            provider=pool.provider_id,
                                    credential_id=c.id)
                        usable += 1  # refresh path makes it usable
                    else:
                        add("ERROR", "expired_no_refresh",
                            f"{pool.provider_id} access "
                            "token expired and no refresh configured. "
                            f"Run `openprogram providers keys {pool.provider_id} add`.",
                            provider=pool.provider_id,
                                    credential_id=c.id)
                        continue
                else:
                    usable += 1
            else:
                usable += 1

            # Refresh wiring sanity — if we have an oauth cred but no
            # refresh registered AND it's not read-only, the user can
            # still use it until expiry but should know.
            if (
                c.kind == "oauth"
                and not refresh_available
                and not c.read_only
            ):
                add("WARN", "no_refresh_registered",
                    f"{pool.provider_id} has an OAuth credential but no "
                    "refresh callback registered — will need manual re-login "
                    "after expiry.",
                    provider=pool.provider_id,
                    )

        if usable == 0 and pool.credentials:
            add("ERROR", "pool_exhausted",
                f"{pool.provider_id} has "
                f"{len(pool.credentials)} credential(s) but none are usable.",
                provider=pool.provider_id)

    return {"pools_checked": len(pools), "findings": findings}


__all__ = ["run_doctor"]
