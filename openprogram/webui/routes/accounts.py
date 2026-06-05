"""Generic per-provider account management — the surface CLI / web / TUI share
to list / activate / rename / remove / add the multiple accounts a provider can
run on.

claude-code keeps its Meridian-backed routes (the literal
``/api/providers/claude-code/accounts/*`` handlers in ``routes/providers.py``,
registered first so they shadow the ``{provider}`` routes here); every OTHER
provider is served from this module, backed by the AuthStore (one credential
pool per profile) and the per-provider active selector (``auth/active.py``). The
response shapes deliberately match claude-code's — ``{installed, ready, active,
accounts:[{name,email,...}]}`` — so a single ``<ProviderAccounts>`` React
component and a single Ink picker drive every provider without branching on
identity.

An *account is a profile*: the AuthStore keys each credential pool by
``(provider_id, profile_id)``, so "multiple accounts" and "multiple profiles"
are the same concept — each account is a profile id. The active account is the
per-provider pin in ``~/.openprogram/auth/_active.json`` (empty pin ⇒ the
``default`` profile is in effect).

Add flow: generic providers report ``add_mode="login"`` and the UI drives the
unified login endpoints (``/api/providers/{id}/login/{start,poll,submit}``) with
``profile=<new account name>``; claude-code reports ``add_mode="code_paste"`` and
uses its own ``/accounts/add`` + ``/accounts/add/code`` pair. Everything else
(list / use / rename / remove) is identical across both backends.

Sync ``def`` handlers so the blocking store I/O runs in FastAPI's threadpool.
"""
from __future__ import annotations

import time

from fastapi.responses import JSONResponse

# The pool rotation strategies a user can pick (auth/types.py PoolStrategy).
_STRATEGIES = ("fill_first", "round_robin", "random", "least_used")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cooling(cred) -> bool:
    """True if this credential is in a cooldown window right now."""
    until = getattr(cred, "cooldown_until_ms", 0) or 0
    return bool(until and until > _now_ms())


def _account_record(pool) -> dict:
    """One account row from a credential pool: name + best-effort identity +
    health, mirroring claude-code's ``{name, email}`` (plus ``kind/status/count``
    + the pool's rotation ``strategy`` and how many keys are currently cooling
    down). Never includes the secret itself."""
    creds = list(pool.credentials)
    first = creds[0] if creds else None
    email = ""
    for c in creds:
        meta = getattr(c, "metadata", None) or {}
        email = meta.get("email") or meta.get("account") or ""
        if email:
            break
    return {
        "name": pool.profile_id,
        "email": email,
        "kind": getattr(first, "kind", "") if first else "",
        "status": getattr(first, "status", "") if first else "empty",
        "count": len(creds),
        "strategy": getattr(pool, "strategy", "fill_first"),
        "cooling": sum(1 for c in creds if _cooling(c)),
    }


def _key_view(cred, active_id: str = "") -> dict:
    """One credential ("key") within an account, masked, with its user-given
    name, its health (for a per-key badge), and whether it's the active/pinned
    one. Never includes the secret itself."""
    payload = getattr(cred, "payload", None)
    raw = getattr(payload, "api_key", "") or getattr(payload, "access_token", "") or ""
    masked = (raw[:6] + "…" + raw[-4:]) if len(raw) > 12 else (("•" * len(raw)) if raw else "")
    meta = getattr(cred, "metadata", None) or {}
    return {
        "credential_id": cred.credential_id,
        "name": meta.get("label") or meta.get("name") or "",
        "kind": cred.kind,
        "status": cred.status,
        "masked": masked,
        "is_active": cred.credential_id == active_id,
        "cooling": _cooling(cred),
        "cooldown_until_ms": getattr(cred, "cooldown_until_ms", 0) or 0,
        "use_count": getattr(cred, "use_count", 0) or 0,
        "last_error": getattr(cred, "last_error", None),
        "source": getattr(cred, "source", ""),
    }


