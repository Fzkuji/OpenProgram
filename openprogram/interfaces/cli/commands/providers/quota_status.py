"""``openprogram providers status`` with no provider: every connected provider on one screen.

The rows read the payload of ``GET /api/providers/status``
(:func:`openprogram.models.usage.provider_status.build_status`): health,
quota (a 20-cell bar per window, as Codex's ``/status`` draws it), 30-day
spend, tokens, cache share and default model. A company connected in more
than one way (``family`` in the payload) is one line with its best state,
its ways indented under it. ``--json`` prints the payload itself;
``--refresh`` reads the quota endpoints now.

The table fits the terminal: a bar gives up cells (down to
:data:`MIN_BAR_CELLS`) and then its reset time moves to a line of its own.
Output that goes to a pipe without ``COLUMNS`` is not wrapped.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from typing import Any, Optional

BAR_CELLS = 20
MIN_BAR_CELLS = 5

_DOT = {
    "ready": "●", "rate_limited": "◐", "signed_out": "○", "no_credit": "✕",
    "invalid_key": "✕", "unknown": "·", "not_configured": "○", "not_running": "○",
}
_STATE_WORDS = {
    "ready": "ready", "rate_limited": "rate limited", "signed_out": "signed out",
    "no_credit": "no credit", "invalid_key": "invalid key", "unknown": "unchecked",
    "not_configured": "not set up", "not_running": "not running",
}


def bar(left_fraction: Optional[float], cells: int = BAR_CELLS) -> str:
    """``[████████░░░░…]`` filled by the share left."""
    if left_fraction is None:
        return "[" + "·" * cells + "]"
    filled = round(max(0.0, min(1.0, left_fraction)) * cells)
    return "[" + "█" * filled + "░" * (cells - filled) + "]"


def _clock(ts: Optional[float], now: float) -> str:
    """A coming time: ``HH:MM`` within 20 hours, else with its date; nothing once it has passed."""
    if not ts or ts <= now:
        return ""
    local = time.localtime(ts)
    if ts - now < 20 * 3600:
        return time.strftime("%H:%M", local)
    return time.strftime("%b %d %H:%M", local)


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    seconds = max(0, int(now - ts))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _tokens(n: int) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= size:
            return f"{n / size:.1f}{suffix}"
    return str(n)


def _money(amount: Optional[float], unit: str = "usd") -> str:
    if amount is None:
        return "?"
    symbol = {"usd": "$", "cny": "¥"}.get(unit)
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {unit}"


def _bar_line(label: str, left: Optional[float], figure: str, reset: str,
              width: Optional[int]) -> list[str]:
    """``label [bar] figure (resets …)`` within ``width`` columns: fewer cells, then the reset below."""
    tail = f" (resets {reset})" if reset else ""
    fixed = len(f"{label:<8} [] {figure}")
    if width is None or fixed + BAR_CELLS + len(tail) <= width:
        return [f"{label:<8} {bar(left)} {figure}{tail}"]
    if fixed + MIN_BAR_CELLS + len(tail) <= width:
        return [f"{label:<8} {bar(left, width - fixed - len(tail))} {figure}{tail}"]
    cells = max(MIN_BAR_CELLS, min(BAR_CELLS, width - fixed))
    line = f"{label:<8} {bar(left, cells)} {figure}"
    if not reset:
        return [line]
    below = f"resets {reset}"
    return [line, f"{'':<9}{below}" if 9 + len(below) <= width else below]


def quota_lines(quota: dict, now: float, width: Optional[int] = None) -> list[str]:
    """The quota block of one provider: one line per window or figure, within ``width`` columns."""
    limits = quota.get("limits") or []
    kind = quota.get("kind")
    failed = (f"last quota read failed: {quota['read_error']} · {_ago(quota.get('read_at'), now)}"
              if quota.get("read_error") else None)
    if not limits:
        return ["no quota data"] + ([failed] if failed else [])
    lines: list[str] = []
    for limit in limits:
        reset = _clock(limit.get("resets_at"), now)
        tail = f" (resets {reset})" if reset else ""
        if limit["unit"] == "percent":
            left = limit.get("left_fraction")
            lines += _bar_line(limit["label"], left, f"{'?' if left is None else round(left * 100)}% left",
                               reset, width)
        elif limit["unit"] in ("usd", "cny"):
            if limit.get("remaining") is not None:
                of = f" of {_money(limit['limit'], limit['unit'])}" if limit.get("limit") is not None else ""
                lines.append(f"{limit['label']}: {_money(limit['remaining'], limit['unit'])} left{of}{tail}")
            else:
                lines.append(f"{limit['label']}: no limit · {_money(limit.get('used'), limit['unit'])} used")
        elif limit["unit"] == "credits":
            amount = limit.get("remaining")
            lines.append(f"Credits: {'unlimited' if amount is None else f'{amount:g}'}")
        else:
            left = limit.get("left_fraction")
            figure = (f"{int(limit['remaining']):,}/{int(limit['limit']):,}"
                      if limit.get("remaining") is not None and limit.get("limit") is not None else "?")
            lines += _bar_line(limit["label"], left, figure, reset, width)
    if kind == "rate_limit":
        lines.append("no balance data")
    if quota.get("stale"):
        lines.append(f"may be stale · updated {_ago(quota.get('fetched_at'), now)}")
    if failed:
        lines.append(failed)
    return lines


def spend_text(spend: dict) -> str:
    if spend["included"]:
        money = "Included"
    elif spend["cost_usd"] is None or (spend["calls"] and spend["unpriced_calls"] == spend["calls"]):
        money = "unpriced"
    else:
        money = _money(spend["cost_usd"])
        if spend["unpriced_calls"]:
            money += f" · {spend['unpriced_calls']} calls unpriced"
    cache = "" if spend["cache_share"] is None else f" · cache {round(spend['cache_share'] * 100)}%"
    return f"30d {money} · {_tokens(spend['total_tokens'])} tok{cache}"


#: Best first: a company's line shows the state of its best way.
_BEST = ("ready", "unknown", "rate_limited", "signed_out", "no_credit", "invalid_key",
         "not_configured", "not_running")


def _words(health: dict, now: float) -> str:
    state = health["state"]
    words = _STATE_WORDS.get(state, state)
    until = _clock(health.get("until"), now) if state == "rate_limited" else ""
    return words + (f" · until {until}" if until else "")


def companies(providers: list[dict]) -> list[list[dict]]:
    """The providers grouped by company (``family``), each group where its first way is listed."""
    groups: dict[str, list[dict]] = {}
    for provider in providers:
        groups.setdefault(provider.get("family") or provider["id"], []).append(provider)
    return list(groups.values())


def render(payload: dict, *, now: Optional[float] = None, columns: Optional[int] = None) -> str:
    """The one-screen table of every connected provider, ``columns`` wide when given.

    A company with two or more ways connected is one line (its name and its
    best way's state), with each way indented under it by its title."""
    now = time.time() if now is None else now
    providers = payload.get("providers") or []
    if not providers:
        return ("No connected providers.\n"
                "Add one: openprogram providers login <provider>   (list them: openprogram providers available)")

    def ways(group: list[dict]) -> list[tuple[int, str, dict]]:
        """(indent, name, provider) of each line under a company, or of a lone provider."""
        if len(group) == 1:
            return [(0, group[0]["label"], group[0])]
        return [(2, (p.get("way_label") or {}).get("title") or p["label"], p) for p in group]

    groups = companies(providers)
    width = max([indent + len(name) for g in groups for indent, name, _p in ways(g)]
                + [len(g[0].get("family_label") or g[0]["label"]) for g in groups if len(g) > 1])
    out: list[str] = []
    for group in groups:
        if len(group) > 1:
            best = min(group, key=lambda p: _BEST.index(p["health"]["state"])
                       if p["health"]["state"] in _BEST else len(_BEST))
            name = group[0].get("family_label") or group[0]["label"]
            out.append(f"{_DOT.get(best['health']['state'], '·')} {name:<{width}}  {_words(best['health'], now)}")
        for indent, name, provider in ways(group):
            health = provider["health"]
            lead = " " * indent + _DOT.get(health["state"], "·")
            head = f"{lead} {name:<{width - indent}}  {_words(health, now)}"
            model = provider.get("default_model")
            out.append(head + (f"  ·  {model}" if model else ""))
            pad = " " * (width + 4)
            for line in quota_lines(provider["quota"], now, columns - len(pad) if columns else None):
                out.append(pad + line)
            out.append(pad + spend_text(provider["spend"]))
    checked = [p["quota"].get("fetched_at") for p in providers if p["quota"].get("fetched_at")]
    out.append("")
    out.append(f"Updated {_ago(payload.get('generated_at'), now)} · quota checked "
               f"{_ago(max(checked) if checked else None, now)} · --refresh · --json")
    return "\n".join(out)


def worker_status(refresh: bool) -> Optional[dict]:
    """The payload as the running Worker of this profile answers it, or ``None``.

    The Worker holds what only it knows, such as each key's use count and
    last use (kept in memory, ``auth/pool.py``), so the CLI asks it first
    and prints what the page shows: ``GET /api/providers/status``, or with
    ``--refresh`` ``POST /api/providers/status/refresh``. ``None`` when no
    Worker answers for this profile."""
    from openprogram.interfaces.cli import worker_client
    try:
        from openprogram.host.security.backend_endpoint import resolve_backend_endpoint
        endpoint = resolve_backend_endpoint()
    except Exception:  # noqa: BLE001 -- no Worker of this profile: build it here
        return None
    if refresh:
        status, payload = worker_client.request(endpoint, "POST", "/api/providers/status/refresh", {},
                                                exit_if_unreachable=False)
    else:
        status, payload = worker_client.request(endpoint, "GET", "/api/providers/status",
                                                exit_if_unreachable=False)
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        return None  # an older Worker or a failed read: build it here
    return payload


def cmd_overview(refresh: bool, as_json: bool) -> int:
    from openprogram.models.usage.provider_status import build_status
    payload = worker_status(refresh)
    try:
        if payload is None:
            payload = build_status(refresh="force" if refresh else "stale")
    except Exception as exc:  # noqa: BLE001 -- one line instead of a traceback
        print(f"Provider status could not be read: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(payload, columns=shutil.get_terminal_size((0, 0)).columns or None))
    return 0


__all__ = ["bar", "cmd_overview", "companies", "quota_lines", "render", "spend_text"]
