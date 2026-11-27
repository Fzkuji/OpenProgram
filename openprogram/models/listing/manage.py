"""Changing a provider's credential list, for the Providers page and the CLI.

The Providers page and ``openprogram providers keys`` both use it.

Each function changes the provider's list in the store and returns what the
web receives (:func:`credential_row`, :func:`provider_credentials`); none
returns a secret. A request the list cannot honour raises
:class:`CredentialEditError` with an HTTP-style ``status`` and, when there is
one, the key check that refused it.

Every change is announced through the store (``AuthEventType.CREDENTIALS_CHANGED``),
which open tabs receive as ``provider.credentials.changed``.
"""
from __future__ import annotations


import logging
import time
from typing import Any, Optional

from openprogram.models.auth.types import AuthConfigError, Credential

_log = logging.getLogger(__name__)


class CredentialEditError(AuthConfigError):
    """A change the provider's list refused; ``status`` is the HTTP status the
    web route answers with, ``extra`` the fields it adds (a ``check``, an ``env_var``)."""

    def __init__(self, message: str, *, status: int = 400, provider_id: str = "", **extra: Any) -> None:
        super().__init__(message, provider_id=provider_id)
        self.status = status
        self.extra = extra


def _seconds(ms: int) -> Optional[float]:
    return ms / 1000 if ms else None


def _import_file_missing(cred: Credential) -> bool:
    """An imported sign-in whose file (another tool's) is not there."""
    from pathlib import Path
    path = (cred.payload.data or {}).get("store_path") if cred.payload else None
    if not path:
        return False
    try:
        return not Path(str(path)).expanduser().exists()
    except OSError:
        return False


def credential_row(cred: Credential, *, now_ms: Optional[int] = None,
                   set_up: Optional[bool] = None) -> dict:
    """One credential as the web receives it (never its secret).

    ``status`` is ``not_set_up`` for a key of a provider that has no address
    here (``key_check.provider_set_up``): it is neither ready nor refused.
    ``no_file`` is an imported sign-in whose file is not there.
    ``signin_name`` is what a sign-in of the provider is called ("ChatGPT"),
    for the name the web gives a row that has none."""
    from openprogram.models.auth.login.login_method_registry import signin_name
    from openprogram.models.auth.selection import state_of
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    blocked = {m: until / 1000 for m, until in (cred.blocked_models or {}).items() if until > now}
    keyed = cred.kind in ("api_key", "credential_process")
    if keyed and set_up is None:
        from openprogram.models.listing.key_check import provider_set_up
        set_up = provider_set_up(cred.provider_id)
    if keyed and not set_up:
        state = "not_set_up"  # only a key is sent to an address; a sign-in brings its own
    elif cred.kind == "import" and _import_file_missing(cred):
        state = "no_file"
    else:
        state = state_of(cred, now)
    return {
        "id": cred.id,
        "label": cred.label,
        "display_label": cred.display_label(),
        "kind": cred.kind,
        "source": cred.source,
        "identity": {k: v for k, v in (cred.identity or {}).items() if k in ("email", "plan", "org", "name")},
        "key_tail": cred.key_tail,
        "signin_name": signin_name(cred.provider_id),
        "env_var": cred.metadata.get("env_var") if cred.source == "env" else None,
        "read_only": cred.read_only,
        "enabled": cred.enabled,
        "status": state,
        "cooldown_until": _seconds(cred.cooldown_until_ms) if state in ("cooling", "quota") else None,
        "blocked_models": blocked,
        "last_error": cred.last_error,
        "last_used_at": _seconds(cred.last_used_at_ms),
        "use_count": int(cred.use_count or 0),
        "created_at": _seconds(cred.created_at_ms),
    }


def provider_credentials(provider: str) -> dict:
    """A provider's list as the web receives it::

        {"provider", "strategy", "strategies", "active",
         "auth": {"methods": [{id, label, label_zh, description, description_zh, path}],
                  "env_vars", "check", "keys_url", "billing_url"},
         "credentials": [credential_row + {"today": {calls, cost_usd},
                                           "limits": [QuotaLimitWire, ...]}, ...]}

    ``today`` is the credential's use since local midnight (the usage
    ledger), ``limits`` the quota limits it last reported. ``active`` is the credential a new conversation would use now (``null``
    when none can be used)."""
    from openprogram.models.auth.login.login_method_registry import describe
    from openprogram.models.auth.selection import choose
    from openprogram.models.auth.store import canonical_provider, get_store
    from openprogram.models.auth.types import STRATEGIES
    provider = canonical_provider(provider)
    pool = get_store().get(provider)
    try:
        active = choose(provider, record=False).id if pool.credentials else None
    except Exception:
        active = None
    today = _usage_today(provider)
    limits = _limits_by_credential(provider, [c.id for c in pool.credentials], active)
    from openprogram.models.listing.key_check import provider_set_up
    set_up = provider_set_up(provider)
    return {
        "provider": provider,
        "strategy": pool.strategy,
        "strategies": list(STRATEGIES),
        "active": active,
        "set_up": set_up,
        "auth": describe(provider),
        "credentials": [{**credential_row(c, set_up=set_up),
                         "today": today.get(c.id, {"calls": 0, "cost_usd": 0.0}),
                         "limits": limits.get(c.id, [])}
                        for c in pool.credentials],
    }