def _generic_summary(provider: str) -> dict:
    """Account state for a non-claude-code provider, shaped like
    :func:`_meridian_cli.accounts_summary` so the UI doesn't branch."""
    from openprogram.auth.store import get_store
    from openprogram.auth.active import get_active_pin
    from openprogram.auth.login_methods import login_methods, default_method

    store = get_store()
    pools = [p for p in store.list_pools() if p.provider_id == provider]
    # default first, then the rest alphabetically — stable, predictable order.
    pools.sort(key=lambda p: (p.profile_id != "default", p.profile_id))
    accounts = [_account_record(p) for p in pools]
    methods = [{"id": mid, "label": label} for mid, label in login_methods(provider)]
    return {
        "installed": True,
        "ready": True,
        "active": get_active_pin(provider),  # "" ⇒ the default profile is in effect
        "accounts": accounts,
        "add_mode": "login",
        "login_methods": methods,
        "default_method": default_method(provider),
    }


def register(app):
    @app.get("/api/providers/{provider}/accounts")
    def api_accounts(provider: str):
        # claude-code is served by the literal route in routes/providers.py;
        # guard anyway so a registration-order change can't silently 404 it.
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content={**_acc.accounts_summary(), "add_mode": "code_paste"})
        return JSONResponse(content=_generic_summary(provider))

    @app.post("/api/providers/{provider}/accounts/use")
    def api_accounts_use(provider: str, body: dict = None):
        """Activate an account (empty name clears the pin → back to default)."""
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.activate_account((body or {}).get("name", "")))
        from openprogram.auth.active import set_active_profile, get_active_pin
        name = (body or {}).get("name", "")
        set_active_profile(provider, name)
        return JSONResponse(content={"active": get_active_pin(provider)})

    @app.post("/api/providers/{provider}/accounts/remove")
    def api_accounts_remove(provider: str, body: dict = None):
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.remove_account((body or {}).get("name", "")))
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        name = (body or {}).get("name", "").strip()
        cleared = False
        if name:
            get_store().delete_pool(provider, name)
            if get_active_pin(provider) == name:
                set_active_profile(provider, "")
                cleared = True
        return JSONResponse(content={"removed": bool(name), "name": name,
                                     "cleared_active": cleared})

    @app.post("/api/providers/{provider}/accounts/rename")
    def api_accounts_rename(provider: str, body: dict = None):
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            b = body or {}
            return JSONResponse(content=_acc.rename_account(b.get("old", ""), b.get("new", "")))
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        from openprogram.auth.types import CredentialPool

        b = body or {}
        old = (b.get("old") or "").strip()
        new = (b.get("new") or "").strip()
        if not old or not new:
            return JSONResponse(content={"ok": False, "error": "both old and new names are required"})
        if new == old:
            return JSONResponse(content={"ok": True, "name": new})
        store = get_store()
        if store.find_pool(provider, new) is not None:
            return JSONResponse(content={"ok": False, "error": f"account '{new}' already exists"})
        pool = store.find_pool(provider, old)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": f"account '{old}' not found"})
        # Re-key every credential onto the new profile, write the new pool, then
        # drop the old file (put-before-delete so a crash never loses the creds).
        for c in pool.credentials:
            c.profile_id = new
        moved = CredentialPool(
            provider_id=provider, profile_id=new, strategy=pool.strategy,
            credentials=pool.credentials, fallback_chain=pool.fallback_chain,
        )
        store.put_pool(moved)
        store.delete_pool(provider, old)
        if get_active_pin(provider) == old:
            set_active_profile(provider, new)
        return JSONResponse(content={"ok": True, "name": new})

    @app.post("/api/providers/{provider}/accounts/add")
    def api_accounts_add(provider: str, body: dict = None):
        """Generic add hands the UI the login methods + target account name; the
        actual credential capture runs through the unified ``/login/*`` flow with
        ``profile=<name>``. (claude-code's literal route does the OAuth itself.)"""
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.start_add((body or {}).get("name", "")))
        from openprogram.auth.login_methods import login_methods, default_method
        name = (body or {}).get("name", "").strip()
        methods = [{"id": mid, "label": label} for mid, label in login_methods(provider)]
        return JSONResponse(content={
            "mode": "login",
            "account": name,
            "login_methods": methods,
            "default_method": default_method(provider),
        })

    # ---- pool controls (rotation / cooldown / keys) -----------------------
    # An account is one pool; these steer how its multiple keys rotate and let
    # the user add / drop / un-cool individual keys. claude-code (Meridian, no
    # AuthStore pool) doesn't apply — guarded below.

    @app.get("/api/providers/{provider}/accounts/{name}/keys")
    def api_account_keys(provider: str, name: str):
        """The keys inside one account, masked, each with name + health + whether
        it's the active one. Plus the pool mode: ``rotation`` on/off
        (``strategy != "fixed"``), the rotating ``strategy``, and ``active`` (the
        pinned key used when rotation is off)."""
        if provider == "claude-code":
            return JSONResponse(content={"keys": [], "rotation": False, "active": "",
                                         "strategy": "fixed", "strategies": []})
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        # Normalize a legacy pool (made before names/active/rotation existed:
        # empty active_credential_id) to the new model's default — rotation OFF,
        # pinned to the first key. One-time + idempotent (active is then set).
        if pool and pool.credentials and not getattr(pool, "active_credential_id", ""):
            pool.active_credential_id = pool.credentials[0].credential_id
            pool.strategy = "fixed"
            store.put_pool(pool)
        strategy = getattr(pool, "strategy", "fixed") if pool else "fixed"
        active = getattr(pool, "active_credential_id", "") if pool else ""
        keys = [_key_view(c, active) for c in (pool.credentials if pool else [])]
        return JSONResponse(content={
            "keys": keys,
            "rotation": strategy != "fixed",
            "active": active,
            "strategy": strategy if strategy != "fixed" else "fill_first",
            "strategies": list(_STRATEGIES),
        })

    @app.post("/api/providers/{provider}/accounts/{name}/rotation")
    def api_account_rotation(provider: str, name: str, body: dict = None):
        """Toggle automatic rotation. Off ⇒ strategy ``fixed`` (only the active
        key is used). On ⇒ a rotating strategy (the one passed, or ``fill_first``;
        a 429 then cools a key down and the next takes over)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        b = body or {}
        enabled = bool(b.get("enabled"))
        want = b.get("strategy") if b.get("strategy") in _STRATEGIES else "fill_first"
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": f"account '{name}' not found"})
        pool.strategy = want if enabled else "fixed"
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "rotation": enabled, "strategy": pool.strategy})

    @app.post("/api/providers/{provider}/accounts/{name}/keys/{credential_id}/use")
    def api_account_use_key(provider: str, name: str, credential_id: str):
        """Pin which key is the active/default one (used as-is when rotation is
        off; tried first when it's on)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None or not any(c.credential_id == credential_id for c in pool.credentials):
            return JSONResponse(content={"ok": False, "error": "key not found"})
        pool.active_credential_id = credential_id
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "active": credential_id})

    @app.post("/api/providers/{provider}/accounts/{name}/keys/{credential_id}/name")
    def api_account_name_key(provider: str, name: str, credential_id: str, body: dict = None):
        """Give a key a human name (stored in its metadata; shown in the list)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        label = ((body or {}).get("name") or "").strip()
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        cred = next((c for c in (pool.credentials if pool else []) if c.credential_id == credential_id), None)
        if cred is None:
            return JSONResponse(content={"ok": False, "error": "key not found"})
        meta = dict(getattr(cred, "metadata", None) or {})
        if label:
            meta["label"] = label
        else:
            meta.pop("label", None)
        cred.metadata = meta
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "name": label})

    @app.post("/api/providers/{provider}/accounts/{name}/strategy")
    def api_account_strategy(provider: str, name: str, body: dict = None):
        """Set the account's rotation strategy (fill_first / round_robin /
        random / least_used)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        strategy = (body or {}).get("strategy", "")
        if strategy not in _STRATEGIES:
            return JSONResponse(content={"ok": False, "error": f"unknown strategy '{strategy}'"})
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": f"account '{name}' not found"})
        pool.strategy = strategy
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "strategy": strategy})

    @app.post("/api/providers/{provider}/accounts/{name}/retry")
    def api_account_retry(provider: str, name: str):
        """"Retry now" — clear the cooldown on every key in the account so a
        rate-limited / cooled-down key is eligible again immediately."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        from openprogram.auth.store import get_store
        from openprogram.auth import pool as _pool
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": f"account '{name}' not found"})
        cleared = 0
        for c in pool.credentials:
            if _cooling(c) or getattr(c, "status", "") in ("rate_limited", "billing_blocked"):
                cleared += 1
            _pool.clear_cooldown(c)
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "cleared": cleared})

    @app.post("/api/providers/{provider}/accounts/{name}/keys")
    def api_account_add_key(provider: str, name: str, body: dict = None):
        """Add an API key to the account's pool. With ``validate: true`` the key
        is auth-probed first and rejected if the provider says it's invalid
        (other probe outcomes — valid / no-balance / unknown — still add). Returns
        the masked view of the new key + the validation result."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        b = body or {}
        key = b.get("api_key", "").strip()
        if not key:
            return JSONResponse(content={"ok": False, "error": "api_key is required"})
        if any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in key):
            return JSONResponse(content={"ok": False, "error": "the value has invalid characters — re-type the key"})
        validation = None
        if b.get("validate"):
            try:
                from openprogram.webui._model_catalog.credentials import (
                    validate_credential, INVALID_CREDENTIAL,
                )
                res = validate_credential(provider, api_key=key, use_cache=False)
                validation = res.to_dict()
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(content={
                        "ok": False,
                        "error": f"{provider} rejected that key (invalid credential).",
                        "validation": validation,
                    })
            except Exception:
                pass  # probe is best-effort; never block an add on a flaky network
        label = (b.get("name") or "").strip()
        from openprogram.auth.store import get_store
        from openprogram.auth.types import Credential, ApiKeyPayload
        store = get_store()
        was_empty = store.find_pool(provider, name) is None or not store.find_pool(provider, name).credentials
        cred = Credential(
            provider_id=provider, profile_id=name, kind="api_key",
            payload=ApiKeyPayload(api_key=key), source="webui_pool_add",
            metadata={"label": label} if label else {},
        )
        pool = store.add_credential(cred)
        if was_empty:
            # First key: pin it active and start with rotation OFF — a single
            # fixed key is the simple default; rotation is opt-in.
            pool.active_credential_id = cred.credential_id
            pool.strategy = "fixed"
            store.put_pool(pool)
        active = getattr(pool, "active_credential_id", "")
        return JSONResponse(content={"ok": True, "key": _key_view(cred, active), "validation": validation})

    @app.post("/api/providers/{provider}/accounts/{name}/keys/reorder")
    def api_account_reorder_keys(provider: str, name: str, body: dict = None):
        """Reorder the keys in an account. Order is priority for the default
        ``fill_first`` strategy: the first key is the default (used until it cools
        down, then the next). Unmentioned keys keep their relative order at the
        end. Body: ``{order: [credential_id, …]}``."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        order = (body or {}).get("order") or []
        from openprogram.auth.store import get_store
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": f"account '{name}' not found"})
        by_id = {c.credential_id: c for c in pool.credentials}
        ranked = [by_id[i] for i in order if i in by_id]
        ranked += [c for c in pool.credentials if c.credential_id not in set(order)]
        pool.credentials = ranked
        pool._rr_cursor = 0  # reset rotation cursor after a manual reorder
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "order": [c.credential_id for c in pool.credentials]})

    @app.delete("/api/providers/{provider}/accounts/{name}/keys/{credential_id}")
    def api_account_remove_key(provider: str, name: str, credential_id: str):
        """Drop one key from the account's pool (leaves the pool/account if other
        keys remain; use the account ``remove`` to delete the whole account)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code has no key pool"})
        from openprogram.auth.store import get_store
        store = get_store()
        store.remove_credential(provider, name, credential_id)
        # If we removed the pinned key, re-pin the first remaining one so the
        # account still has a definite active key.
        pool = store.find_pool(provider, name)
        if pool and pool.active_credential_id == credential_id:
            pool.active_credential_id = pool.credentials[0].credential_id if pool.credentials else ""
            store.put_pool(pool)
        return JSONResponse(content={"ok": True, "removed": credential_id})
