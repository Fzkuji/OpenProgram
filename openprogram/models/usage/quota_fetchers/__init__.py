"""Read-only quota endpoints, behind one interface, and when they are read.

Each module here answers one provider's quota endpoint with ``parse(payload,
now) -> list[QuotaLimit]`` and names its request with ``request(cred) ->
(url, headers, configured_base)``. A fetcher whose payload shape comes from
peer code and not from a response OpenProgram has seen ships with
``VERIFIED = False``: it is never sent until a real response has been
recorded as its test fixture and the flag flipped. Unverified sources are
listed in docs/reference/design/providers/provider-quota.html.

When an endpoint is read (never on a timer, never a paid request):

  * a page or the CLI opens and the key's last read -- answered or not --
    is older than :data:`STALE_AFTER_S` (``reason="open"``), so a key whose
    endpoint refuses it (401, 5xx, an unparsable body) is not asked again on
    every open;
  * the person asks (Refresh, ``providers status --refresh``; ``"manual"``);
  * a call failed with 402, 429 or a spent quota (``"failure"``), at most
    once per key per :data:`STALE_AFTER_S`;
  * right after a key is added (``"key_added"``).

One read is in flight per key. A 429 from the endpoint itself holds the key
for its ``Retry-After``, or :data:`ENDPOINT_429_WAIT_S` without one (the
Oh My Pi Claude fetcher's rule).

Each read is recorded in ``usage.db`` (``quota_reads``): when, its HTTP
status, why it failed (cleared by a read that answered), the 429 hold and
the last failure read. Every process -- the Worker, each CLI run -- decides
from that record, so a refused read is not repeated by the next process. A
failed read leaves the key's earlier values, with their own observed times.
"""
from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..quota import SOURCE_ENDPOINT, QuotaLimit, default_store

_log = logging.getLogger(__name__)

#: An endpoint value older than this is read again when a page opens.
STALE_AFTER_S = 10 * 60
#: Wait after the quota endpoint itself answered 429 without Retry-After.
ENDPOINT_429_WAIT_S = 5 * 60
#: A request's time budget.
TIMEOUT_S = 10.0

REASONS = ("open", "manual", "failure", "key_added")

_MODULES = ("openrouter", "codex_usage", "zai", "minimax", "opencode_go", "github_copilot")


@dataclass
class FetchCredential:
    """What one quota read needs: never stored, never logged."""

    provider: str
    account: str          # credential id, '' for an environment key
    token: str
    base_url: Optional[str] = None


HttpGet = Callable[[str, dict, Optional[str]], Optional[tuple[int, str, dict]]]


def _modules() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        for provider in module.PROVIDERS:
            out[provider] = module
    return out


def fetcher_for(provider: str, *, include_unverified: bool = False) -> Any:
    """The fetcher module of a provider, or ``None`` (only verified ones by default)."""
    module = _modules().get(provider)
    if module is None or (not module.VERIFIED and not include_unverified):
        return None
    return module


def sources() -> list[dict]:
    """Every provider with a fetcher and whether it is sent (for the docs and CLI)."""
    return [
        {"provider": provider, "endpoint": module.ENDPOINT, "verified": bool(module.VERIFIED),
         "shape_from": module.SHAPE_FROM}
        for provider, module in sorted(_modules().items())
    ]


def _http_get(url: str, headers: dict, configured_base: Optional[str]) -> Optional[tuple[int, str, dict]]:
    """``(status, body, headers)`` through the guarded client, or ``None`` on a transport error.

    The request goes out as a read of the provider's own endpoint, the
    same managed consumer the key check uses (``credentials._http_get``),
    frozen to the origin of ``configured_base`` (or of ``url``).
    """
    from openprogram.host.security import safe_http
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

    base = configured_base or url
    consumer = "webui.model_listing.configured"
    try:
        client = safe_http.configured_safe_client(
            consumer, base,
            owner_exception=OwnerURLException(consumer=consumer, origin=normalize_origin(base)),
        )
        with client:
            response = client.get(url, headers=headers, timeout=TIMEOUT_S)
    except Exception:
        return None
    return response.status_code, response.text, dict(response.headers)


