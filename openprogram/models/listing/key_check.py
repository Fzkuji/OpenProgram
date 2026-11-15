"""A key's check as one line, and what connecting a provider turns on.

Adding a key (``POST /api/providers/{id}/accounts/keys`` with
``validate: true``), adopting an environment key, re-checking a stored one
and finishing a sign-in all end here:

  * :func:`check_of` turns the probe (``credentials.validate_credential``,
    layer 1 then a layer-2 ping only when the provider has no billing
    endpoint) into one of six results the Add-provider dialog prints as a
    single line: ``works``, ``rejected``, ``no_credit``, ``not_available``
    (region or plan), ``unreachable`` and ``unverified`` (accepted, not
    proven usable). A key that is not rejected is kept.
  * :func:`connect` runs once the key is stored: it reads the key's balance
    when the provider has a verified quota reader, lists the provider's
    models, turns the provider on when this was its first credential and
    the key is usable, and turns on the catalogue's suggested models when
    none is on yet. The three suggestions are what step 3 ("default model")
    offers.

The check follows Hermes (a free read-only probe before saving, "unknown"
said honestly) and OpenCode's connect flow (ending on "choose a model").
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional, TypedDict

_log = logging.getLogger(__name__)

CheckResult = Literal["works", "rejected", "no_credit", "not_available", "unreachable", "unverified"]
#: Results whose key the provider accepted (usable, or once topped up): they turn a
#: new provider on. ``unverified`` counts only when the provider answered 200 to the
#: key (auth accepted, usability not proven); a 5xx or no answer proves nothing.
ENABLING_RESULTS = frozenset({"works", "no_credit", "unverified"})


def key_accepted(check: "KeyCheck") -> bool:
    """The provider accepted this key, so connecting may turn the provider on."""
    if check["result"] not in ENABLING_RESULTS:
        return False
    return check["result"] != "unverified" or check["http_status"] == 200
SUGGESTED = 3


class Balance(TypedDict):
    amount: float
    unit: str            # usd | cny | credits
    label: str           # 'Balance', 'Key limit', 'Credits'


class SuggestedModel(TypedDict):
    id: str
    name: str
    context_window: Optional[int]
    #: USD per million tokens, from the catalogue; null when unpriced.
    input_cost: Optional[float]
    output_cost: Optional[float]
    reasoning: bool
    vision: bool
    tools: bool
    enabled: bool


class KeyCheck(TypedDict):
    result: CheckResult
    http_status: Optional[int]
    #: The probe's own sentence (never the key); the web prints its own words per result.
    detail: Optional[str]
    #: Which provider page fixes it: 'keys' for a rejected key, 'billing' for no credit.
    fix_page: Optional[Literal["keys", "billing"]]
    fix_url: Optional[str]
    balance: Optional[Balance]
    model_count: Optional[int]
    models: list[SuggestedModel]
    #: Models of this provider that are on after the check.
    enabled_models: list[str]
    provider_enabled: bool
    #: This check turned the provider on.
    auto_enabled: bool


def result_of(probe: Any) -> CheckResult:
    """The one-line result of a ``CredentialResult``."""
    from . import credentials as c
    status = getattr(probe, "status", None)
    if status in (c.VALID, c.VALID_MODEL_UNAVAILABLE):
        return "works" if status == c.VALID_MODEL_UNAVAILABLE or c.probe_proves_usable(probe) else "unverified"
    if status == c.INVALID_CREDENTIAL:
        return "rejected"
    if status == c.VALID_NO_BALANCE:
        return "no_credit"
    if status == c.NOT_AVAILABLE:
        return "not_available"
    if status == c.UNKNOWN and getattr(probe, "http_status", None) is None and getattr(probe, "model", None) is None:
        return "unreachable"
    return "unverified"


def check_of(provider_id: str, probe: Any) -> KeyCheck:
    """The check before :func:`connect` adds models and balance."""
    from openprogram.models.providers.catalog.consoles import console_url
    result = result_of(probe)
    page = "keys" if result == "rejected" else "billing" if result == "no_credit" else None
    return {
        "result": result,
        "http_status": getattr(probe, "http_status", None),
        "detail": getattr(probe, "detail", None),
        "fix_page": page,  # type: ignore[typeddict-item]
        "fix_url": console_url(provider_id, page) if page else None,
        "balance": None,
        "model_count": None,
        "models": [],
        "enabled_models": [],
        "provider_enabled": _provider_enabled(provider_id),
        "auto_enabled": False,
    }


def _provider_enabled(provider_id: str) -> bool:
    try:
        from openprogram.models.providers.settings.storage import _read_providers_cfg
        return bool((_read_providers_cfg().get(provider_id) or {}).get("enabled"))
    except Exception:
        return False


def balance_of(provider_id: str, account: str) -> Optional[Balance]:
    """The money limit a key reported last (balance, key limit or credits), if any."""
    from openprogram.models.usage.quota import default_store
    for report in default_store.reports(provider_id):
        if report.account != account:
            continue
        for limit in report.limits:
            base = limit.limit_key.rsplit(":", 1)[-1]
            if base in ("balance", "key_limit", "credits") and limit.remaining is not None:
                return {"amount": float(limit.remaining), "unit": limit.unit, "label": limit.label}
    return None


def read_new_balance(provider_id: str, account: str) -> Optional[Balance]:
    """Read a just-stored key's quota endpoint once (verified readers only), then its balance."""
    try:
        from openprogram.models.usage.quota import forget_accounts
        from openprogram.models.usage.quota_fetchers import refresh
        forget_accounts()
        refresh([provider_id], reason="key_added")
    except Exception:
        _log.debug("balance of the new %s key not read", provider_id, exc_info=True)
    return balance_of(provider_id, account)