def _usage_today(provider: str) -> dict[str, dict]:
    """Each credential's calls and cost since local midnight, from the usage
    ledger (``{credential_id: {"calls", "cost_usd"}}``)."""
    try:
        from openprogram.models.usage.ledger import default_ledger
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        rows = default_ledger.query(since=midnight, group_by=["credential_id"],
                                    filters={"provider": provider})
    except Exception:
        _log.debug("%s: no usage for today", provider, exc_info=True)
        return {}
    return {str(r.keys.get("credential_id") or ""): {"calls": r.events, "cost_usd": round(r.cost_total, 6)}
            for r in rows if r.keys.get("credential_id")}


def _limits_by_credential(provider: str, ids: Optional[list] = None,
                          active: Optional[str] = None) -> dict[str, list]:
    """The quota limits each credential last reported (windows, a balance),
    as the status route carries them. A report no credential is named on
    (it came before requests named their credential) is the provider's only
    credential's, else the one in use now."""
    try:
        from openprogram.models.usage.quota import default_store
        now = time.time()
        out: dict[str, list] = {}
        unnamed = None
        for report in default_store.reports(provider):
            if report.account:
                out[report.account] = report.wire(now)["limits"]
            else:
                unnamed = report
        ids = list(ids or [])
        owner = ids[0] if len(ids) == 1 else active
        if unnamed is not None and owner and owner not in out:
            out[owner] = unnamed.wire(now)["limits"]
        return out
    except Exception:
        _log.debug("%s: no quota reports", provider, exc_info=True)
        return {}


def _find(provider: str, cid: str) -> Credential:
    from openprogram.models.auth.store import get_store
    cred = get_store().find(provider, cid)
    if cred is None:
        raise CredentialEditError("no such credential", status=404, provider_id=provider)
    return cred




def _probe(provider: str, key: str, base_url: Optional[str] = None):
    from openprogram.models.listing.credentials import validate_credential
    return validate_credential(provider, api_key=key, base_url=base_url, use_cache=False,
                               prove_usable=True)


# A key checked by the add dialog is stored a moment later by Add key; the
# check is kept for that long so the key is not probed twice. Keyed by the
# key's hash, so no secret is held here.
_CHECK_KEEP_S = 300.0
_checked: dict[tuple[str, str, str], tuple[float, Any]] = {}


def _check_key_id(provider: str, key: str, base_url: Optional[str]) -> tuple[str, str, str]:
    import hashlib
    return provider, hashlib.sha256(key.encode()).hexdigest(), base_url or ""


def _probe_or_none(provider: str, key: str, base_url: Optional[str] = None):
    """The key's check: the one the dialog just made when it is recent, else a new one."""
    ident = _check_key_id(provider, key, base_url)
    now = time.monotonic()
    for stale in [k for k, (at, _) in _checked.items() if now - at > _CHECK_KEEP_S]:
        _checked.pop(stale, None)
    kept = _checked.pop(ident, None)
    if kept is not None:
        return kept[1]
    try:
        return _probe(provider, key, base_url)
    except Exception:
        _log.info("%s: key check failed", provider, exc_info=True)
        return None


def check_key(provider: str, key: str, *, replace: Optional[str] = None) -> dict:
    """Check a pasted key with the provider without storing it; returns
    ``{"ok", "check"}``. With ``replace`` the key is checked against that
    stored key's endpoint. The result is kept for a few minutes so the Add
    key / Replace key that follows reuses it instead of probing again."""
    from openprogram.models.listing.key_check import check_of
    from openprogram.models.auth.provider_policy import require_api_key
    from openprogram.models.auth.store import canonical_provider
    base_url: Optional[str] = None
    if replace is not None:
        cred = _find(provider, replace)
        if cred.kind != "api_key" or cred.read_only:
            raise CredentialEditError("only a stored API key can be replaced; sign in again instead",
                                      provider_id=provider)
        provider, base_url = cred.provider_id, cred.payload.base_url or None
    else:
        provider = canonical_provider(provider)
    try:
        require_api_key(key)
    except Exception as exc:
        raise CredentialEditError(str(exc), provider_id=provider) from None
    probe = _probe_or_none(provider, key, base_url)
    if probe is not None:
        _checked[_check_key_id(provider, key, base_url)] = (time.monotonic(), probe)
    return {"ok": True, "check": check_of(provider, probe) if probe is not None else None}