def credentials(provider: str) -> list[FetchCredential]:
    """Every key of a provider: its stored credentials, else its environment key."""
    out: list[FetchCredential] = []
    try:
        from openprogram.models.auth.resolver import resolve_connection
        from openprogram.models.auth.store import get_store
        for pool in get_store().list_pools():
            if pool.provider_id != provider:
                continue
            for cred in pool.credentials:
                try:
                    conn = resolve_connection(cred)
                except Exception:
                    conn = None
                if conn is not None and conn.auth_value:
                    out.append(FetchCredential(provider, cred.credential_id, conn.auth_value, conn.base_url))
    except Exception:
        _log.debug("no stored credentials for %s", provider, exc_info=True)
    if not out:
        try:
            from openprogram.models.providers.settings.env_api_keys import resolve_provider_key
            key = resolve_provider_key(provider)
        except Exception:
            key = None
        if key:
            out.append(FetchCredential(provider, "", key))
    return out


class _Policy:
    """Reads in flight in this process; everything else is in :func:`_read_record`."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.in_flight: set[tuple[str, str]] = set()
        #: The read records when ``usage.db`` cannot be written.
        self.fallback: dict[tuple[str, str], dict] = {}

    def reset(self) -> None:
        with self.lock:
            self.in_flight.clear()
            self.fallback.clear()


_policy = _Policy()


def _read_record(provider: str, account: str) -> dict:
    """A key's last read, from ``usage.db`` (shared by every process)."""
    try:
        rows = default_store.ledger().quota_reads(provider, account)
    except Exception:
        _log.debug("quota read record of %s not readable", provider, exc_info=True)
        rows = []
    record = dict(_policy.fallback.get((provider, account), {}))
    if rows:
        record.update({k: v for k, v in rows[0].items() if k not in ("provider", "account")})
    return record


def _note_read(provider: str, account: str, **fields: Any) -> None:
    try:
        default_store.ledger().put_quota_read(provider, account, **fields)
        _policy.fallback.pop((provider, account), None)
    except Exception:
        _log.warning("quota read of %s not recorded", provider, exc_info=True)
        _policy.fallback.setdefault((provider, account), {}).update(fields)


def last_reads(provider: str) -> dict[str, dict]:
    """Each key's last read of a provider's quota endpoint, by account."""
    try:
        rows = default_store.ledger().quota_reads(provider)
    except Exception:
        rows = []
    out = {row["account"]: row for row in rows}
    for (p, account), fields in list(_policy.fallback.items()):
        if p == provider:
            out[account] = {**out.get(account, {"provider": p, "account": account}), **fields}
    return out


def _due(cred: FetchCredential, reason: str, now: float) -> bool:
    key = (cred.provider, cred.account)
    if key in _policy.in_flight:
        return False
    record = _read_record(cred.provider, cred.account)
    if (record.get("held_until") or 0.0) > now:
        return False
    if reason == "open":
        stored = default_store.last_fetched(cred.provider, cred.account, SOURCE_ENDPOINT)
        last = max((t for t in (stored, record.get("read_at")) if t is not None), default=None)
        return last is None or now - last >= STALE_AFTER_S
    if reason == "failure":
        return now - (record.get("failure_read_at") or 0.0) >= STALE_AFTER_S
    return True


def _retry_after(headers: dict) -> Optional[float]:
    from openprogram.models.providers.utils.errors import parse_retry_after
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    return parse_retry_after(lowered)