def _rank_key(row: dict) -> tuple:
    released = str(row.get("release_date") or "")
    return (not row.get("enabled"), not row.get("tools", True), released == "", _neg(released), row["id"])


def _neg(text: str) -> tuple:
    return tuple(-ord(ch) for ch in text)


def suggested_models(provider_id: str, rows: Optional[list[dict]] = None) -> tuple[int, list[SuggestedModel]]:
    """``(model count, top three)``: models already on first, then the newest with tools."""
    from openprogram.models.providers.catalog.sources import models_dev
    if rows is None:
        from .listing import list_models_for_provider
        rows = list_models_for_provider(provider_id)
    merged = []
    for row in rows:
        mid = row.get("id")
        if not mid:
            continue
        meta = models_dev.lookup(provider_id, mid) or {}
        outputs = meta.get("output_modalities") or ["text"]
        if "text" not in outputs:
            continue
        merged.append({**meta, **{k: v for k, v in row.items() if v not in (None, 0, "")}, "id": mid,
                       "release_date": meta.get("release_date")})
    merged.sort(key=_rank_key)
    top: list[SuggestedModel] = [{
        "id": row["id"],
        "name": str(row.get("name") or row["id"]),
        "context_window": int(row["context_window"]) if row.get("context_window") else None,
        "input_cost": row.get("input_cost"),
        "output_cost": row.get("output_cost"),
        "reasoning": bool(row.get("reasoning")),
        "vision": bool(row.get("vision")),
        "tools": bool(row.get("tools", True)),
        "enabled": bool(row.get("enabled")),
    } for row in merged[:SUGGESTED]]
    return len(merged), top


def connect(provider_id: str, check: KeyCheck, *, account: str, first_credential: bool,
            read_balance: bool = True) -> KeyCheck:
    """Finish a stored, not-rejected key: balance, models, provider and models on."""
    out: KeyCheck = dict(check)  # type: ignore[assignment]
    if read_balance:
        out["balance"] = read_new_balance(provider_id, account)
    usable = key_accepted(check)
    if usable and first_credential and not out["provider_enabled"]:
        from .toggle import toggle_provider
        toggle_provider(provider_id, True)
        out["provider_enabled"] = out["auto_enabled"] = True
    if not usable:
        return out
    try:
        count, top = suggested_models(provider_id)
    except Exception:
        _log.warning("models of %s not listed after its key was added", provider_id, exc_info=True)
        return out
    out["model_count"] = count
    enabled = [m["id"] for m in top if m["enabled"]]
    if not enabled and out["provider_enabled"]:
        from .toggle import toggle_model
        for model in top:
            try:
                toggle_model(provider_id, model["id"], True)
                model["enabled"] = True
            except Exception:
                _log.warning("model %s of %s not turned on", model["id"], provider_id, exc_info=True)
    out["models"] = top
    out["enabled_models"] = _enabled_model_ids(provider_id) or [m["id"] for m in top if m["enabled"]]
    return out


def _enabled_model_ids(provider_id: str) -> list[str]:
    try:
        from openprogram.models.providers.settings.storage import _read_providers_cfg
        from .listing import _enabled_ids
        return sorted(_enabled_ids(_read_providers_cfg().get(provider_id) or {}))
    except Exception:
        return []


__all__ = [
    "Balance",
    "CheckResult",
    "ENABLING_RESULTS",
    "KeyCheck",
    "SuggestedModel",
    "balance_of",
    "check_of",
    "connect",
    "key_accepted",
    "read_new_balance",
    "result_of",
    "suggested_models",
]
