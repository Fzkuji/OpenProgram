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


def _masked(raw: str) -> str:
    return (raw[:6] + "…" + raw[-4:]) if len(raw) > 12 else (("•" * len(raw)) if raw else "")


def _primary_cred(pool):
    return pool.credentials[0] if (pool and pool.credentials) else None


def _api_key_of(cred) -> str:
    return getattr(getattr(cred, "payload", None), "api_key", "") or ""


def _validate_account(provider: str, name: str) -> dict:
    """LIVE, kind-aware validation of ONE account (profile) — what "does it
    actually work" means per credential type, with NO model call:
      * api_key      → auth-probe the key against the provider's endpoint.
      * oauth/device → refresh + check THIS profile's token (the OAuth login
                       is what works; a model being unavailable is NOT an auth
                       failure, so we never do a model ping here).
    Returns {status, detail?, via?} (status: valid / invalid_credential /
    needs_reauth / unknown / missing)."""
    from openprogram.auth.store import get_store
    cred = _primary_cred(get_store().find_pool(provider, name))
    if cred is None:
        return {"status": "missing", "detail": "no credential on this account"}
    kind = getattr(cred, "kind", "")
    if kind == "api_key":
        from openprogram.webui._model_catalog.credentials import validate_credential
        try:
            return validate_credential(provider, api_key=_api_key_of(cred), use_cache=False).to_dict()
        except Exception as e:
            return {"status": "unknown", "detail": str(e)}
    if kind in ("oauth", "device_code"):
        from openprogram.auth.manager import get_manager
        from openprogram.auth.resolver import _extract_token
        try:
            tok = _extract_token(get_manager().acquire_sync(provider, name))
            if tok:
                return {"status": "valid", "via": "AuthManager", "detail": "Signed in (token valid)."}
            return {"status": "needs_reauth", "detail": "no usable token — sign in again."}
        except Exception as e:
            return {"status": "needs_reauth", "detail": str(e)}
    return {"status": getattr(cred, "status", "unknown")}


def _account_record(pool, active_profile: str) -> dict:
    """One ACCOUNT = one profile (holding one credential). Reports a uniform
    shape for every provider: the account's id/name (the profile), its identity
    (a masked key for api-key accounts, an email for OAuth ones), health, whether
    it's the active one, and whether its key can be revealed. Never includes the
    secret itself."""
    cred = _primary_cred(pool)
    meta = (getattr(cred, "metadata", None) or {}) if cred else {}
    email = meta.get("email") or meta.get("account") or ""
    kind = getattr(cred, "kind", "") if cred else ""
    identity = _masked(_api_key_of(cred)) if kind == "api_key" else email
    return {
        "id": pool.profile_id,
        "name": pool.profile_id,
        "identity": identity,
        "email": email,
        "kind": kind,
        "status": getattr(cred, "status", "") if cred else "empty",
        "is_active": pool.profile_id == active_profile,
        "can_reveal": kind == "api_key",
        "cooling": _cooling(cred) if cred else False,
    }


def _api_key_env(provider: str) -> str:
    """The provider's API-key env var, or '' — used to decide add_mode
    (api_key paste vs sign-in) + which accounts can paste/reveal a key."""
    try:
        from openprogram.providers.env_api_keys import _PROVIDER_ENV_VARS
        names = _PROVIDER_ENV_VARS.get(provider) or []
        return names[0] if names else ""
    except Exception:
        return ""