def _refuse_rejected(provider: str, probe) -> None:
    from openprogram.models.listing.credentials import INVALID_CREDENTIAL
    from openprogram.models.listing.key_check import check_of
    if probe is not None and probe.status == INVALID_CREDENTIAL:
        raise CredentialEditError(f"{provider} rejected that key", provider_id=provider,
                                  check=check_of(provider, probe))


def apply_check(provider: str, cred_id: str, probe) -> None:
    """Record what a check says: a passing one clears every hold, a refused
    key or a spent balance marks it; anything else leaves the state."""
    from openprogram.models.listing.credentials import probe_proves_usable, status_from_probe
    from openprogram.models.auth.selection import QUOTA_HOLD_MS, clear, mark
    status = status_from_probe(probe.status, usable_proven=probe_proves_usable(probe))
    if status == "ready":
        clear(provider, cred_id)
    elif status == "quota":
        mark(provider, cred_id, "quota", until_ms=int(time.time() * 1000) + QUOTA_HOLD_MS,
             code="quota", message=probe.detail or "no balance")
    elif status == "invalid_key":
        from openprogram.models.auth.store import get_store
        cred = get_store().find(provider, cred_id)
        kind = "invalid_key" if cred is None or cred.kind in ("api_key", "credential_process") \
            else "needs_login"
        mark(provider, cred_id, kind, code=kind, message=probe.detail or "refused")


def _label(value: str) -> str:
    from openprogram.models.auth.account.account_labels import normalize_account_label
    try:
        return normalize_account_label(value, allow_empty=True)
    except ValueError as exc:
        raise CredentialEditError(str(exc)) from None


def add_api_key(provider: str, key: str, *, label: str = "", validate: bool = True,
                source: str = "web") -> dict:
    """Store a pasted key at the end of the provider's list; returns
    ``{"ok", "credential", "check"}``.

    With ``validate`` it is checked first (``models/listing/key_check.py``
    ``check``: works / rejected / no_credit / not_available / unreachable /
    unverified); a rejected key is not stored. The first usable key of a
    provider turns the provider and its suggested models on (the check's
    ``auto_enabled`` / ``enabled_models`` say so, for the caller to tell
    open tabs)."""
    from openprogram.models.listing.key_check import check_of, connect
    from openprogram.models.auth.login.credential_login import api_key_credential
    from openprogram.models.auth.store import canonical_provider, get_store
    provider = canonical_provider(provider)
    # A key an environment variable also holds is stored all the same, so it
    # outlives the variable; the list shows it once (``store.env_rows``).
    probe = _probe_or_none(provider, key) if validate else None
    _refuse_rejected(provider, probe)
    try:
        cred = api_key_credential(provider, key, source=source)
    except CredentialEditError:
        raise
    except AuthConfigError as exc:
        raise CredentialEditError(str(exc), provider_id=provider) from None
    cred.label = _label(label)
    store = get_store()
    first = not store.stored(provider).credentials
    kept = store.add_credential(cred)
    check = None
    if probe is not None:
        apply_check(provider, kept.id, probe)
        check = connect(provider, check_of(provider, probe), account=kept.id, first_credential=first)
    else:
        from openprogram.models.usage.quota_fetchers import after_key_added
        after_key_added(provider)
    row = store.find(provider, kept.id) or kept
    return {"ok": True, "credential": credential_row(row), "check": check}


def edit(provider: str, cid: str, *, label: Optional[str] = None,
         enabled: Optional[bool] = None) -> dict:
    """Rename a credential or turn it off and on; returns its row."""
    from openprogram.models.auth.store import get_store
    if label is not None:
        label = _label(label)

    def _change(row: Credential) -> None:
        if label is not None:
            row.label = label
        if enabled is not None:
            row.enabled = enabled

    store = get_store()
    cred = store.update(provider, cid, _change)
    if cred is None:
        raise CredentialEditError("no such credential", status=404, provider_id=provider)
    store.changed(provider, credential=cid)
    return credential_row(cred)


