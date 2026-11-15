"""Per-provider account management backed by the credential store."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Body, Query
from fastapi.responses import JSONResponse

from openprogram.models.auth.credentials import is_redacted_value

from .credential_secrets import (
    check_request_body,
    is_nonempty_printable_ascii,
    mask_credential,
)

_log = logging.getLogger(__name__)

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


def reveal_deprecation_response() -> tuple[dict, int]:
    """The stable answer for any legacy credential-reveal client.

    Returning a value here is the behaviour being removed, so the reply
    is a fixed error with no credential field at all — a client scripted
    against the old route fails loudly instead of silently reading a
    secret that is no longer meant to leave the store.
    """
    return {"error": "credential reveal is no longer supported"}, 410


def _primary_cred(pool):
    return pool.credentials[0] if (pool and pool.credentials) else None


def _api_key_of(cred) -> str:
    return getattr(getattr(cred, "payload", None), "auth_value", "") or ""


def _apply_persist(provider: str, name: str, persist: str | None, *, detail: str | None = None) -> None:
    """Write a mapped persist status onto the live credential. ``None`` leaves
    the stored status unchanged (and never upgrades ``billing_blocked``)."""
    if not persist:
        return
    from openprogram.models.auth.pool import clear_cooldown
    from openprogram.models.auth.store import get_store
    pool = get_store().find_pool(_pool_id(provider), name)
    live = _primary_cred(pool)
    if live is None:
        return
    if persist == "valid":
        clear_cooldown(live)
        live.status = "valid"
        live.last_error = None
    else:
        live.status = persist
        if persist == "billing_blocked":
            live.cooldown_until_ms = 0
        if detail:
            live.last_error = detail
    live.updated_at_ms = _now_ms()
    get_store().put_pool(pool)


def _validate_account(provider: str, name: str, *, ping: bool = False) -> dict:
    """LIVE, kind-aware validation of ONE account.

    * api_key — layer-1 auth probe; ``ping=True`` (explicit Validate) also
      runs a cheap layer-2 completion when the kind has no billing endpoint.
    * oauth/device — refresh + check THIS account's token.

    ``valid`` is persisted and returned only when the probe proves the key
    is usable now (OpenRouter ``GET /key`` remaining, or a layer-2 / OAuth
    success). Auth-only ``GET /models`` 200 must not revive ``billing_blocked``.
    """
    from openprogram.models.auth.store import get_store
    from openprogram.models.listing.credentials import (
        display_status_from_probe,
        persist_status_from_probe,
        probe_proves_usable,
        validate_credential,
    )
    cred = _primary_cred(get_store().find_pool(_pool_id(provider), name))
    if cred is None:
        return {"status": "missing", "ok": False, "detail": "no credential on this account"}
    kind = getattr(cred, "kind", "")
    previous = getattr(cred, "status", "") or ""
    if kind == "api_key":
        try:
            result = validate_credential(
                provider, api_key=_api_key_of(cred),
                use_cache=False, prove_usable=ping,
            )
        except Exception as e:
            return {"status": "unknown", "ok": False, "detail": str(e)}
        usable = probe_proves_usable(result)
        persist = persist_status_from_probe(
            result.status, previous=previous, usable_proven=usable,
        )
        display = display_status_from_probe(
            result.status, previous=previous, usable_proven=usable,
        )
        _apply_persist(provider, name, persist, detail=result.detail)
        out = result.to_dict()
        out["status"] = display
        out["ok"] = display == "valid"
        # The same one-line result Add provider shows (Retry re-checks here).
        from openprogram.models.listing.key_check import balance_of, check_of
        check = check_of(provider, result)
        check["balance"] = balance_of(provider, cred.credential_id)
        out["check"] = check
        return out
    if kind in ("oauth", "device_code"):
        from openprogram.models.auth.credential_provider import get_credential_provider
        from openprogram.models.auth.resolver import _extract_token
        try:
            tok = _extract_token(get_credential_provider().acquire_sync(provider, name))
            if tok:
                _apply_persist(provider, name, "valid")
                return {"status": "valid", "ok": True, "via": "CredentialProvider", "detail": "Signed in (token valid)."}
            _apply_persist(provider, name, "needs_reauth", detail="no usable token — sign in again.")
            return {"status": "needs_reauth", "ok": False, "detail": "no usable token — sign in again."}
        except Exception as e:
            _apply_persist(provider, name, "needs_reauth", detail=str(e))
            return {"status": "needs_reauth", "ok": False, "detail": str(e)}
    return {"status": previous or "unknown", "ok": previous == "valid"}


def _account_record(pool, active: str, fallback: str, disabled: set = frozenset()) -> dict:
    """One ACCOUNT = one account (holding one credential). Uniform shape for every
    provider. API-key credentials expose only ``has_value`` and ``masked_key``;
    login credentials keep their non-secret identity metadata. ``is_active``
    is the account requests run on with rotation off: the pin, else the
    fallback account. ``is_fallback`` marks that fallback, which stays in use
    while nothing else is pinned, so it cannot be switched off on its own.
    ``enabled`` is the independent rotation selection. Never returns a
    credential value."""
    cred = _primary_cred(pool)
    kind = getattr(cred, "kind", "") if cred else ""
    account = pool.account_id
    from openprogram.models.auth.account.account_labels import account_identity
    from openprogram.models.auth.account.account_labels import effective_account_label

    label = effective_account_label(cred, account)
    if kind == "api_key":
        raw_key = _api_key_of(cred)
        email, identity = "", ""
    else:
        email, identity = account_identity(cred)
    # A rate_limited credential whose throttle window has passed is
    # usable again — report it as valid instead of a stale state. No
    # separate "cooling" flag: the status column says everything
    # (valid / rate_limited / billing_blocked / needs_reauth / revoked).
    status = getattr(cred, "status", "") if cred else "empty"
    if status == "rate_limited" and cred and not _cooling(cred):
        status = "valid"
    record = {
        "id": account,
        "label": label,
        # Compatibility projection for older Web/TUI clients. It is display
        # text, never the stable id used by account operations.
        "name": label,
        "email": email,
        "kind": kind,
        "status": status,
        "is_active": account == active,
        "is_fallback": account == fallback,
        "enabled": account not in disabled,
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
    try:
        from openprogram.models.providers.settings.env_api_keys import env_vars_for
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
        from openprogram.models.providers.settings.storage import _is_custom_provider
        from openprogram.models.providers.catalog.metadata import synthesized_env_var
        if _is_custom_provider(provider):
            return synthesized_env_var(provider)
    except Exception:
        # Unknown custom-provider status: treat as a sign-in provider.
        _log.debug("custom provider check failed for %s", provider, exc_info=True)
    return ""


def _pool_id(provider: str) -> str:
    """Resolve a supported credential pool id."""
    from fastapi import HTTPException
    from openprogram.models.auth.account.aliases import resolve
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    provider = resolve(provider)
    if provider in UNSUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported provider")
    return provider


def _generic_summary(provider: str) -> dict:
    """Unified account state: accounts = accounts (one credential each), the
    effective active account, the rotation toggle + strategy, and how "add"
    works (paste a key vs sign in)."""
    from openprogram.models.auth.store import get_store
    from openprogram.models.auth.account.account_selection import get_active_account
    from openprogram.models.auth.account.account_selection import get_active_pin
    from openprogram.models.auth.rotation import get_rotation, STRATEGIES
    from openprogram.models.auth.account.account_priority import account_priority_key
    from openprogram.models.auth.rotation import get_accounts_out_of_rotation
    from openprogram.models.auth.login.login_method_registry import login_methods
    from openprogram.models.auth.login.login_method_registry import default_method

    pool = _pool_id(provider)
    store = get_store()
    active = get_active_account(pool)  # effective: pin, else "default"
    pinned = get_active_pin(pool)      # explicit pin only
    from openprogram.models.auth.context import get_active_account_id
    fallback = get_active_account_id()  # what runs while nothing is pinned
    disabled = get_accounts_out_of_rotation(pool)      # accounts turned OFF for rotation
    pools = [p for p in store.list_pools() if p.provider_id == pool]
    _k = account_priority_key(pool)                # honour the user's drag order
    pools.sort(key=lambda p: _k(p.account_id))
    accounts = [_account_record(p, active, fallback, disabled) for p in pools]
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


def _settings_changed() -> None:
    """Tell open tabs to reload enabled models (a provider or models were turned on)."""
    try:
        from openprogram.runtime.events import emit_ws_frame
        emit_ws_frame({"type": "agent_settings_changed", "data": {}})
    except Exception:
        _log.debug("settings change not broadcast", exc_info=True)


def _add_api_key(provider: str, key: str, name_field: str, *, validate: bool, source: str) -> tuple[dict, int]:
    """Store a new api-key account and, with ``validate``, check it first.

    The check is answered as ``check`` (models/listing/key_check.py): one
    of works / rejected / no_credit / not_available / unreachable /
    unverified, with the balance, the model count and three suggested
    models. A rejected key is not stored (400). The first usable key of a
    provider turns the provider and its suggested models on. Never returns
    the key.
    """
    validation = None
    persist = None
    probe = None
    from openprogram.models.listing.credentials import (
        INVALID_CREDENTIAL,
        display_status_from_probe,
        persist_status_from_probe,
        probe_proves_usable,
        validate_credential,
    )
    from openprogram.models.listing.key_check import check_of, connect
    if validate:
        try:
            res = validate_credential(
                provider, api_key=key, use_cache=False, prove_usable=True,
            )
            probe = res
            usable = probe_proves_usable(res)
            persist = persist_status_from_probe(
                res.status, previous="", usable_proven=usable,
            )
            display = display_status_from_probe(
                res.status, previous="", usable_proven=usable,
            )
            validation = res.to_dict()
            validation["status"] = display
            validation["ok"] = display == "valid"
            if res.status == INVALID_CREDENTIAL:
                return {"error": f"{provider} rejected that key", "check": check_of(provider, res)}, 400
        except Exception:
            persist = None
            probe = None
    from openprogram.models.auth.store import get_store
    from openprogram.models.auth.account.account_selection import get_active_pin
    from openprogram.models.auth.account.account_selection import set_active_account
    from openprogram.models.auth.types import Credential, CredentialData
    store = get_store()
    pid = _pool_id(provider)
    pools = [p for p in store.list_pools() if p.provider_id == pid]
    existing = {p.account_id for p in pools}
    first_credential = not any(p.credentials for p in pools)
    name = name_field.strip()
    if not name:
        name = "default" if "default" not in existing else f"key-{len(existing) + 1}"
    cur = store.find_pool(pid, name)
    if cur is not None and cur.credentials:
        return {"error": f"account '{name}' already exists"}, 409
    # Never stamp ``valid`` just because a key was saved. Persist the
    # mapped probe result; an unproven key stays ``unknown``.
    initial_status = persist or "unknown"
    credential = Credential(
        provider_id=pid, account_id=name, kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key),
        source=source,
        status=initial_status,
    )
    store.add_credential(credential)
    if persist == "billing_blocked":
        pool = store.find_pool(pid, name)
        live = _primary_cred(pool)
        if live is not None:
            live.cooldown_until_ms = 0
            if validation and validation.get("detail"):
                live.last_error = validation["detail"]
            store.put_pool(pool)
    if not existing and not get_active_pin(pid):
        set_active_account(pid, name)   # first account becomes active
    check = None
    if probe is not None:
        # The balance is read now, once, through the provider's verified
        # quota reader; the provider and its suggested models are turned on.
        check = connect(provider, check_of(provider, probe), account=credential.credential_id,
                        first_credential=first_credential)
        if check["auto_enabled"] or check["enabled_models"]:
            _settings_changed()
    else:
        # A new key's quota (balance, key limit) is read right away, once.
        from openprogram.models.usage.quota_fetchers import after_key_added
        after_key_added(pid)
    return {"ok": True, "name": name, "validation": validation, "status": initial_status, "check": check}, 200


def register(app):
    @app.get("/api/providers/{provider}/accounts")
    def api_accounts(provider: str):
        return JSONResponse(content=_generic_summary(provider))

    @app.get("/api/providers/{provider}/accounts/{name}/reveal")
    def api_accounts_reveal(provider: str, name: str):
        """Legacy key-reveal route: answers, but never with a value."""
        payload, status = reveal_deprecation_response()
        return JSONResponse(content=payload, status_code=status)

    @app.post("/api/providers/{provider}/accounts/use")
    def api_accounts_use(provider: str, body: Any = Body(default=None)):
        """Make an account active (the one requests run on). ``{"id": ""}``
        clears the pin so the default account takes over."""
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
        from openprogram.models.auth.account.account_selection import set_active_account
        from openprogram.models.auth.account.account_selection import get_active_account
        from openprogram.models.auth.store import get_store
        pool = _pool_id(provider)
        if name and get_store().find_pool(pool, name) is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        set_active_account(pool, name)
        return JSONResponse(content={"active": get_active_account(pool)})

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
        from openprogram.models.auth.store import get_store
        from openprogram.models.auth.account.account_selection import get_active_pin
        from openprogram.models.auth.account.account_selection import set_active_account
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
            set_active_account(provider_id, "")
        return JSONResponse(content={
            "removed": True,
            "name": name,
            "cleared_active": cleared,
        })

    @app.post("/api/providers/{provider}/accounts/rename")
    def api_accounts_rename(provider: str, body: Any = Body(default=None)):
        """Change an account's display label without re-keying its stable id."""
        error = check_request_body(body, allowed={"id", "name"}, required={"id", "name"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        from openprogram.models.auth.store import get_store
        from openprogram.models.auth.account.account_labels import normalize_account_label

        pid = _pool_id(provider)
        account_id = body["id"]
        if not is_nonempty_printable_ascii(account_id):
            return JSONResponse(
                content={"error": "invalid account id"}, status_code=400
            )
        account_id = account_id.strip()
        try:
            label = normalize_account_label(body["name"])
        except ValueError as exc:
            return JSONResponse(
                content={"error": str(exc)}, status_code=400
            )
        store = get_store()
        pool = store.find_pool(pid, account_id)
        if pool is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        for c in pool.credentials:
            c.metadata = {**(c.metadata or {}), "label": label}
        store.put_pool(pool)
        return JSONResponse(content={
            "ok": True,
            "id": account_id,
            "label": label,
            "name": label,
        })

    @app.post("/api/providers/{provider}/accounts/add")
    def api_accounts_add(provider: str, body: Any = Body(default=None)):
        """Generic add hands the UI the login methods + target account name; the
        actual credential capture runs through the unified ``/login/*`` flow with
        ``account=<name>``. No credential is accepted here."""
        if body is None:
            body = {}
        error = check_request_body(body, allowed={"name"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        from openprogram.models.auth.login.login_method_registry import login_methods
        from openprogram.models.auth.login.login_method_registry import default_method
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
    # An account is a account holding one credential. For api-key accounts the
    # key can be added / replaced / validated; rotation is a per-
    # provider toggle across accounts.

    @app.post("/api/providers/{provider}/accounts/keys")
    def api_accounts_add_key(provider: str, body: Any = Body(default=None)):
        """Add a new api-key ACCOUNT: create the account <name> with the key.
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
        provider = _pool_id(provider)
        key = body["api_key"]
        name_field = body.get("name", "")
        validate = body.get("validate", False)
        if (
            not is_nonempty_printable_ascii(key)
            or "sk-ant-oat" in key
            or is_redacted_value(key)
            or not isinstance(validate, bool)
            or not isinstance(name_field, str)
            or (name_field != "" and not is_nonempty_printable_ascii(name_field))
        ):
            return JSONResponse(
                content={"error": "invalid account key body"}, status_code=400
            )
        key = key.strip()
        if not key:
            return JSONResponse(
                content={"error": "invalid account key body"}, status_code=400
            )
        payload, status = _add_api_key(provider, key, name_field, validate=validate, source="webui_add")
        return JSONResponse(content=payload, status_code=status)

    @app.post("/api/providers/{provider}/accounts/env")
    def api_accounts_use_env_key(provider: str, body: Any = Body(default=None)):
        """``Use`` on a key found in the Worker's environment: the named
        variable is read here, checked and stored like a pasted key, so the
        key itself never crosses the wire. Body ``{env_var, name?}``; the
        variable must be one the provider reads (models/listing/detected.py)."""
        error = check_request_body(body, allowed={"env_var", "name"}, required={"env_var"})
        if error is not None:
            return JSONResponse(content={"error": error}, status_code=400)
        env_var, name_field = body["env_var"], body.get("name", "")
        if not is_nonempty_printable_ascii(env_var) or not isinstance(name_field, str) or (
                name_field != "" and not is_nonempty_printable_ascii(name_field)):
            return JSONResponse(content={"error": "invalid env key body"}, status_code=400)
        provider = _pool_id(provider)
        from openprogram.models.listing.detected import env_value, key_env_names
        if env_var not in key_env_names(provider):
            return JSONResponse(content={"error": f"{provider} does not read {env_var}"}, status_code=400)
        key = env_value(env_var)
        if not key or "sk-ant-oat" in key or is_redacted_value(key):
            return JSONResponse(content={"error": f"{env_var} is not set"}, status_code=404)
        payload, status = _add_api_key(provider, key, name_field, validate=True, source=f"env:{env_var}")
        return JSONResponse(content=payload, status_code=status)

    @app.post("/api/providers/{provider}/accounts/{name}/update")
    def api_account_update(
        provider: str,
        name: str,
        body: Any = Body(default=None),
    ):
        """Replace an api-key account's key with a new one (validated first)."""
        if (
            not isinstance(body, dict)
            or "api_key" not in body
            or not set(body).issubset({"api_key", "validate"})
        ):
            return JSONResponse(
                content={"error": "invalid account update body"},
                status_code=400,
            )
        provider = _pool_id(provider)
        key = body["api_key"]
        validate = body.get("validate", True)
        if (
            not is_nonempty_printable_ascii(key)
            or "sk-ant-oat" in key
            or is_redacted_value(key)
            or not isinstance(validate, bool)
        ):
            return JSONResponse(
                content={"error": "invalid account update body"},
                status_code=400,
            )

        from openprogram.models.auth.store import get_store

        store = get_store()
        pool = store.find_pool(_pool_id(provider), name)
        cred = _primary_cred(pool)
        if cred is None or cred.kind != "api_key":
            return JSONResponse(
                content={"error": "API-key account not found"},
                status_code=404,
            )

        persist = None
        previous = getattr(cred, "status", "") or ""
        if validate:
            try:
                from openprogram.models.listing.credentials import (
                    INVALID_CREDENTIAL,
                    persist_status_from_probe,
                    probe_proves_usable,
                    validate_credential,
                )
                res = validate_credential(
                    provider, api_key=key, use_cache=False, prove_usable=True,
                )
                if res.status == INVALID_CREDENTIAL:
                    return JSONResponse(
                        content={"error": f"{provider} rejected that key"},
                        status_code=400,
                    )
                persist = persist_status_from_probe(
                    res.status, previous=previous,
                    usable_proven=probe_proves_usable(res),
                )
            except Exception:
                # A failed probe is an unknown result and cannot block save.
                # Leave status unchanged — do not stamp valid.
                persist = None
        cred.payload.auth_value = key
        if persist == "valid":
            from openprogram.models.auth.pool import clear_cooldown
            clear_cooldown(cred)
            cred.status = "valid"
            cred.last_error = None
        elif persist:
            cred.status = persist
            if persist == "billing_blocked":
                cred.cooldown_until_ms = 0
        # persist is None: leave previous status (never force valid).
        cred.updated_at_ms = _now_ms()
        store.put_pool(pool)
        return JSONResponse(content={"ok": True})

    @app.post("/api/providers/{provider}/accounts/{name}/validate")
    def api_account_validate(
        provider: str, name: str, ping: bool = Query(default=False),
    ):
        """LIVE-validate ONE account. ``ping=true`` is the explicit user
        Validate (cheap layer-2 when the kind has no billing endpoint).
        On-mount / remount omit it so a ``GET /models`` 200 cannot revive
        ``billing_blocked`` or paint green Valid."""
        result = _validate_account(provider, name, ping=ping)
        return JSONResponse(content=result)

    @app.post("/api/providers/{provider}/accounts/validate-all")
    def api_accounts_validate_all(provider: str):
        """Live-validate every account (layer-1 only — do not spend tokens
        on every row). Returns [{id, status, ...}]."""
        from openprogram.models.auth.store import get_store
        pid = _pool_id(provider)
        out = [{"id": p.account_id, **_validate_account(provider, p.account_id, ping=False)}
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
        from openprogram.models.auth.rotation import set_rotation
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
        from openprogram.models.auth.account.account_priority import set_account_priority
        set_account_priority(_pool_id(provider), order)
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
        from openprogram.models.auth.rotation import (
            set_account_in_rotation, get_accounts_out_of_rotation,
        )
        from openprogram.models.auth.store import get_store
        pid = _pool_id(provider)
        if get_store().find_pool(pid, name) is None:
            return JSONResponse(
                content={"error": "account id not found"}, status_code=404
            )
        set_account_in_rotation(pid, name, enabled)
        return JSONResponse(content={"ok": True, "disabled": sorted(get_accounts_out_of_rotation(pid))})

    @app.post("/api/providers/{provider}/accounts/{name}/retry")
    def api_account_retry(provider: str, name: str):
        """Clear an account's cooldown so a rate-limited account is usable now."""
        from openprogram.models.auth.store import get_store
        from openprogram.models.auth import pool as _pool
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