def _generic_summary(provider: str) -> dict:
    """Unified account state: accounts = profiles (one credential each), the
    effective active account, the rotation toggle + strategy, and how "add"
    works (paste a key vs sign in)."""
    from openprogram.auth.store import get_store
    from openprogram.auth.active import get_active_profile
    from openprogram.auth.rotation import get_rotation, STRATEGIES
    from openprogram.auth.login_methods import login_methods, default_method

    store = get_store()
    active = get_active_profile(provider)  # effective: pin, else "default"
    pools = [p for p in store.list_pools() if p.provider_id == provider]
    pools.sort(key=lambda p: (p.profile_id != "default", p.profile_id))
    accounts = [_account_record(p, active) for p in pools]
    rot = get_rotation(provider)
    has_key = bool(_api_key_env(provider))
    methods = [{"id": mid, "label": label} for mid, label in login_methods(provider)]
    return {
        "installed": True,
        "ready": True,
        "active": active,
        "accounts": accounts,
        "rotation": rot["enabled"],
        "strategy": rot["strategy"],
        "strategies": list(STRATEGIES),
        # api-key providers paste a key into a new account; the rest sign in.
        "add_mode": "api_key" if has_key else "login",
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
        """Make an account active (the one requests run on). Empty ⇒ default."""
        b = body or {}
        name = b.get("id", b.get("name", ""))
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.activate_account(name))
        from openprogram.auth.active import set_active_profile, get_active_profile
        set_active_profile(provider, name)
        return JSONResponse(content={"active": get_active_profile(provider)})

    @app.post("/api/providers/{provider}/accounts/remove")
    def api_accounts_remove(provider: str, body: dict = None):
        b = body or {}
        name = b.get("id", b.get("name", ""))
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.remove_account(name))
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        name = (name or "").strip()
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
        b = body or {}
        if provider == "claude-code":
            from openprogram.providers.anthropic import _meridian_cli as _acc
            return JSONResponse(content=_acc.rename_account(b.get("id", b.get("old", "")), b.get("name", b.get("new", ""))))
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        from openprogram.auth.types import CredentialPool

        old = (b.get("id", b.get("old", "")) or "").strip()
        new = (b.get("name", b.get("new", "")) or "").strip()
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

    # ---- per-account key ops (api-key accounts) ---------------------------
    # An account is a profile holding one credential. For api-key accounts the
    # key can be added / revealed / updated / validated; rotation is a per-
    # provider toggle across accounts. claude-code (Meridian) is guarded.

    @app.post("/api/providers/{provider}/accounts/keys")
    def api_accounts_add_key(provider: str, body: dict = None):
        """Add a new api-key ACCOUNT: create the profile <name> with the key.
        validate:true auth-probes first and rejects an invalid key. Blank name
        auto-picks 'default' (or key-N)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code signs in; it has no API key"})
        b = body or {}
        key = (b.get("api_key") or "").strip()
        if not key:
            return JSONResponse(content={"ok": False, "error": "api_key is required"})
        if any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in key):
            return JSONResponse(content={"ok": False, "error": "the value has invalid characters \u2014 re-type the key"})
        validation = None
        if b.get("validate"):
            try:
                from openprogram.webui._model_catalog.credentials import validate_credential, INVALID_CREDENTIAL
                res = validate_credential(provider, api_key=key, use_cache=False)
                validation = res.to_dict()
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(content={"ok": False, "error": f"{provider} rejected that key (invalid credential).", "validation": validation})
            except Exception:
                pass
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        from openprogram.auth.types import Credential, ApiKeyPayload
        store = get_store()
        existing = {p.profile_id for p in store.list_pools() if p.provider_id == provider}
        name = (b.get("name") or "").strip()
        if not name:
            name = "default" if "default" not in existing else f"key-{len(existing) + 1}"
        cur = store.find_pool(provider, name)
        if cur is not None and cur.credentials:
            return JSONResponse(content={"ok": False, "error": f"account '{name}' already exists \u2014 pick another name"})
        store.add_credential(Credential(
            provider_id=provider, profile_id=name, kind="api_key",
            payload=ApiKeyPayload(api_key=key), source="webui_add",
        ))
        if not existing and not get_active_pin(provider):
            set_active_profile(provider, name)   # first account becomes active
        return JSONResponse(content={"ok": True, "name": name, "validation": validation})

    @app.get("/api/providers/{provider}/accounts/{name}/reveal")
    def api_account_reveal(provider: str, name: str):
        """The full API key of one api-key account (to copy / check)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "no API key"})
        from openprogram.auth.store import get_store
        cred = _primary_cred(get_store().find_pool(provider, name))
        if cred is None or cred.kind != "api_key":
            return JSONResponse(content={"ok": False, "error": "no API key on this account"})
        return JSONResponse(content={"ok": True, "value": _api_key_of(cred)})

    @app.post("/api/providers/{provider}/accounts/{name}/update")
    def api_account_update(provider: str, name: str, body: dict = None):
        """Replace an api-key account's key with a new one (validated first)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "no API key"})
        b = body or {}
        key = (b.get("api_key") or "").strip()
        if not key or any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in key):
            return JSONResponse(content={"ok": False, "error": "a valid API key is required"})
        validation = None
        if b.get("validate", True):
            try:
                from openprogram.webui._model_catalog.credentials import validate_credential, INVALID_CREDENTIAL
                res = validate_credential(provider, api_key=key, use_cache=False)
                validation = res.to_dict()
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(content={"ok": False, "error": f"{provider} rejected that key.", "validation": validation})
            except Exception:
                pass
        from openprogram.auth.store import get_store
        from openprogram.auth.types import ApiKeyPayload
        store = get_store()
        pool = store.find_pool(provider, name)
        cred = _primary_cred(pool)
        if cred is None or cred.kind != "api_key":
            return JSONResponse(content={"ok": False, "error": "no API key on this account"})
        cred.payload = ApiKeyPayload(api_key=key)
        cred.status = "valid"
        cred.cooldown_until_ms = 0
        cred.last_error = None
        store.put_pool(pool)
        return JSONResponse(content={"ok": True, "validation": validation})

    @app.post("/api/providers/{provider}/accounts/{name}/validate")
    def api_account_validate(provider: str, name: str):
        """LIVE-validate ONE account (kind-aware, no model call)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": True, "status": "valid", "detail": "managed by the local backend"})
        return JSONResponse(content={"ok": True, **_validate_account(provider, name)})

    @app.post("/api/providers/{provider}/accounts/validate-all")
    def api_accounts_validate_all(provider: str):
        """Live-validate every account; returns [{id, status, ...}]."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": True, "results": []})
        from openprogram.auth.store import get_store
        out = [{"id": p.profile_id, **_validate_account(provider, p.profile_id)}
               for p in get_store().list_pools() if p.provider_id == provider]
        return JSONResponse(content={"ok": True, "results": out})

    @app.post("/api/providers/{provider}/accounts/rotation")
    def api_accounts_rotation(provider: str, body: dict = None):
        """Toggle automatic rotation across this provider's accounts. Off \u21d2 use
        the active account only; On \u21d2 a 429 cools an account down and the next
        takes over (fill_first / round_robin / random / least_used)."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "claude-code doesn't rotate accounts"})
        from openprogram.auth.rotation import set_rotation
        b = body or {}
        res = set_rotation(provider, enabled=bool(b.get("enabled")), strategy=(b.get("strategy") or ""))
        return JSONResponse(content={"ok": True, **res})

    @app.post("/api/providers/{provider}/accounts/{name}/retry")
    def api_account_retry(provider: str, name: str):
        """Clear an account's cooldown so a rate-limited account is usable now."""
        if provider == "claude-code":
            return JSONResponse(content={"ok": False, "error": "n/a"})
        from openprogram.auth.store import get_store
        from openprogram.auth import pool as _pool
        store = get_store()
        pool = store.find_pool(provider, name)
        if pool is None:
            return JSONResponse(content={"ok": False, "error": "account not found"})
        for c in pool.credentials:
            _pool.clear_cooldown(c)
        store.put_pool(pool)
        return JSONResponse(content={"ok": True})
