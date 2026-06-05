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

from fastapi.responses import JSONResponse


def _account_record(pool) -> dict:
    """One account row from a credential pool: name + best-effort identity +
    health, mirroring claude-code's ``{name, email}`` (plus ``kind/status/count``
    the richer UIs can show). Never includes the secret itself."""
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
