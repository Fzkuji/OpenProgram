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
from typing import Any

from fastapi import Body
from fastapi.responses import JSONResponse

from ._credential_secrets import (
    check_request_body,
    is_nonempty_printable_ascii,
    mask_credential,
)

# The pool rotation strategies a user can pick (auth/types.py PoolStrategy).
_STRATEGIES = ("fill_first", "round_robin", "random", "least_used")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cooling(cred) -> bool:
    """True if this credential is in a cooldown window right now."""
    until = getattr(cred, "cooldown_until_ms", 0) or 0
    return bool(until and until > _now_ms())


def _masked(raw: str) -> str:
    return mask_credential(raw)


def _primary_cred(pool):
    return pool.credentials[0] if (pool and pool.credentials) else None


def _api_key_of(cred) -> str:
    return getattr(getattr(cred, "payload", None), "auth_value", "") or ""


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
        from openprogram.webui._model_listing.credentials import validate_credential
        try:
            result = validate_credential(provider, api_key=_api_key_of(cred), use_cache=False).to_dict()
        except Exception as e:
            return {"status": "unknown", "detail": str(e)}
        if result.get("status") == "valid" and getattr(cred, "status", "") != "valid":
            # The probe proves the key works again (e.g. the user topped
            # up after a billing stop) — restore it so rotation picks it
            # back up. This closes the top-up → Validate → usable loop.
            from openprogram.auth.pool import clear_cooldown
            from openprogram.auth.store import get_store as _gs
            pool = _gs().find_pool(provider, name)
            live = _primary_cred(pool)
            if live is not None:
                clear_cooldown(live)
                live.status = "valid"
                live.last_error = None
                _gs().put_pool(pool)
        return result
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


def _oauth_email(cred) -> str:
    """Best email for a login account: metadata first, else decode the OIDC
    id_token (codex / gemini carry one with an `email` claim) so EXISTING
    accounts show the email with no re-login."""
    meta = (getattr(cred, "metadata", None) or {}) if cred else {}
    email = meta.get("email") or meta.get("account") or ""
    if email:
        return email
    # Anthropic's token response nests the email under
    # extra.account.email_address (org name carries it too). Codex/Gemini
    # instead ship an id_token with an `email` claim — try both.
    payload_data = getattr(getattr(cred, "payload", None), "data", None) or {}
    extra = payload_data.get("extra") or {}
    acct = extra.get("account") if isinstance(extra, dict) else None
    if isinstance(acct, dict) and acct.get("email_address"):
        return acct["email_address"]
    idt = payload_data.get("id_token", "") or ""
    if idt:
        try:
            from openprogram.providers.openai_codex.auth_adapter import _decode_jwt_payload
            return (_decode_jwt_payload(idt) or {}).get("email", "") or ""
        except Exception:
            pass
    return ""


def _account_record(pool, pinned: str, disabled: set = frozenset()) -> dict:
    """One ACCOUNT = one profile (holding one credential). Uniform shape for every
    provider. API-key credentials expose only ``has_value`` and ``masked_key``;
    login credentials keep their non-secret identity metadata. ``is_active``
    is the explicit pin (single-active, rotation off), while ``enabled`` is the
    independent rotation selection. Never returns a credential value."""
    cred = _primary_cred(pool)
    kind = getattr(cred, "kind", "") if cred else ""
    profile = pool.profile_id
    if kind == "api_key":
        name = profile
        raw_key = _api_key_of(cred)
        email = ""
    else:
        email = _oauth_email(cred)
        # Show the email as the name for an un-renamed account; a custom rename
        # (profile != "default") wins. No separate identity column.
        name = email if (email and profile == "default") else profile
        identity = ""
    # A rate_limited credential whose throttle window has passed is
    # usable again — report it as valid instead of a stale state. No
    # separate "cooling" flag: the status column says everything
    # (valid / rate_limited / billing_blocked / needs_reauth / revoked).
    status = getattr(cred, "status", "") if cred else "empty"
    if status == "rate_limited" and cred and not _cooling(cred):
        status = "valid"
    record = {
        "id": profile,
        "name": name,
        "email": email,
        "kind": kind,
        "status": status,
        "is_active": profile == pinned,
        "enabled": profile not in disabled,
    }
    if kind == "api_key":
        record.update({
            "has_value": bool(raw_key),
            "masked_key": _masked(raw_key),
        })
    else:
        record["identity"] = identity
    return record