def fetch_one(module: Any, cred: FetchCredential, *, http_get: HttpGet = _http_get,
              now: Optional[float] = None) -> Optional[list[QuotaLimit]]:
    """Read one key's quota endpoint and record what it says. ``None`` when nothing was learnt."""
    now = time.time() if now is None else now
    url, headers, configured = module.request(cred)
    from openprogram.models.listing.probe_log import KIND_QUOTA, probing
    with probing(cred.provider, KIND_QUOTA):
        answer = http_get(url, headers, configured)
    account = (cred.provider, cred.account)
    if answer is None:
        _note_read(*account, read_at=now, status=None, error="unreachable")
        return None
    status, body, response_headers = answer
    if status == 429:
        wait = _retry_after(response_headers) or ENDPOINT_429_WAIT_S
        _note_read(*account, read_at=now, status=status, error="HTTP 429", held_until=now + wait)
        return None
    if status != 200:
        _note_read(*account, read_at=now, status=status, error=f"HTTP {status}")
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        _note_read(*account, read_at=now, status=status, error="answer did not parse")
        return None
    limits = module.parse(payload, now)
    if limits:
        default_store.observe(cred.provider, cred.account, limits, force_write=True, replace_source=True)
    _note_read(*account, read_at=now, status=status, error=None if limits else "no quota in the answer")
    return limits or None


def refresh(providers: Optional[Iterable[str]] = None, *, reason: str = "open",
            http_get: HttpGet = _http_get, now: Optional[float] = None) -> dict[str, int]:
    """Read the quota endpoints that are due for ``reason``; how many keys each provider answered for.

    Providers without a verified fetcher are skipped: they keep the values
    their replies' headers carry, or report no quota data.
    """
    if reason not in REASONS:
        raise ValueError(f"unknown quota refresh reason {reason!r}")
    now = time.time() if now is None else now
    modules = _modules()
    names = list(providers) if providers is not None else list(modules)
    answered: dict[str, int] = {}
    for provider in names:
        module = fetcher_for(provider)
        if module is None:
            continue
        for cred in credentials(provider):
            key = (cred.provider, cred.account)
            with _policy.lock:
                if not _due(cred, reason, now):
                    continue
                _policy.in_flight.add(key)
            if reason == "failure":
                _note_read(*key, failure_read_at=now)
            try:
                if fetch_one(module, cred, http_get=http_get, now=now):
                    answered[provider] = answered.get(provider, 0) + 1
            except Exception:
                _log.warning("quota read of %s failed", provider, exc_info=True)
            finally:
                with _policy.lock:
                    _policy.in_flight.discard(key)
    return answered


def refresh_later(providers: Optional[Iterable[str]] = None, *, reason: str) -> None:
    """:func:`refresh` on a background thread, when any of ``providers`` has a verified fetcher."""
    names = list(providers) if providers is not None else None
    if names is not None and not any(fetcher_for(p) for p in names):
        return
    _spawn(lambda: refresh(names, reason=reason))


def _spawn(work: Callable[[], Any]) -> None:
    """Run ``work`` on a daemon thread (replaced in tests, which never read in the background)."""
    threading.Thread(target=work, name="quota-refresh", daemon=True).start()


def after_failure(provider: str, exc: BaseException) -> None:
    """A call failed: read the provider's quota once if the failure was about it."""
    try:
        if fetcher_for(provider) is None:
            return
        from openprogram.models.providers.utils.errors import _PERMANENT_MARKERS_QUOTA
        status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
        text = f"{exc} {getattr(exc, 'error_text', '') or ''}".lower()
        if status in (402, 429) or any(marker in text for marker in _PERMANENT_MARKERS_QUOTA):
            refresh_later([provider], reason="failure")
    except Exception:
        _log.debug("quota read after a failure not scheduled", exc_info=True)


def after_key_added(provider: str) -> None:
    """A key was just saved: read its quota now."""
    from ..quota import forget_accounts
    forget_accounts()
    refresh_later([provider], reason="key_added")


def reset_policy() -> None:
    """Forget this process's in-flight reads (tests)."""
    _policy.reset()


__all__ = [
    "ENDPOINT_429_WAIT_S",
    "FetchCredential",
    "REASONS",
    "STALE_AFTER_S",
    "after_failure",
    "after_key_added",
    "credentials",
    "fetch_one",
    "fetcher_for",
    "last_reads",
    "refresh",
    "refresh_later",
    "reset_policy",
    "sources",
]
