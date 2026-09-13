"""Credential CLI doctor responsibilities."""
from __future__ import annotations
import json
import time
from typing import Any
from ..account.accounts import DEFAULT_ACCOUNT_NAME
from ..account.accounts import get_account_manager
from ..store import get_store


def _cmd_doctor(as_json: bool) -> int:
    """Run the full credential health report.

    Thin wrapper over :func:`run_doctor` — the shared analysis is in that
    function so the REST route can reuse it without re-implementing the
    check matrix.
    """
    report = run_doctor()
    findings = report["findings"]

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor_report(
            report["pools_checked"],
            report["accounts_checked"],
            findings,
        )

    has_error = any(f["level"] == "ERROR" for f in findings)
    return 1 if has_error else 0



def run_doctor() -> dict[str, Any]:
    """Pure analysis — no printing, no process exit. Returns a dict with
    ``pools_checked``, ``accounts_checked``, ``findings``.

    Checks (a superset of OpenClaw's ``doctor-auth`` list adapted for
    our store layout): expired OAuth, refresh availability, cooldown
    state, pool exhaustion, orphaned account references, empty accounts,
    duplicate credential ids, imported creds whose source file is gone.

    Used by the CLI ``doctor`` verb and the ``POST /api/providers/doctor``
    REST route. Extracting this makes both share one source of truth.
    """
    from .formatting import _fmt_duration
    from ..credential_provider import get_provider_config

    store = get_store()
    pm = get_account_manager()
    accounts = {p.name: p for p in pm.list_accounts()}
    pools = store.list_pools()

    findings: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, **extra: Any) -> None:
        findings.append({"level": level, "code": code, "message": message, **extra})

    if not pools:
        add("WARN", "no_pools",
            "No credential pools configured. Run `openprogram providers setup`.")

    # Accounts referenced by pools but not registered with AccountManager.
    pool_account_ids = {p.account_id for p in pools}
    orphaned_account_refs = pool_account_ids - set(accounts.keys())
    for pid in sorted(orphaned_account_refs):
        add("ERROR", "orphan_profile_ref",
            f"Pool references account {pid!r} which no longer exists.",
            account=pid)

    # Accounts that exist but nobody's logged into.
    empty_accounts = sorted(set(accounts.keys()) - pool_account_ids)
    for pid in empty_accounts:
        if pid == DEFAULT_ACCOUNT_NAME:
            continue  # default is expected to start empty
        add("INFO", "empty_profile",
            f"Account {pid!r} has no credentials yet.",
            account=pid)

    now_ms = int(time.time() * 1000)

    for pool in pools:
        cfg = get_provider_config(pool.provider_id)
        refresh_available = cfg.refresh is not None or cfg.async_refresh is not None

        # Dup credential_id detection (flags storage corruption).
        seen: dict[str, int] = {}
        for c in pool.credentials:
            seen[c.credential_id] = seen.get(c.credential_id, 0) + 1
        for cid, n in seen.items():
            if n > 1:
                add("ERROR", "duplicate_credential_id",
                    f"Pool {pool.provider_id}/{pool.account_id} has "
                    f"{n} credentials with id {cid!r}.",
                    provider=pool.provider_id, account=pool.account_id)

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
                        f"{pool.provider_id}/{pool.account_id} was imported "
                        f"from {src_path} which no longer exists. "
                        "Consider re-running login if the external CLI logged out.",
                        provider=pool.provider_id, account=pool.account_id,
                        credential_id=c.credential_id,
                        source_path=src_path)

            # Cooldown check.
            cooldown_until = getattr(c, "cooldown_until_ms", 0) or 0
            if cooldown_until and cooldown_until > now_ms:
                add("WARN", "cooling_down",
                    f"{pool.provider_id}/{pool.account_id} credential "
                    f"{c.credential_id} cooling down for "
                    f"{_fmt_duration(cooldown_until - now_ms)}.",
                    provider=pool.provider_id, account=pool.account_id,
                    credential_id=c.credential_id)
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
                            f"{pool.provider_id}/{pool.account_id} access "
                            "token expired; will refresh on next use."
                            + (" (read-only — external CLI)" if c.read_only else ""),
                            provider=pool.provider_id,
                            account=pool.account_id,
                            credential_id=c.credential_id)
                        usable += 1  # refresh path makes it usable
                    else:
                        add("ERROR", "expired_no_refresh",
                            f"{pool.provider_id}/{pool.account_id} access "
                            "token expired and no refresh configured. "
                            f"Run `openprogram providers login {pool.provider_id}`.",
                            provider=pool.provider_id,
                            account=pool.account_id,
                            credential_id=c.credential_id)
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
                    account=pool.account_id)

        if usable == 0 and pool.credentials:
            add("ERROR", "pool_exhausted",
                f"{pool.provider_id}/{pool.account_id} has "
                f"{len(pool.credentials)} credential(s) but none are usable.",
                provider=pool.provider_id, account=pool.account_id)

    return {
        "pools_checked": len(pools),
        "accounts_checked": len(accounts),
        "findings": findings,
    }



def _print_doctor_report(pools_count: int, accounts_count: int, findings) -> None:
    print(f"Checked {pools_count} pool(s) across {accounts_count} account(s).\n")
    if not findings:
        print("✓ All checks passed.")
        return
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings_sorted = sorted(findings, key=lambda f: (order.get(f["level"], 3), f["code"]))
    tag = {"ERROR": "✗ ERROR", "WARN": "⚠ WARN ", "INFO": "· INFO "}
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for f in findings_sorted:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
        print(f"{tag.get(f['level'], f['level']):8s}  [{f['code']}] {f['message']}")
    print()
    print(f"Summary: {counts.get('ERROR', 0)} error(s), "
          f"{counts.get('WARN', 0)} warning(s), "
          f"{counts.get('INFO', 0)} info.")

