"""cron-worker — foreground loop that fires cron entries.

Reads the schedule file produced by the ``cron`` tool every minute. For
each entry whose 5-field expression matches the current minute, spawns a
detached subprocess running the entry's ``prompt`` via
``openprogram deep-work``. Per-entry stdout/stderr lands in
``<schedule_dir>/logs/<entry_id>-<timestamp>.log``. Last-fired minute per
entry is persisted to ``<schedule_dir>/worker-state.json`` so a worker
restart within the same minute doesn't re-fire already-fired entries.

Usage:

    openprogram cron-worker            # run forever (foreground)
    openprogram cron-worker --once     # evaluate one tick and exit
    openprogram cron-worker --list     # show whether entries match now

Design notes:

- Foreground loop only. No double-forking, no service manager wrapping.
  Run it under tmux / nohup / launchd yourself if you want it to survive
  logout or reboot.
- Cron matching is hand-rolled (no ``croniter`` dependency). Supports
  the common Vixie syntax: ``*``, ``N``, ``N-M``, ``*/S``, ``N-M/S``,
  comma lists, and the ``@yearly/@monthly/@weekly/@daily/@hourly``
  macros. ``@reboot`` fires once when the worker starts.
- When day-of-month and day-of-week are both restricted (not ``*``),
  they combine with OR, matching Vixie/ISC cron semantics.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from .cron import _load, _resolve_path


_MACRO_EXPANSIONS = {
    "@yearly":   "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly":  "0 0 1 * *",
    "@weekly":   "0 0 * * 0",
    "@daily":    "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly":   "0 * * * *",
}


def _expand(expr: str) -> str | None:
    """Expand a macro to 5 fields. Returns None for ``@reboot``."""
    e = expr.strip().lower()
    if e == "@reboot":
        return None
    return _MACRO_EXPANSIONS.get(e, expr)


def _parse_field(field: str, low: int, high: int) -> set[int]:
    """Parse one cron field into the set of ints it matches.

    Supports ``*``, ``N``, ``N-M``, ``*/S``, ``N-M/S``, and comma lists
    of these. ``low`` and ``high`` are inclusive bounds for the field.
    """
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        base = part
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"step must be positive: {part!r}")
        if base in ("*", ""):
            start, end = low, high
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            v = int(base)
            start = end = v
        for n in range(start, end + 1, step):
            if low <= n <= high:
                out.add(n)
    return out


def match(expr: str, now: dt.datetime) -> bool:
    """Return True if ``now`` (minute precision) matches ``expr``.

    Returns False for ``@reboot`` — that's handled separately by the
    worker's first tick, not by clock-matching.
    """
    expanded = _expand(expr)
    if expanded is None:
        return False
    parts = expanded.split()
    if len(parts) != 5:
        return False
    m_f, h_f, dom_f, mo_f, dow_f = parts
    try:
        minutes = _parse_field(m_f,   0, 59)
        hours   = _parse_field(h_f,   0, 23)
        doms    = _parse_field(dom_f, 1, 31)
        months  = _parse_field(mo_f,  1, 12)
        dows    = _parse_field(dow_f, 0, 7)  # allow 7 as Sunday
    except (ValueError, IndexError):
        return False

    if 7 in dows:
        dows = (dows - {7}) | {0}

    # Python: Mon=0..Sun=6. Cron: Sun=0..Sat=6. Convert.
    cron_dow = (now.weekday() + 1) % 7

    dom_wild = dom_f.strip() == "*"
    dow_wild = dow_f.strip() == "*"
    dom_ok = now.day in doms
    dow_ok = cron_dow in dows
    if not dom_wild and not dow_wild:
        day_ok = dom_ok or dow_ok  # Vixie cron OR semantics
    else:
        day_ok = (dom_wild or dom_ok) and (dow_wild or dow_ok)

    return (
        now.minute in minutes
        and now.hour in hours
        and now.month in months
        and day_ok
    )


def _schedule_dir() -> str:
    return os.path.dirname(_resolve_path()) or "."


def _state_path() -> str:
    return os.path.join(_schedule_dir(), "worker-state.json")


def _logs_dir() -> str:
    return os.path.join(_schedule_dir(), "logs")


def _load_state() -> dict[str, str]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, str]) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _spawn(entry: dict[str, Any], log_dir: str) -> subprocess.Popen[bytes] | None:
    """Fire an entry by launching ``openprogram deep-work`` detached."""
    prompt = (entry.get("prompt") or "").strip()
    if not prompt:
        return None
    os.makedirs(log_dir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = os.path.join(log_dir, f"{entry.get('id','noid')}-{ts}.log")
    log_fh = open(log_path, "w", buffering=1, encoding="utf-8")
    log_fh.write(f"# cron fire — entry {entry.get('id')} @ {ts}\n")
    log_fh.write(f"# expr: {entry.get('cron')}\n")
    log_fh.write(f"# prompt: {prompt}\n\n")
    log_fh.flush()
    cmd = [sys.executable, "-m", "openprogram.cli", "deep-work", prompt, "--no-interactive"]
    return subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach from worker's process group
    )


def _tick(state: dict[str, str], *, reboot: bool = False) -> int:
    """Evaluate schedule once at the current wall-clock minute.

    Returns the number of entries fired. When ``reboot=True`` only
    ``@reboot`` entries are considered; normal clock matching is skipped.
    """
    entries = _load(_resolve_path())
    if not entries:
        return 0
    now = dt.datetime.now().replace(second=0, microsecond=0)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    log_dir = _logs_dir()
    fired = 0
    for entry in entries:
        eid = entry.get("id")
        expr = (entry.get("cron") or "").strip()
        if not eid or not expr:
            continue
        if reboot:
            should_fire = expr.lower() == "@reboot"
        else:
            should_fire = match(expr, now) and state.get(eid) != stamp
        if not should_fire:
            continue
        proc = _spawn(entry, log_dir)
        if proc is None:
            continue
        if not reboot:
            state[eid] = stamp
        fired += 1
        print(f"[{stamp}] fire {eid}  pid={proc.pid}  ({expr}) {(entry.get('prompt') or '')[:60]}")
    return fired


def run_forever() -> None:
    """Run the worker loop until SIGINT/SIGTERM."""
    stop = {"flag": False}

    def _on_signal(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print(f"cron-worker started. schedule={_resolve_path()}  logs={_logs_dir()}")
    print("press Ctrl+C to stop.")

    state = _load_state()
    if _tick(state, reboot=True):
        _save_state(state)

    while not stop["flag"]:
        now = dt.datetime.now()
        remain = 60 - now.second - now.microsecond / 1_000_000
        # Break sleep into 1s chunks so signals are responsive
        while remain > 0 and not stop["flag"]:
            chunk = min(1.0, remain)
            time.sleep(chunk)
            remain -= chunk
        if stop["flag"]:
            break
        if _tick(state):
            _save_state(state)

    print("\ncron-worker stopped.")


def run_once() -> int:
    """One-shot tick. Useful for testing / external schedulers."""
    state = _load_state()
    fired = _tick(state)
    _save_state(state)
    return fired


def list_next() -> None:
    """Print each entry and whether it matches the current minute."""
    entries = _load(_resolve_path())
    if not entries:
        print("(no cron entries)")
        return
    now = dt.datetime.now().replace(second=0, microsecond=0)
    print(f"Now: {now.strftime('%Y-%m-%d %H:%M')}  (testing match for this minute)")
    for e in entries:
        expr = (e.get("cron") or "").strip()
        matches = match(expr, now)
        tag = "MATCH" if matches else "----"
        prompt = (e.get("prompt") or "")[:60]
        print(f"  {e.get('id','?')}  {expr:20s}  {tag}  → {prompt}")


__all__ = ["match", "run_forever", "run_once", "list_next"]