def remove(provider: str, cid: str) -> None:
    """Remove a stored credential; a company key leaves every way of the
    company at once. An environment variable's row is removed by unsetting
    the variable, so it is refused with that variable's name."""
    from openprogram.models.usage.quota import forget_accounts
    from openprogram.models.auth.store import get_store
    cred = _find(provider, cid)
    if cred.source == "env":
        name = cred.metadata.get("env_var") or ""
        raise CredentialEditError(
            f"This key is read from {name}. Unset {name} where OpenProgram starts "
            "(your shell profile or the app's environment) to remove it.",
            status=409, provider_id=provider, env_var=name)
    get_store().remove_credential(provider, cid)
    forget_accounts()


def check(provider: str, cid: str) -> dict:
    """Check a credential now and record the outcome on this way's row;
    returns ``{"credential", "check"}``. A company key refused here stays the
    company's: it is marked on this way only."""
    from openprogram.models.listing.credentials import check_credential
    from openprogram.models.listing.key_check import check_of
    from openprogram.models.auth.store import get_store
    cred = _find(provider, cid)
    probe = check_credential(cred.provider_id, cred)
    apply_check(cred.provider_id, cid, probe)
    row = get_store().find(provider, cid) or cred
    return {"credential": credential_row(row), "check": check_of(cred.provider_id, probe)}


def replace(provider: str, cid: str, key: str) -> dict:
    """Put a new key in a stored API key's place (its name, place and id
    stay); a rejected key is refused. Returns ``{"ok", "credential", "check"}``."""
    from openprogram.models.listing.key_check import check_of
    from openprogram.models.auth.provider_policy import require_api_key
    from openprogram.models.auth.selection import clear
    from openprogram.models.auth.store import get_store
    cred = _find(provider, cid)
    if cred.kind != "api_key" or cred.read_only:
        raise CredentialEditError("only a stored API key can be replaced; sign in again instead",
                                  provider_id=provider)
    try:
        require_api_key(key)
    except Exception as exc:
        raise CredentialEditError(str(exc), provider_id=provider) from None
    probe = _probe_or_none(cred.provider_id, key, cred.payload.base_url or None)
    _refuse_rejected(cred.provider_id, probe)

    def _swap(row: Credential) -> None:
        row.payload.auth_value = key

    store = get_store()
    store.update(provider, cid, _swap)
    # The replaced key must not stay on disk in the last good generation.
    store.drop_old_generations(provider)
    clear(cred.provider_id, cid)
    if probe is not None:
        apply_check(cred.provider_id, cid, probe)
    from openprogram.models.usage.quota_fetchers import after_key_added
    after_key_added(cred.provider_id)
    return {"ok": True, "credential": credential_row(store.find(provider, cid) or cred),
            "check": check_of(cred.provider_id, probe) if probe else None}


def reorder(provider: str, order: list) -> dict:
    """Put the list in ``order`` (every credential id once); returns the list.
    Applied to the list as it is on disk now, so a key another process
    added meanwhile makes the order stale and is refused, never dropped."""
    from openprogram.models.auth.store import get_store
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order) \
            or len(set(order)) != len(order):
        raise CredentialEditError("order must list every credential id exactly once",
                                  provider_id=provider)

    def _order(pool) -> None:
        if set(order) != {c.id for c in pool.credentials}:
            # Well formed, but not this list: it changed since the page read it.
            raise CredentialEditError("the list changed; read it again and reorder",
                                      status=409, provider_id=provider)
        by_id = {c.id: c for c in pool.credentials}
        pool.credentials = [by_id[i] for i in order]

    store = get_store()
    store.mutate(provider, _order)
    store.changed(provider)
    return provider_credentials(provider)


def set_strategy(provider: str, strategy: str) -> dict:
    """Set how the provider's credentials take turns; returns the list."""
    from openprogram.models.auth.store import get_store
    from openprogram.models.auth.types import STRATEGIES
    if strategy not in STRATEGIES:
        raise CredentialEditError(f"strategy must be one of {', '.join(STRATEGIES)}", provider_id=provider)

    def _set(pool) -> None:
        pool.strategy = strategy
        pool.cursor = 0

    store = get_store()
    store.mutate(provider, _set)
    store.set_cursor(provider, 0)
    store.changed(provider)
    return provider_credentials(provider)


__all__ = ["CredentialEditError", "add_api_key", "apply_check", "check", "check_key", "credential_row", "edit",
           "provider_credentials", "remove", "reorder", "replace", "set_strategy"]
