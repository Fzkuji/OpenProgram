"""Scheduler worker for one-time, recurring, and monitor tasks.

Reads the task file produced by the ``scheduler`` tool every minute. For
each due entry, spawns a
detached subprocess running the entry's ``prompt`` via
``process_user_turn``. Per-entry stdout/stderr lands in
``<schedule_dir>/logs/<entry_id>-<timestamp>.log``. Last-fired minute per
entry is persisted to ``<schedule_dir>/worker-state.json`` so a worker
restart within the same minute doesn't re-fire already-fired entries.

Usage:

    openprogram scheduler-worker            # run forever (foreground)
    openprogram scheduler-worker --once     # evaluate one tick and exit
    openprogram scheduler-worker --list     # show whether entries match now

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

from contextlib import redirect_stderr, redirect_stdout
import datetime as dt
import json
import multiprocessing
import os
import signal
import subprocess
import tempfile
import time
import threading
import uuid
from typing import Any

from openprogram.backend.local import _invocation
from openprogram import sandbox as _sandbox

from .cron import _load, _resolve_path, _store_lock, _verify_execution_spec


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


def valid_cron(expr: str) -> bool:
    """Return whether the worker can parse and execute an expression."""
    expanded = _expand(expr)
    if expanded is None:
        return expr.strip().lower() == "@reboot"
    parts = expanded.split()
    if len(parts) != 5:
        return False
    try:
        values = (
            _parse_field(parts[0], 0, 59),
            _parse_field(parts[1], 0, 23),
            _parse_field(parts[2], 1, 31),
            _parse_field(parts[3], 1, 12),
            _parse_field(parts[4], 0, 7),
        )
    except (ValueError, IndexError):
        return False
    return all(values)


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


def _load_state() -> dict[str, Any]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="worker-state-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run_prompt_job(spec: dict[str, Any], log_path: str) -> None:
    """Child-process entry for one non-interactive agent prompt."""
    with open(log_path, "a", buffering=1, encoding="utf-8") as log_fh:
        with redirect_stdout(log_fh), redirect_stderr(log_fh):
            print(f"# policy_hash={spec['policy_hash']}")
            try:
                verified, policy, error = _verify_execution_spec(spec)
                if error or verified is None or policy is None:
                    print(f"# refused: {error or 'invalid execution spec'}")
                    return
                spec = verified
                # The frozen policy was verified above but never applied,
                # so in-process tools (read/grep/glob/list) still saw the
                # host. Pin it the way a spawned subprocess is pinned
                # (process_runner._child_entry) before any tool exists —
                # a later config edit cannot widen this job.
                _sandbox.install_policy_snapshot({
                    "enabled": True,
                    "policy": _sandbox.policy_to_dict(policy),
                })
                job_authority = spec["job_authority"]
                prompt = spec["prompt"]
                memory_refs = spec.get("memory_refs") or []
                if memory_refs:
                    from openprogram.memory.references import render_context

                    context = render_context(memory_refs)
                    if context:
                        prompt = f"{prompt}\n\n{context}"
                from openprogram.agent.dispatcher import TurnRequest, process_user_turn
                from openprogram.programs.permission_rule import load_merged_rules

                result = process_user_turn(TurnRequest(
                    session_id=spec["session_id"],
                    user_text=prompt,
                    agent_id=spec["agent_id"],
                    source="scheduler",
                    permission_mode=spec["permission_mode"],
                    advance_head=False,
                    speaker_kind="runtime",
                    speaker_id="runtime/scheduler",
                    speaker_display="scheduler",
                    principal_id=job_authority["principal_id"],
                    authority_tier=job_authority["authority_tier"],
                    interaction="non-interactive",
                    permission_rules=load_merged_rules(spec["session_id"]),
                ))
                if result.final_text:
                    print(result.final_text)
                if result.failed:
                    print(f"# failed: {result.error or 'unknown turn failure'}")
            except Exception as exc:
                print(f"# failed: {type(exc).__name__}: {exc}")


def _spawn(entry: dict[str, Any], log_dir: str) -> Any | None:
    """Fire an entry from its verified immutable execution spec."""
    prompt = (entry.get("prompt") or "").strip()
    command = (entry.get("command") or "").strip()
    if not prompt and not command and not entry.get("execution"):
        return None
    os.makedirs(log_dir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = os.path.join(log_dir, f"{entry.get('id','noid')}-{ts}.log")
    log_fh = open(log_path, "w", buffering=1, encoding="utf-8")
    try:
        log_fh.write(f"# cron fire — entry {entry.get('id')} @ {ts}\n")
        log_fh.write(f"# expr: {entry.get('cron')}\n")
        spec, policy, error = _verify_execution_spec(entry.get("execution"))
        if error:
            log_fh.write(f"# refused: {error}\n")
            return None
        assert spec is not None and policy is not None
        kind = spec["kind"]
        body = spec[kind]
        log_fh.write(f"# {kind}: {body}\n")
        log_fh.write(f"# cwd: {spec['cwd']}\n")
        log_fh.write(f"# policy_hash={spec['policy_hash']}\n\n")
        if kind == "command":
            log_fh.flush()
            try:
                args, use_shell, env, _sandboxed = _invocation(
                    body,
                    cwd=spec["cwd"],
                    policy=policy,
                    force_sandbox=True,
                )
            except _sandbox.SandboxUnavailable as exc:
                log_fh.write(f"# refused: {exc}\n")
                return None
            proc: Any = subprocess.Popen(
                args,
                shell=use_shell,
                cwd=spec["cwd"],
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            log_fh.flush()
            proc = multiprocessing.Process(
                target=_run_prompt_job,
                args=(spec, log_path),
                name=f"openprogram-cron-{entry.get('id', 'noid')}",
            )
            proc.start()
    finally:
        # Popen dup'd our fd into the child's stdout; we don't need the
        # parent-side handle anymore. Leaving it open leaks an fd per
        # fire and prevents log rotation on Linux until the parent exits.
        log_fh.close()
    return proc


def _last_stamp(state: dict[str, Any], entry_id: str) -> str | None:
    value = state.get(entry_id)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        stamp = value.get("last_fired_minute")
        return stamp if isinstance(stamp, str) else None
    return None


def _entry_due(
    entry: dict[str, Any], now: dt.datetime, state: dict[str, Any], *, reboot: bool,
) -> bool:
    if entry.get("enabled", True) is False:
        return False
    entry_id = str(entry.get("id") or "")
    if not entry_id:
        return False
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    task_type = entry.get("type")
    if task_type == "once":
        if reboot or _last_stamp(state, entry_id) is not None:
            return False
        try:
            due = dt.datetime.fromisoformat(
                str(entry.get("run_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            return False
        if due.tzinfo is None:
            return False
        current = now if now.tzinfo is not None else now.astimezone()
        return current.astimezone(dt.timezone.utc) >= due.astimezone(dt.timezone.utc)
    expr = str(entry.get("cron") or "").strip()
    if not expr:
        return False
    if reboot:
        return expr.lower() == "@reboot"
    return match(expr, now) and _last_stamp(state, entry_id) != stamp


def _claim_entry(
    entry: dict[str, Any], now: dt.datetime, *, reboot: bool,
) -> tuple[str, bool, Any, dict[str, Any]] | None:
    """Persist an exclusive execution claim before starting a task."""
    entry_id = str(entry.get("id") or "")
    path = _state_path()
    with _store_lock(path):
        shared = _load_state()
        if not _entry_due(entry, now, shared, reboot=reboot):
            return None
        had_previous = entry_id in shared
        previous = shared.get(entry_id)
        token = uuid.uuid4().hex
        claim = {
            "last_fired_minute": now.strftime("%Y-%m-%dT%H:%M"),
            "last_started_at": now.isoformat(),
            "claim_token": token,
            "status": "starting",
        }
        shared[entry_id] = claim
        _save_state(shared)
    return token, had_previous, previous, claim


def _release_claim(
    entry_id: str, token: str, *, had_previous: bool, previous: Any,
) -> bool:
    path = _state_path()
    with _store_lock(path):
        shared = _load_state()
        current = shared.get(entry_id)
        if not isinstance(current, dict) or current.get("claim_token") != token:
            return False
        if had_previous:
            shared[entry_id] = previous
        else:
            shared.pop(entry_id, None)
        _save_state(shared)
    return True


def _complete_claim(entry_id: str, token: str) -> dict[str, Any] | None:
    path = _state_path()
    with _store_lock(path):
        shared = _load_state()
        current = shared.get(entry_id)
        if not isinstance(current, dict) or current.get("claim_token") != token:
            return None
        completed = {
            key: value for key, value in current.items()
            if key not in {"claim_token", "status"}
        }
        shared[entry_id] = completed
        _save_state(shared)
    return completed


def _tick(
    state: dict[str, Any],
    *,
    reboot: bool = False,
    now: dt.datetime | None = None,
) -> int:
    """Evaluate schedule once at the current wall-clock minute.

    Returns the number of entries fired. When ``reboot=True`` only
    ``@reboot`` entries are considered; normal clock matching is skipped.
    """
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    entries = _load(_resolve_path())
    if not entries:
        return 0
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    log_dir = _logs_dir()
    fired = 0
    for entry in entries:
        eid = entry.get("id")
        if not eid:
            continue
        should_fire = _entry_due(entry, now, state, reboot=reboot)
        if not should_fire:
            continue
        try:
            claimed = _claim_entry(entry, now, reboot=reboot)
        except Exception as exc:  # noqa: BLE001 — state failure is task-local
            print(f"[{stamp}] claim failed {eid}: {type(exc).__name__}: {exc}")
            continue
        if claimed is None:
            continue
        token, had_previous, previous, claim = claimed
        state[eid] = claim
        proc = None
        try:
            proc = _spawn(entry, log_dir)
        except Exception as exc:  # noqa: BLE001 — one task must not stop Scheduler
            print(f"[{stamp}] failed {eid}: {type(exc).__name__}: {exc}")
        if proc is None:
            try:
                released = _release_claim(
                    str(eid), token,
                    had_previous=had_previous,
                    previous=previous,
                )
            except Exception as exc:  # noqa: BLE001 — claim blocks duplicates
                print(
                    f"[{stamp}] claim release failed {eid}: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                if released:
                    if had_previous:
                        state[eid] = previous
                    else:
                        state.pop(eid, None)
            continue
        try:
            completed = _complete_claim(str(eid), token)
        except Exception as exc:  # noqa: BLE001 — claim still blocks duplicates
            print(
                f"[{stamp}] claim completion failed {eid}: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            if completed is not None:
                state[eid] = completed
        fired += 1
        execution = entry.get("execution") or {}
        spec_kind = execution.get("kind")
        body = execution.get(spec_kind) or ""
        kind = "$" if spec_kind == "command" else ">"
        schedule = entry.get("run_at") or entry.get("cron") or ""
        print(f"[{stamp}] fire {eid}  pid={proc.pid}  ({schedule}) {kind} {body[:60]}")
    return fired


def run_forever(stop_event: threading.Event | None = None) -> None:
    """Run the worker loop until SIGINT/SIGTERM."""
    stop = {"flag": False}

    def _on_signal(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    if stop_event is None:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    print(f"scheduler-worker started. schedule={_resolve_path()}  logs={_logs_dir()}")
    print("press Ctrl+C to stop.")

    state = _load_state()
    _tick(state, reboot=True)

    while not stop["flag"] and not (stop_event and stop_event.is_set()):
        now = dt.datetime.now()
        remain = 60 - now.second - now.microsecond / 1_000_000
        # Break sleep into 1s chunks so signals are responsive
        while remain > 0 and not stop["flag"] and not (stop_event and stop_event.is_set()):
            chunk = min(1.0, remain)
            time.sleep(chunk)
            remain -= chunk
        if stop["flag"] or (stop_event and stop_event.is_set()):
            break
        _tick(state)

    print("\nscheduler-worker stopped.")


def run_once() -> int:
    """One-shot tick. Useful for testing / external schedulers."""
    state = _load_state()
    fired = _tick(state)
    return fired


def start_in_worker() -> tuple[threading.Event, threading.Thread]:
    """Start Scheduler in the persistent OpenProgram worker process."""
    try:
        from openprogram import memory

        if memory.is_enabled():
            from openprogram.agent.authority import local_owner_authority
            from openprogram.memory import store
            from openprogram.scheduler.migration import migrate_legacy_commitments

            migrate_legacy_commitments(
                memory_root=store.ensure(),
                cwd=os.getcwd(),
                authority=local_owner_authority(),
            )
    except Exception as exc:  # noqa: BLE001 — migration must not stop worker startup
        print(f"[scheduler] legacy migration skipped: {type(exc).__name__}: {exc}")
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        args=(stop,),
        daemon=True,
        name="scheduler-worker",
    )
    thread.start()
    return stop, thread


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
        body = (e.get("prompt") or e.get("command") or "")[:60]
        kind = "$" if e.get("command") else ">"
        print(f"  {e.get('id','?')}  {expr:20s}  {tag}  {kind} {body}")


__all__ = [
    "valid_cron", "match", "run_forever", "run_once", "list_next",
    "start_in_worker",
]
