"""Credential CLI catalog responsibilities."""
from __future__ import annotations
import json
import sys
from typing import Any, Optional
from ..account.accounts import DEFAULT_ACCOUNT_NAME
from ..account.accounts import get_account_manager
from ..store import get_store


def _cmd_available(
    query: Optional[str], as_json: bool, configured_only: bool = False,
) -> int:
    """List every provider OpenProgram can configure (built-in + the
    models.dev community catalogue), optionally filtered by a search term.
    The ``PROVIDER`` id column is exactly what you pass to
    ``openprogram providers login <id>``."""
    try:
        from openprogram.webui._model_listing import list_providers
        rows = list(list_providers() or [])
    except Exception as e:  # noqa: BLE001
        print(f"Could not load the provider catalogue: {e}", file=sys.stderr)
        return 1

    q = (query or "").strip().lower()
    if q:
        rows = [
            r for r in rows
            if q in (r.get("id") or "").lower()
            or q in (r.get("label") or "").lower()
        ]
    if configured_only:
        rows = [r for r in rows if r.get("configured")]
    # Configured providers first, then alphabetical by id.
    rows.sort(key=lambda r: (not r.get("configured"), (r.get("id") or "")))

    if as_json:
        slim = [{
            "id": r.get("id"),
            "label": r.get("label"),
            "configured": bool(r.get("configured")),
            "enabled": bool(r.get("enabled")),
            "api_key_env": r.get("api_key_env"),
            "model_count": r.get("model_count"),
            "supports_fetch": bool(r.get("supports_fetch")),
        } for r in rows]
        print(json.dumps(slim, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("No providers match that search." if q else "No providers found.")
        return 0

    def _col(key: str, cap: int, header: str) -> int:
        return max(
            min(cap, max((len(str(r.get(key) or "")) for r in rows), default=0)),
            len(header),
        )
    id_w = _col("id", 30, "PROVIDER")
    lbl_w = _col("label", 28, "LABEL")
    env_w = _col("api_key_env", 26, "KEY ENV")

    def _fit(s: str, w: int) -> str:
        # Truncate over-wide values so columns stay aligned.
        s = s or ""
        return s if len(s) <= w else s[: w - 1] + "…"

    print(f"{'PROVIDER':<{id_w}}  {'LABEL':<{lbl_w}}  "
          f"{'KEY ENV':<{env_w}}  MODELS  CFG")
    for r in rows:
        mc = r.get("model_count")
        print(
            f"{_fit(r.get('id'), id_w):<{id_w}}  "
            f"{_fit(r.get('label'), lbl_w):<{lbl_w}}  "
            f"{_fit(r.get('api_key_env') or '—', env_w):<{env_w}}  "
            f"{(str(mc) if mc is not None else '—'):>6}  "
            f"{'✓' if r.get('configured') else '·'}"
        )
    print(
        f"\n{len(rows)} provider(s)"
        + (f" matching {query!r}" if q else "")
        + ".  Configure one with:  openprogram providers login <PROVIDER>"
    )
    return 0



def _cmd_use(provider: str, account: str) -> int:
    """Set which account (account) a provider runs on. Empty account clears the
    pin (back to the default). This is what makes a second logged-in account
    actually take effect at request time."""
    from openprogram.auth.account.account_selection import set_active_account
    from openprogram.auth.account.account_selection import get_active_pin
    set_active_account(provider, account)
    pin = get_active_pin(provider)
    if pin:
        print(f"✓ {provider} now runs on account '{pin}'.")
    else:
        print(f"✓ {provider} cleared — runs on the default account.")
    return 0



def _cmd_list(account_filter: Optional[str], as_json: bool) -> int:
    from .formatting import _payload_summary
    store = get_store()
    pm = get_account_manager()
    pools = store.list_pools()
    if account_filter:
        pools = [p for p in pools if p.account_id == account_filter]

    if as_json:
        out = [
            {
                "provider_id": p.provider_id,
                "account_id": p.account_id,
                "strategy": p.strategy,
                "credentials": [
                    {
                        "id": c.credential_id,
                        "kind": c.kind,
                        "preview": _payload_summary(c),
                        "status": c.status,
                        "read_only": c.read_only,
                    }
                    for c in p.credentials
                ],
            }
            for p in pools
        ]
        print(json.dumps(out, indent=2))
        return 0

    if not pools:
        print("No credential pools yet. Try:")
        print("  openprogram providers discover        # scan for existing credentials")
        print("  openprogram providers login <prov>    # add one manually")
        return 0

    from openprogram.auth.account.account_selection import get_active_pin
    print(f"{'provider':28s}  {'account':16s}  credential")
    for p in pools:
        # Mark the account this provider is pinned to run on (→ active); an
        # unpinned provider runs on 'default'.
        active = get_active_pin(p.provider_id)
        marker = " ← active" if active and active == p.account_id else ""
        for c in p.credentials:
            ro = " [read-only]" if c.read_only else ""
            print(f"{p.provider_id:28s}  {p.account_id:16s}  "
                  f"{c.credential_id} — {_payload_summary(c)}{ro}{marker}")
    return 0



def _cmd_discover(as_json: bool) -> int:
    from .formatting import _payload_summary
    from openprogram.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

    pm = get_account_manager()
    default = pm.get_account(DEFAULT_ACCOUNT_NAME)
    sources: list[Any] = [
        CodexCliSource(),
        QwenCliSource(),
        GhCliSource(),
    ]
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(EnvApiKeySource(provider_id=provider, env_var=env_var))

    found: list[dict[str, Any]] = []
    for src in sources:
        try:
            creds = src.try_import(default.root)
        except Exception as e:
            found.append({"source_id": src.source_id, "error": str(e)})
            continue
        for cred in creds:
            found.append({
                "source_id": src.source_id,
                "provider": cred.provider_id,
                "account": cred.account_id,
                "kind": cred.kind,
                "preview": _payload_summary(cred),
                "read_only": cred.read_only,
            })

    if as_json:
        print(json.dumps(found, indent=2))
        return 0

    if not found:
        print("Nothing found. No existing CLIs or env-var keys detected on this machine.")
        return 0

    print(f"Found {len(found)} adoptable credential(s):\n")
    print(f"{'source':28s}  {'provider':24s}  preview")
    for f in found:
        if "error" in f:
            print(f"{f['source_id']:28s}  (error)                   {f['error']}")
            continue
        print(f"{f['source_id']:28s}  {f['provider']:24s}  {f['preview']}")
    print("\nAdopt one with:  openprogram providers adopt <source_id>")
    return 0



def _cmd_adopt(source_id: str, account: str) -> int:
    from .formatting import _payload_summary
    store = get_store()
    pm = get_account_manager()
    account_obj = pm.get_account(account)

    src = _source_by_id(source_id, account)
    if src is None:
        print(f"Unknown source: {source_id!r}. "
              f"Run `openprogram providers discover` to see available ids.",
              file=sys.stderr)
        return 1

    try:
        creds = src.try_import(account_obj.root)
    except Exception as e:
        print(f"Source failed: {e}", file=sys.stderr)
        return 1
    if not creds:
        print(f"Source {source_id!r} produced no credentials — nothing to adopt.",
              file=sys.stderr)
        return 1

    for cred in creds:
        # Force the caller's requested account — some sources default
        # to "default" but the user may be scoping to "work".
        cred.account_id = account
        store.add_credential(cred)
        print(f"✓ Adopted {cred.provider_id}/{cred.account_id}: {_payload_summary(cred)}")
    return 0



def _cmd_adopt_all(account: str) -> int:
    """Batch-adopt every credential the discovery layer finds (CLI wrapper).

    Delegates to :func:`run_adopt_all` for the actual work; this function
    only formats output. Exit 0 on success (even zero adopted), 1 if
    any source errored.
    """
    result = run_adopt_all(account)
    for item in result["events"]:
        level = item["level"]
        if level == "adopted":
            print(f"  + {item['provider_id']}/{account}: {item['preview']}")
        elif level == "error":
            print(f"  ! {item['source_id']}: {item['error']}", file=sys.stderr)
    print(f"\nAdopted {result['adopted']} · "
          f"skipped {result['skipped']} · errored {result['errored']}")
    return 1 if result["errored"] else 0



def run_adopt_all(account: str) -> dict[str, Any]:
    """Pure batch-adopt — no stdout, returns a structured report.

    Used by the CLI ``adopt --all`` verb and the
    ``POST /api/providers/adopt_all`` REST route.

    Idempotent: a second run skips credentials whose source label
    already appears in the destination pool, so the event-var path
    doesn't accumulate duplicates.
    """
    from .formatting import _payload_summary
    from openprogram.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

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
                    "preview": _payload_summary(cred),
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



def _source_by_id(source_id: str, account: str):
    from openprogram.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

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

