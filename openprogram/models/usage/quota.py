"""Provider quota: how much of a provider's limits is left, per key.

One model for every provider. A provider reports its limits in one of four
kinds, chosen by what it sends:

  * ``windowed``   — a subscription's usage windows (Codex 5h and weekly,
                     coding plans): percent used and when each resets.
  * ``balance``    — a prepaid amount or a key's spending limit (OpenRouter).
  * ``rate_limit`` — only per-minute request/token headroom from response
                     headers (OpenAI and Anthropic API keys).
  * ``none``       — nothing reported; the page says so instead of drawing
                     an empty bar.

Each value is a :class:`QuotaLimit` carrying where it came from (``source``:
``headers``, ``frame``, ``endpoint``) and when it was observed. The shape
follows Oh My Pi's ``UsageLimit`` (``packages/ai/src/usage.ts``); labels and
the per-cent-left reading follow Codex (``tui/src/status/rate_limits.rs``).

:func:`observe_headers` and :func:`observe_codex_frame` are called by the
adapters with what an ordinary reply already carries, at no cost. Values
are kept in memory at once and written to ``usage.db`` (``quota_snapshots``)
at most every :data:`WRITE_EVERY_S` per key; an endpoint read is written at
once. Raw headers and bodies are never stored.

See docs/reference/design/providers/provider-quota.html.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from openprogram.models.providers.utils.http_transport.rate_limit import (
    CodexRateLimits,
    parse_codex_rate_limit_frame,
    parse_codex_rate_limits,
    parse_rate_limit,
)

_log = logging.getLogger(__name__)

KIND_WINDOWED = "windowed"
KIND_BALANCE = "balance"
KIND_RATE_LIMIT = "rate_limit"
KIND_NONE = "none"

SOURCE_HEADERS = "headers"
SOURCE_FRAME = "frame"
SOURCE_ENDPOINT = "endpoint"

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_EXHAUSTED = "exhausted"
STATUS_UNKNOWN = "unknown"

#: Used share at which a limit turns ``warning`` (the bar's warning tone).
WARNING_AT = 0.80
#: Used share at which a limit reads as nearly spent (the danger tone).
DANGER_AT = 0.95
#: A snapshot older than this is drawn as stale (Codex marks 15 minutes).
STALE_AFTER_S = 15 * 60
#: Header observations reach the disk at most this often per key.
WRITE_EVERY_S = 30.0

_BALANCE_KEYS = frozenset({"balance", "key_limit", "credits"})
_RATE_KEYS = frozenset({"rpm", "tpm", "tpm_in", "tpm_out"})


@dataclass
class QuotaLimit:
    """One limit of one key, as last observed."""

    limit_key: str            # 'primary', 'secondary', 'rpm', 'tpm', 'key_limit', ...
    label: str                # '5h', 'Weekly', 'Req/min', 'Key limit'
    unit: str                 # percent | requests | tokens | usd | cny | credits
    source: str               # headers | frame | endpoint
    fetched_at: float
    used: Optional[float] = None
    limit: Optional[float] = None
    remaining: Optional[float] = None
    used_fraction: Optional[float] = None
    window_minutes: Optional[int] = None
    resets_at: Optional[float] = None   # Unix seconds
    status: str = STATUS_UNKNOWN

    def __post_init__(self) -> None:
        if self.used_fraction is None:
            if self.limit not in (None, 0) and self.used is not None:
                self.used_fraction = self.used / self.limit
            elif self.limit not in (None, 0) and self.remaining is not None:
                self.used_fraction = 1 - self.remaining / self.limit
        if self.used_fraction is not None:
            self.used_fraction = min(1.0, max(0.0, float(self.used_fraction)))
        if self.status == STATUS_UNKNOWN:
            self.status = limit_status(self.used_fraction, self.remaining)

    def to_row(self, provider: str, account: str) -> dict:
        row = asdict(self)
        row["lim"] = row.pop("limit")
        row.update(provider=provider, account=account)
        return row

    @classmethod
    def from_row(cls, row: dict) -> "QuotaLimit":
        return cls(
            limit_key=row["limit_key"], label=row["label"], unit=row["unit"],
            source=row["source"], fetched_at=float(row["fetched_at"]),
            used=row.get("used"), limit=row.get("lim"), remaining=row.get("remaining"),
            used_fraction=row.get("used_fraction"), window_minutes=row.get("window_minutes"),
            resets_at=row.get("resets_at"), status=row.get("status") or STATUS_UNKNOWN,
        )

    def wire(self, now: Optional[float] = None) -> dict:
        """The limit as the status route and CLI carry it."""
        now = time.time() if now is None else now
        out = asdict(self)
        out["stale"] = now - self.fetched_at > STALE_AFTER_S
        out["left_fraction"] = None if self.used_fraction is None else round(1 - self.used_fraction, 4)
        return out


@dataclass
class QuotaReport:
    """Every limit one key of one provider reported."""

    provider: str
    account: str
    limits: list[QuotaLimit] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return report_kind(self.limits)

    @property
    def fetched_at(self) -> Optional[float]:
        return max((limit.fetched_at for limit in self.limits), default=None)

    def worst(self) -> Optional[QuotaLimit]:
        """The limit closest to running out (Oh My Pi's card shows this one)."""
        ranked = [limit for limit in self.limits if limit.used_fraction is not None]
        return max(ranked, key=lambda limit: limit.used_fraction, default=None)

    def wire(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        fetched = self.fetched_at
        return {
            "provider": self.provider,
            "account": self.account,
            "kind": self.kind,
            "fetched_at": fetched,
            "stale": fetched is not None and now - fetched > STALE_AFTER_S,
            "limits": [limit.wire(now) for limit in _ordered(self.limits)],
        }


def limit_status(used_fraction: Optional[float], remaining: Optional[float] = None) -> str:
    """``ok`` / ``warning`` (≥ 80% used) / ``exhausted`` (all used) / ``unknown``."""
    if remaining is not None and remaining <= 0:
        return STATUS_EXHAUSTED
    if used_fraction is None:
        return STATUS_UNKNOWN
    if used_fraction >= 1:
        return STATUS_EXHAUSTED
    if used_fraction >= WARNING_AT:
        return STATUS_WARNING
    return STATUS_OK


def report_kind(limits: Iterable[QuotaLimit]) -> str:
    """Which of the four quota kinds a set of limits is."""
    limits = list(limits)
    if any(limit.unit == "percent" or (limit.window_minutes or 0) > 1 for limit in limits):
        return KIND_WINDOWED
    if any(limit.limit_key in _BALANCE_KEYS for limit in limits):
        return KIND_BALANCE
    if any(limit.limit_key in _RATE_KEYS for limit in limits):
        return KIND_RATE_LIMIT
    return KIND_NONE


def should_warn(limit: QuotaLimit, now: Optional[float] = None) -> bool:
    """Whether a window deserves an early warning (Claude Code's time-aware rule).

    At 95% used it always does. From 80% it does only when usage is ahead
    of the time elapsed in the window, so a window used steadily stays quiet.
    """
    fraction = limit.used_fraction
    if fraction is None:
        return False
    if fraction >= DANGER_AT:
        return True
    if fraction < WARNING_AT:
        return False
    if not limit.window_minutes or limit.resets_at is None:
        return True
    now = time.time() if now is None else now
    window_s = limit.window_minutes * 60
    elapsed = 1 - max(0.0, limit.resets_at - now) / window_s
    return fraction > max(0.0, min(1.0, elapsed))


_ORDER = ("primary", "secondary", "credits", "key_limit", "balance", "rpm", "tpm", "tpm_in", "tpm_out")


def _ordered(limits: Iterable[QuotaLimit]) -> list[QuotaLimit]:
    def rank(limit: QuotaLimit) -> tuple:
        base = limit.limit_key.rsplit(":", 1)[-1]
        prefixed = ":" in limit.limit_key
        return (prefixed, _ORDER.index(base) if base in _ORDER else len(_ORDER), limit.limit_key)
    return sorted(limits, key=rank)


# Normalisation: headers and frames to limits

def window_label(minutes: Optional[int], *, secondary: bool = False) -> str:
    """Codex's window names: 5h, Daily, Weekly, Monthly, Annual."""
    if minutes:
        for target, name in ((300, "5h"), (1440, "Daily"), (10080, "Weekly"),
                             (43200, "Monthly"), (525600, "Annual")):
            if abs(minutes - target) <= max(3, target // 20):
                return name
        if minutes % 1440 == 0:
            return f"{minutes // 1440}d"
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"
    return "Weekly" if secondary else "5h"


def codex_limits(families: Iterable[CodexRateLimits], *, source: str, now: float) -> list[QuotaLimit]:
    """Codex windows and credits as quota limits."""
    out: list[QuotaLimit] = []
    for family in families:
        prefix = "" if family.limit_id == "codex" else f"{family.limit_id}:"
        name = family.limit_name or ("" if not prefix else family.limit_id.replace("_", "-"))
        for key, window in (("primary", family.primary), ("secondary", family.secondary)):
            if window is None:
                continue
            used = max(0.0, min(100.0, window.used_percent))
            label = window_label(window.window_minutes, secondary=key == "secondary")
            out.append(QuotaLimit(
                limit_key=prefix + key, label=f"{name} {label}".strip(), unit="percent",
                source=source, fetched_at=now, used=used, limit=100.0, remaining=100.0 - used,
                used_fraction=used / 100.0, window_minutes=window.window_minutes,
                resets_at=float(window.resets_at) if window.resets_at else None,
            ))
        credits = family.credits
        if credits is not None and (credits.unlimited or credits.has_credits):
            balance = _number(credits.balance)
            out.append(QuotaLimit(
                limit_key=prefix + "credits", label="Credits", unit="credits", source=source,
                fetched_at=now, remaining=None if credits.unlimited else balance,
                status=STATUS_OK,
            ))
    return out


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def reset_epoch(value: Optional[str], now: float) -> Optional[float]:
    """A reset header as Unix seconds.

    OpenAI sends a duration (``6m0s``, ``1s``, ``20ms``); Anthropic sends an
    RFC 3339 time (``2026-11-13T14:02:00Z``).
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    parts = _DURATION_PART.findall(text)
    if parts and "".join(n + u for n, u in parts) == text:
        scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
        return now + sum(float(n) * scale[u] for n, u in parts)
    number = _number(text)
    return now + number if number is not None else None


def _number(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def header_limits(headers: Any, *, now: Optional[float] = None) -> list[QuotaLimit]:
    """Every limit an ordinary reply's headers carry, in any of the three schemes."""
    if headers is None:
        return []
    now = time.time() if now is None else now
    codex = parse_codex_rate_limits(headers)
    if codex:
        return codex_limits(codex, source=SOURCE_HEADERS, now=now)
    info = parse_rate_limit(headers)
    if not info.present:
        return []
    out: list[QuotaLimit] = []
    for key, label, unit, lim, rem, reset in (
        ("rpm", "Req/min", "requests", info.limit_requests, info.remaining_requests, info.reset_requests),
        ("tpm", "Tok/min", "tokens", info.limit_tokens, info.remaining_tokens, info.reset_tokens),
        ("tpm_in", "Input tok/min", "tokens", info.limit_input_tokens,
         info.remaining_input_tokens, info.reset_input_tokens),
        ("tpm_out", "Output tok/min", "tokens", info.limit_output_tokens,
         info.remaining_output_tokens, info.reset_output_tokens),
    ):
        if lim is None and rem is None:
            continue
        out.append(QuotaLimit(
            limit_key=key, label=label, unit=unit, source=SOURCE_HEADERS, fetched_at=now,
            limit=float(lim) if lim is not None else None,
            remaining=float(rem) if rem is not None else None,
            used=float(lim - rem) if lim is not None and rem is not None else None,
            window_minutes=1, resets_at=reset_epoch(reset, now),
        ))
    return out


# Which key a reply was sent with

_account_cache: dict[tuple[str, str], Optional[str]] = {}
_account_lock = threading.Lock()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def account_for_token(provider: str, token: Optional[str]) -> Optional[str]:
    """The stored credential a bearer token belongs to.

    Returns its id (an environment variable's row included), ``""`` for a
    key that is not among the provider's credentials, or ``None`` when there
    is no token. Only a digest of the token is kept, to remember the answer.
    """
    if not token:
        return None
    key = (provider, _digest(token))
    with _account_lock:
        if key in _account_cache:
            return _account_cache[key]
    found = ""
    try:
        import os
        from openprogram.models.auth.store import get_store
        for cred in get_store().get(provider).credentials:
            value = (os.environ.get(cred.metadata.get("env_var") or "", "")
                     if cred.source == "env" else cred.payload.auth_value)
            if value and value.strip() == token:
                found = cred.id
                break
    except Exception:
        _log.debug("no credential store for %s", provider, exc_info=True)
    with _account_lock:
        if len(_account_cache) > 256:
            _account_cache.clear()
        _account_cache[key] = found
    return found


def forget_accounts() -> None:
    """Drop remembered token → credential answers (after keys change)."""
    with _account_lock:
        _account_cache.clear()


# The store: memory first, disk throttled

Listener = Callable[[dict], None]


class QuotaStore:
    """Latest limits per provider and key: in memory, mirrored to ``usage.db``."""

    def __init__(self, ledger: Any = None) -> None:
        self._ledger = ledger
        self._lock = threading.Lock()
        self._limits: dict[tuple[str, str], dict[str, QuotaLimit]] = {}
        self._dirty: dict[tuple[str, str], set[str]] = {}
        self._written: dict[tuple[str, str], float] = {}
        #: The plan each key's replies last named (Codex ``plan_type``); memory only.
        self._plans: dict[tuple[str, str], str] = {}
        self._loaded = False
        self._listener: Optional[Listener] = None

    def ledger(self) -> Any:
        if self._ledger is None:
            from .ledger import default_ledger
            return default_ledger
        return self._ledger

    def set_listener(self, listener: Optional[Listener]) -> None:
        """Hand each changed report (as :meth:`QuotaReport.wire`) to ``listener``."""
        self._listener = listener

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            rows = self.ledger().quota_rows()
        except Exception:
            _log.warning("stored quota could not be read", exc_info=True)
            rows = []
        with self._lock:
            if self._loaded:
                return
            for row in rows:
                key = (row["provider"], row["account"])
                current = self._limits.setdefault(key, {})
                if row["limit_key"] not in current:
                    current[row["limit_key"]] = QuotaLimit.from_row(row)
            self._loaded = True

    def observe(
        self, provider: str, account: str, limits: list[QuotaLimit], *,
        force_write: bool = False, replace_source: bool = False,
    ) -> None:
        """Take a new report for one key.

        ``replace_source`` drops the key's stored limits of the same source
        that this report no longer carries (an endpoint read is complete;
        headers are not, as some replies carry only some of them).
        """
        if not provider or account is None:
            return
        self._load()
        key = (provider, account)
        now = max((limit.fetched_at for limit in limits), default=time.time())
        dropped: list[str] = []
        with self._lock:
            current = self._limits.setdefault(key, {})
            changed = False
            if replace_source and limits:
                sources = {limit.source for limit in limits}
                fresh = {limit.limit_key for limit in limits}
                dropped = [k for k, v in current.items() if v.source in sources and k not in fresh]
                for k in dropped:
                    current.pop(k)
                changed = bool(dropped)
            for limit in limits:
                before = current.get(limit.limit_key)
                if before is None or before.status != limit.status or before.used_fraction != limit.used_fraction \
                        or before.remaining != limit.remaining:
                    changed = True
                current[limit.limit_key] = limit
                self._dirty.setdefault(key, set()).add(limit.limit_key)
            due = force_write or now - self._written.get(key, 0.0) >= WRITE_EVERY_S
            rows = []
            if due:
                rows = [current[k].to_row(provider, account) for k in self._dirty.pop(key, set()) if k in current]
                self._written[key] = now
            report = QuotaReport(provider, account, list(current.values()))
        if rows or dropped:
            try:
                self.ledger().put_quota_rows(rows)
                if dropped:
                    self.ledger().drop_quota_rows(provider, account, dropped)
            except Exception:
                _log.warning("quota for %s not stored", provider, exc_info=True)
        if changed and self._listener is not None:
            try:
                self._listener(report.wire(now))
            except Exception:
                _log.debug("quota listener failed", exc_info=True)

    def note_plan(self, provider: str, account: str, plan: Optional[str]) -> None:
        """Remember the plan a key's reply named."""
        if provider and isinstance(plan, str) and plan.strip():
            with self._lock:
                self._plans[(provider, account or "")] = plan.strip().lower()

    def plan(self, provider: str, accounts: Optional[Iterable[str]] = None) -> Optional[str]:
        """The plan a provider's replies last named, for one of ``accounts``
        (any key when ``None``); ``None`` when no reply has said."""
        wanted = None if accounts is None else set(accounts)
        with self._lock:
            for (p, account), plan in self._plans.items():
                if p == provider and (wanted is None or account in wanted):
                    return plan
        return None

    def flush(self) -> None:
        """Write every observation still held in memory only."""
        with self._lock:
            rows = []
            for key, names in self._dirty.items():
                current = self._limits.get(key, {})
                rows += [current[n].to_row(*key) for n in names if n in current]
                self._written[key] = time.time()
            self._dirty.clear()
        if rows:
            try:
                self.ledger().put_quota_rows(rows)
            except Exception:
                _log.warning("quota not stored", exc_info=True)

    def reports(self, provider: Optional[str] = None) -> list[QuotaReport]:
        """The latest report of every key (of one provider, or all)."""
        self._load()
        with self._lock:
            return [
                QuotaReport(p, a, list(limits.values()))
                for (p, a), limits in sorted(self._limits.items())
                if limits and (provider is None or p == provider)
            ]

    def last_fetched(self, provider: str, account: str, source: Optional[str] = None) -> Optional[float]:
        """When a key's limits (of one source) were last observed."""
        self._load()
        with self._lock:
            limits = self._limits.get((provider, account), {}).values()
            times = [limit.fetched_at for limit in limits if source is None or limit.source == source]
        return max(times, default=None)

    def reset(self) -> None:
        """Forget what is held in memory (tests, or after the ledger moves)."""
        with self._lock:
            self._limits.clear()
            self._dirty.clear()
            self._written.clear()
            self._plans.clear()
            self._loaded = False


default_store = QuotaStore()


def observe_headers(provider: str, token: Optional[str], headers: Any, *,
                    account: Optional[str] = None) -> list[QuotaLimit]:
    """Record the limits a reply's headers carry. Never raises.

    ``account`` names the credential when the adapter knows it; otherwise
    it is found from ``token``.
    """
    try:
        limits = header_limits(headers)
        if not limits:
            return []
        if account is None:
            account = account_for_token(provider, token)
        if account is None:
            account = ""
        default_store.observe(provider, account, limits)
        return limits
    except Exception:
        _log.debug("quota headers of %s not read", provider, exc_info=True)
        return []


def observe_codex_frame(provider: str, token: Optional[str], frame: Any, *,
                        account: Optional[str] = None) -> list[QuotaLimit]:
    """Record a Codex ``codex.rate_limits`` WebSocket frame. Never raises.

    ``account`` names the credential the request was sent with (an imported
    sign-in's token is read from another tool's file, so its token never
    matches a stored value); otherwise it is found from ``token``."""
    try:
        family = parse_codex_rate_limit_frame(frame)
        if family is None:
            return []
        if account is None:
            account = account_for_token(provider, token) or ""
        default_store.note_plan(provider, account, family.plan_type)
        if not family.present:
            return []
        limits = codex_limits([family], source=SOURCE_FRAME, now=time.time())
        default_store.observe(provider, account, limits)
        return limits
    except Exception:
        _log.debug("codex rate-limit frame not read", exc_info=True)
        return []


def exhausted_limit(provider: str, account: Optional[str] = None) -> Optional[QuotaLimit]:
    """The spent limit of a provider's key that resets last, if any is spent."""
    found = exhausted_report(provider, account)
    return found[1] if found else None


def exhausted_report(provider: str, account: Optional[str] = None) -> Optional[tuple[str, QuotaLimit, list[QuotaLimit]]]:
    """``(account, spent limit, the key's other limits)`` for the spent limit that resets last."""
    spent = [
        (report, limit)
        for report in default_store.reports(provider)
        if account is None or report.account == account
        for limit in report.limits
        if limit.status == STATUS_EXHAUSTED
    ]
    if not spent:
        return None
    report, limit = max(spent, key=lambda pair: pair[1].resets_at or 0.0)
    others = [other for other in _ordered(report.limits) if other.limit_key != limit.limit_key]
    return report.account, limit, others


__all__ = [
    "DANGER_AT",
    "KIND_BALANCE",
    "KIND_NONE",
    "KIND_RATE_LIMIT",
    "KIND_WINDOWED",
    "QuotaLimit",
    "QuotaReport",
    "QuotaStore",
    "STALE_AFTER_S",
    "WARNING_AT",
    "WRITE_EVERY_S",
    "account_for_token",
    "codex_limits",
    "default_store",
    "exhausted_limit",
    "exhausted_report",
    "header_limits",
    "limit_status",
    "observe_codex_frame",
    "observe_headers",
    "report_kind",
    "reset_epoch",
    "should_warn",
    "window_label",
]