def _api_key_env(provider: str) -> str:
    """The provider's API-key env var, or '' — used to decide add_mode
    (API-key entry vs sign-in) and which accounts can replace a key.

    Uses ``env_vars_for`` (canonical static table → models.dev community
    fallback), NOT the static ``_PROVIDER_ENV_VARS`` alone. A community-tier
    api-key provider (e.g. ``minimax-cn-coding-plan``) has no static row, so
    reading the static dict returned '' and misclassified it as a sign-in
    provider — which hid the key-paste box in the web form. The fallback
    restores the key field for every models.dev api-key provider."""
    # claude-code runs on a Claude SUBSCRIPTION — it carries ANTHROPIC_API_KEY
    # only as an internal detail and never asks the user for a key. Report no
    # key env so the UI uses the sign-in (login) add-mode, not key-paste.
    if provider == "claude-code":
        return ""
    try:
        from openprogram.providers.env_api_keys import env_vars_for
        names = env_vars_for(provider)
        if names:
            return names[0]
    except Exception:
        return ""
    # Custom (user-added OpenAI-compatible) providers have no env-var row in
    # any catalogue, but they authenticate with a pasted key — without this
    # they'd be misclassified as sign-in providers and the web form would
    # hide the key-paste box entirely. Same synthesised display-only label
    # the provider listing uses; keys resolve from the AuthStore by pid.
    try:
        from openprogram.providers.storage import _is_custom_provider
        from openprogram.providers.metadata import synthesized_env_var
        if _is_custom_provider(provider):
            return synthesized_env_var(provider)
    except Exception:
        pass
    return ""


def _pool_id(provider: str) -> str:
    """The AuthStore pool a provider's credentials live in.

    `claude-code` is an alias that resolves from the `anthropic` pool (its
    direct runtime reads `anthropic`), so all account ops on claude-code
    read/write the anthropic pool. Every other provider resolves through the
    canonical alias table (``bailian`` → ``alibaba-token-plan-cn`` …) so the
    pool AND the per-provider sidecars (active / rotation / order / disabled)
    key off one canonical id — no split-brain if a legacy alias arrives.
    """
    if provider == "claude-code":
        return "anthropic"
    from openprogram.auth.aliases import resolve
    return resolve(provider)


def _generic_summary(provider: str) -> dict:
    """Unified account state: accounts = profiles (one credential each), the
    effective active account, the rotation toggle + strategy, and how "add"
    works (paste a key vs sign in)."""
    from openprogram.auth.store import get_store
    from openprogram.auth.active import get_active_profile, get_active_pin
    from openprogram.auth.rotation import get_rotation, STRATEGIES
    from openprogram.auth.order import sort_key
    from openprogram.auth.enabled import get_disabled
    from openprogram.auth.login_methods import login_methods, default_method

    pool = _pool_id(provider)
    store = get_store()
    active = get_active_profile(pool)  # effective: pin, else "default"
    pinned = get_active_pin(pool)      # explicit pin only ("" ⇒ none active)
    disabled = get_disabled(pool)      # accounts turned OFF for rotation
    pools = [p for p in store.list_pools() if p.provider_id == pool]
    _k = sort_key(pool)                # honour the user's drag order
    pools.sort(key=lambda p: _k(p.profile_id))
    accounts = [_account_record(p, pinned, disabled) for p in pools]
    rot = get_rotation(pool)
    has_key = bool(_api_key_env(provider))
    methods = [{"id": mid, "label": label} for mid, label in login_methods(provider)]
    return {
        "installed": True,
        "ready": True,
        "active": active,
        "pinned": pinned,
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
        return JSONResponse(content=_generic_summary(provider))

    @app.post("/api/providers/{provider}/accounts/use")
    def api_accounts_use(provider: str, body: Any = Body(default=None)):
        """Make an account active (the one requests run on). ``{"id": ""}``
        clears the pin so the default profile takes over."""
        error = check_request_body(body, allowed={"id"}, required={"id"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        name = body["id"]
        # "" is the documented clear-the-pin value; anything else must name a
        # real account, so a typo can't silently un-pin the active one.
        if name != "" and not is_nonempty_printable_ascii(name):
            return JSONResponse(
                content={"error": "invalid account id"}, status_code=400
            )
        from openprogram.auth.active import set_active_profile, get_active_profile
        from openprogram.auth.store import get_store
        pool = _pool_id(provider)
        if name and get_store().find_pool(pool, name) is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        set_active_profile(pool, name)
        return JSONResponse(content={"active": get_active_profile(pool)})

    @app.post("/api/providers/{provider}/accounts/remove")
    def api_accounts_remove(provider: str, body: Any = Body(default=None)):
        if body is None or body == {}:
            return JSONResponse(
                content={"error": "account id not found"},
                status_code=404,
            )
        if not isinstance(body, dict) or set(body) != {"id"}:
            return JSONResponse(
                content={"error": "body must contain only id"},
                status_code=400,
            )
        name = body["id"]
        if not is_nonempty_printable_ascii(name):
            return JSONResponse(
                content={"error": "invalid account id"},
                status_code=400,
            )
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        provider_id = _pool_id(provider)
        store = get_store()
        if store.find_pool(provider_id, name) is None:
            return JSONResponse(
                content={"error": "account id not found"},
                status_code=404,
            )
        cleared = get_active_pin(provider_id) == name
        store.delete_pool(provider_id, name)
        if cleared:
            set_active_profile(provider_id, "")
        return JSONResponse(content={
            "removed": True,
            "name": name,
            "cleared_active": cleared,
        })

    @app.post("/api/providers/{provider}/accounts/rename")
    def api_accounts_rename(provider: str, body: Any = Body(default=None)):
        """Rename account ``id`` to ``name``. Both are required."""
        error = check_request_body(body, allowed={"id", "name"}, required={"id", "name"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        from openprogram.auth.types import CredentialPool

        pid = _pool_id(provider)
        old, new = body["id"], body["name"]
        if not is_nonempty_printable_ascii(old) or not is_nonempty_printable_ascii(new):
            return JSONResponse(
                content={"error": "invalid account id"}, status_code=400
            )
        old, new = old.strip(), new.strip()
        if not old or not new:
            return JSONResponse(
                content={"error": "invalid account id"}, status_code=400
            )
        store = get_store()
        if new == old:
            if store.find_pool(pid, old) is None:
                return JSONResponse(
                    content={"error": "account id not found"}, status_code=404
                )
            return JSONResponse(content={"ok": True, "name": new})
        if store.find_pool(pid, new) is not None:
            return JSONResponse(
                content={"error": f"account '{new}' already exists"}, status_code=409
            )
        pool = store.find_pool(pid, old)
        if pool is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        # Re-key every credential onto the new profile, write the new pool, then
        # drop the old file (put-before-delete so a crash never loses the creds).
        for c in pool.credentials:
            c.profile_id = new
        moved = CredentialPool(
            provider_id=pid, profile_id=new, strategy=pool.strategy,
            credentials=pool.credentials, fallback_chain=pool.fallback_chain,
        )
        store.put_pool(moved)
        store.delete_pool(pid, old)
        if get_active_pin(pid) == old:
            set_active_profile(pid, new)
        return JSONResponse(content={"ok": True, "name": new})

    @app.post("/api/providers/{provider}/accounts/add")
    def api_accounts_add(provider: str, body: Any = Body(default=None)):
        """Generic add hands the UI the login methods + target account name; the
        actual credential capture runs through the unified ``/login/*`` flow with
        ``profile=<name>``. No credential is accepted here."""
        if body is None:
            body = {}
        error = check_request_body(body, allowed={"name"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        from openprogram.auth.login_methods import login_methods, default_method
        name = body.get("name", "")
        if name != "" and not is_nonempty_printable_ascii(name):
            return JSONResponse(
                content={"error": "invalid account name"}, status_code=400
            )
        name = name.strip()
        methods = [{"id": mid, "label": label} for mid, label in login_methods(provider)]
        return JSONResponse(content={
            "mode": "login",
            "account": name,
            "login_methods": methods,
            "default_method": default_method(provider),
        })

    # ---- per-account key ops (api-key accounts) ---------------------------
    # An account is a profile holding one credential. For api-key accounts the
    # key can be added / replaced / validated; rotation is a per-
    # provider toggle across accounts. claude-code (Meridian) is guarded.

    @app.post("/api/providers/{provider}/accounts/keys")
    def api_accounts_add_key(provider: str, body: Any = Body(default=None)):
        """Add a new api-key ACCOUNT: create the profile <name> with the key.
        validate:true auth-probes first and rejects an invalid key. Blank name
        auto-picks 'default' (or key-N)."""
        # One of the two endpoints that legitimately carries a credential, so
        # the field set is checked here rather than through the shared
        # credential-rejecting helper.
        if (
            not isinstance(body, dict)
            or "api_key" not in body
            or not set(body).issubset({"api_key", "name", "validate"})
        ):
            return JSONResponse(
                content={"error": "invalid account key body"}, status_code=400
            )
        key = body["api_key"]
        name_field = body.get("name", "")
        validate = body.get("validate", False)
        if (
            not is_nonempty_printable_ascii(key)
            or not isinstance(validate, bool)
            or not isinstance(name_field, str)
            or (name_field != "" and not is_nonempty_printable_ascii(name_field))
        ):
            return JSONResponse(
                content={"error": "invalid account key body"}, status_code=400
            )
        if provider == "claude-code":
            return JSONResponse(
                content={"error": "claude-code signs in; it has no API key"},
                status_code=400,
            )
        key = key.strip()
        if not key:
            return JSONResponse(
                content={"error": "invalid account key body"}, status_code=400
            )
        validation = None
        if validate:
            try:
                from openprogram.webui._model_listing.credentials import validate_credential, INVALID_CREDENTIAL
                res = validate_credential(provider, api_key=key, use_cache=False)
                validation = res.to_dict()
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(
                        content={"error": f"{provider} rejected that key"},
                        status_code=400,
                    )
            except Exception:
                pass
        from openprogram.auth.store import get_store
        from openprogram.auth.active import get_active_pin, set_active_profile
        from openprogram.auth.types import Credential, CredentialData
        store = get_store()
        pid = _pool_id(provider)
        existing = {p.profile_id for p in store.list_pools() if p.provider_id == pid}
        name = name_field.strip()
        if not name:
            name = "default" if "default" not in existing else f"key-{len(existing) + 1}"
        cur = store.find_pool(pid, name)
        if cur is not None and cur.credentials:
            return JSONResponse(
                content={"error": f"account '{name}' already exists"},
                status_code=409,
            )
        store.add_credential(Credential(
            provider_id=pid, profile_id=name, kind="api_key",
            payload=CredentialData(kind="api_key", auth_value=key), source="webui_add",
        ))
        if not existing and not get_active_pin(pid):
            set_active_profile(pid, name)   # first account becomes active
        return JSONResponse(content={"ok": True, "name": name, "validation": validation})

    @app.post("/api/providers/{provider}/accounts/{name}/update")
    def api_account_update(
        provider: str,
        name: str,
        body: Any = Body(default=None),
    ):
        """Replace an api-key account's key with a new one (validated first)."""
        if provider == "claude-code":
            return JSONResponse(
                content={"error": "API-key account not found"},
                status_code=404,
            )
        if (
            not isinstance(body, dict)
            or "api_key" not in body
            or not set(body).issubset({"api_key", "validate"})
        ):
            return JSONResponse(
                content={"error": "invalid account update body"},
                status_code=400,
            )
        key = body["api_key"]
        validate = body.get("validate", True)
        if not is_nonempty_printable_ascii(key) or not isinstance(validate, bool):
            return JSONResponse(
                content={"error": "invalid account update body"},
                status_code=400,
            )

        from openprogram.auth.store import get_store

        store = get_store()
        pool = store.find_pool(_pool_id(provider), name)
        cred = _primary_cred(pool)
        if cred is None or cred.kind != "api_key":
            return JSONResponse(
                content={"error": "API-key account not found"},
                status_code=404,
            )

        if validate:
            try:
                from openprogram.webui._model_listing.credentials import validate_credential, INVALID_CREDENTIAL
                res = validate_credential(provider, api_key=key, use_cache=False)
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(
                        content={"error": f"{provider} rejected that key"},
                        status_code=400,
                    )
            except Exception:
                # A failed probe is an unknown result and cannot block save.
                pass
        cred.payload.auth_value = key
        cred.status = "valid"
        cred.cooldown_until_ms = 0
        cred.last_error = None
        cred.updated_at_ms = _now_ms()
        store.put_pool(pool)
        return JSONResponse(content={"ok": True})

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
        pid = _pool_id(provider)
        out = [{"id": p.profile_id, **_validate_account(provider, p.profile_id)}
               for p in get_store().list_pools() if p.provider_id == pid]
        return JSONResponse(content={"ok": True, "results": out})

    @app.post("/api/providers/{provider}/accounts/rotation")
    def api_accounts_rotation(provider: str, body: Any = Body(default=None)):
        """Toggle automatic rotation across this provider's accounts. Off \u21d2 use
        the active account only; On \u21d2 a 429 cools an account down and the next
        takes over (fill_first / round_robin / random / least_used)."""
        error = check_request_body(
            body, allowed={"enabled", "strategy"}, required={"enabled"}
        )
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        enabled = body["enabled"]
        strategy = body.get("strategy", "")
        if not isinstance(enabled, bool) or not isinstance(strategy, str):
            return JSONResponse(
                content={"error": "invalid rotation body"}, status_code=400
            )
        if strategy and strategy not in _STRATEGIES:
            return JSONResponse(
                content={"error": f"unknown strategy: {strategy}"}, status_code=400
            )
        if provider == "claude-code":
            return JSONResponse(
                content={"error": "claude-code doesn't rotate accounts"},
                status_code=400,
            )
        from openprogram.auth.rotation import set_rotation
        res = set_rotation(_pool_id(provider), enabled=enabled, strategy=strategy)
        return JSONResponse(content={"ok": True, **res})

    @app.post("/api/providers/{provider}/accounts/reorder")
    def api_accounts_reorder(provider: str, body: Any = Body(default=None)):
        """Set the account order (drag) — the priority requests try them in when
        rotation is on. Body: ``{order: [account_id, …]}``."""
        error = check_request_body(body, allowed={"order"}, required={"order"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        order = body["order"]
        if not isinstance(order, list) or not all(
            is_nonempty_printable_ascii(x) for x in order
        ):
            return JSONResponse(
                content={"error": "order must be a list of account ids"},
                status_code=400,
            )
        if len(set(order)) != len(order):
            return JSONResponse(
                content={"error": "order must not repeat an account id"},
                status_code=400,
            )
        from openprogram.auth.order import set_order
        set_order(_pool_id(provider), order)
        return JSONResponse(content={"ok": True, "order": order})

    @app.post("/api/providers/{provider}/accounts/enabled")
    def api_accounts_set_enabled(provider: str, body: Any = Body(default=None)):
        """Turn ONE account on / off for rotation — independent per account, so
        several can be on at once (unlike the single-active pin). Body:
        ``{id: account_id, enabled: bool}``."""
        error = check_request_body(
            body, allowed={"id", "enabled"}, required={"id", "enabled"}
        )
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        name, enabled = body["id"], body["enabled"]
        if not is_nonempty_printable_ascii(name) or not isinstance(enabled, bool):
            return JSONResponse(
                content={"error": "invalid enabled body"}, status_code=400
            )
        from openprogram.auth.enabled import set_enabled, get_disabled
        from openprogram.auth.store import get_store
        pid = _pool_id(provider)
        if get_store().find_pool(pid, name) is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        set_enabled(pid, name, enabled)
        return JSONResponse(content={"ok": True, "disabled": sorted(get_disabled(pid))})

    @app.post("/api/providers/{provider}/accounts/{name}/retry")
    def api_account_retry(provider: str, name: str):
        """Clear an account's cooldown so a rate-limited account is usable now."""
        if provider == "claude-code":
            return JSONResponse(
                content={"error": "claude-code accounts have no cooldown"},
                status_code=400,
            )
        from openprogram.auth.store import get_store
        from openprogram.auth import pool as _pool
        store = get_store()
        # Resolve the canonical pool id — a legacy alias ("bailian") reached
        # this handler and found no pool, so the cooldown was never cleared.
        pool = store.find_pool(_pool_id(provider), name)
        if pool is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        for c in pool.credentials:
            _pool.clear_cooldown(c)
        store.put_pool(pool)
        return JSONResponse(content={"ok": True})
