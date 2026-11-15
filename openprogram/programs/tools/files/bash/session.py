"""Command sessions shared by ``bash`` and ``write_stdin``, after Codex's unified exec.

A command runs under a managed-process supervisor. The call waits up to its
yield time; a command still running then keeps running in the background and
the reply carries its ``session_id``. ``write_stdin`` writes to that session,
interrupts it with ``\\x03`` or, with nothing to write, waits for more output.
At most 64 sessions run per conversation; starting another stops the least
recently used one outside the eight most recent.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time

from openprogram.host.processes import ProcessStore, control, scoped_records, start
from openprogram.host.processes.store import LIVE, TERMINAL

# Codex unified_exec constants.
MIN_YIELD_TIME_MS = 250
MAX_YIELD_TIME_MS = 30_000
MIN_EMPTY_YIELD_TIME_MS = 5_000
MAX_EMPTY_YIELD_TIME_MS = 300_000
DEFAULT_WRITE_YIELD_TIME_MS = 250
MAX_SESSIONS = 64
PROTECTED_RECENT = 8
OUTPUT_MAX_BYTES = 1024 * 1024
INTERRUPT = "\x03"
# Ours: a command waits 30 s before it moves to the background.
DEFAULT_YIELD_TIME_MS = 30_000

_POLL_SECONDS = 0.05


class HeadTailBuffer:
    """Keeps the first and last half of ``OUTPUT_MAX_BYTES``; the middle is counted, not kept."""

    def __init__(self, max_bytes: int = OUTPUT_MAX_BYTES):
        self.head_budget = max_bytes // 2
        self.tail_budget = max_bytes - self.head_budget
        self.head = bytearray()
        self.tail = bytearray()
        self.omitted = 0

    def push(self, chunk: bytes) -> None:
        room = self.head_budget - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        self.tail += chunk
        excess = len(self.tail) - self.tail_budget
        if excess > 0:
            del self.tail[:excess]
            self.omitted += excess

    def text(self) -> str:
        if not self.omitted:
            return bytes(self.head + self.tail).decode("utf-8", errors="replace")
        return (bytes(self.head).decode("utf-8", errors="replace")
                + f"\n... {self.omitted} bytes omitted ...\n"
                + bytes(self.tail).decode("utf-8", errors="replace"))


@dataclass
class Reply:
    record: dict
    output: str
    wall_time: float
    dropped: int = 0
    note: str = ""

    @property
    def running(self) -> bool:
        return self.record["status"] in LIVE or self.record["status"] == "unknown"

    def text(self) -> str:
        record = self.record
        lines = [f"Wall time: {self.wall_time:.4f} seconds"]
        if self.running:
            lines.append(f"Process running with session ID {record['id']}")
        elif record["status"] == "exited":
            lines.append(f"Process exited with code {record['exit_code']}")
        else:
            lines.append(f"Process {record['status']} (exit code {record['exit_code']})")
        if record.get("cwd"):
            lines.append("cwd=" + json.dumps(record["cwd"], ensure_ascii=False))
        if record.get("backend_id") not in (None, "local"):
            lines.append(f"backend={record['backend_id']}")
        if self.note:
            lines.append(self.note)
        if self.dropped:
            lines.append(f"... {self.dropped} earlier bytes were dropped before they were read ...")
        lines.append("Output:")
        return "\n".join(lines) + "\n" + self.output


def clamp_exec_yield(value) -> int:
    value = DEFAULT_YIELD_TIME_MS if value is None else int(value)
    return max(MIN_YIELD_TIME_MS, min(MAX_YIELD_TIME_MS, value))


def clamp_write_yield(value, chars: str) -> int:
    if chars:
        value = DEFAULT_WRITE_YIELD_TIME_MS if value is None else int(value)
        return max(MIN_YIELD_TIME_MS, min(MAX_YIELD_TIME_MS, value))
    value = MIN_EMPTY_YIELD_TIME_MS if value is None else int(value)
    return max(MIN_EMPTY_YIELD_TIME_MS, min(MAX_EMPTY_YIELD_TIME_MS, value))


def collect(store: ProcessStore, process_id: str, yield_ms: int, started: float, *,
            stop_on_cancel: bool = False) -> Reply:
    """Unread output until the command ends or the yield time passes.

    Cancelling the turn ends the wait. A command still in its first wait is
    stopped with it, as a foreground command would be; a session the agent
    already left in the background keeps running.
    """
    from openprogram.host.cancellation import CancelledError, check_cancelled

    buffer = HeadTailBuffer()
    dropped = 0
    note = ""
    deadline = started + yield_ms / 1000
    while True:
        try:
            check_cancelled()
        except CancelledError:
            if not stop_on_cancel:
                raise
            control(store, process_id, "stop")
            stop_on_cancel = False
            note = "Cancelled; the command was stopped"
            deadline = time.monotonic() + 5
        chunk, lost = store.take(process_id)
        buffer.push(chunk)
        dropped += lost
        record = store.get(process_id)
        if record["status"] in TERMINAL:
            # The supervisor appends the last output before the final status.
            chunk, lost = store.take(process_id)
            buffer.push(chunk)
            dropped += lost
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_SECONDS)
    fields = {"last_used": time.time()}
    if record["status"] in TERMINAL:
        fields["reported"] = True
    record = store.update(process_id, **fields)
    return Reply(record, buffer.text(), time.monotonic() - started, dropped, note)


def make_room(store: ProcessStore, session_id: str) -> str:
    """Stop the least recently used session when the conversation has 64 running."""
    if store.live_count(session_id) < MAX_SESSIONS:
        return ""
    running = [item for item in scoped_records(store, session_id) if item["status"] in LIVE]
    if len(running) < MAX_SESSIONS:
        return ""
    recent = sorted(running, key=lambda item: item.get("last_used") or item["started_at"], reverse=True)
    protected = {item["id"] for item in recent[:PROTECTED_RECENT]}
    victim = next(item for item in reversed(recent) if item["id"] not in protected)
    store.update(victim["id"], reported=True)
    control(store, victim["id"], "stop")
    return f"Stopped session {victim['id']} ({victim['command'][:80]}): at most {MAX_SESSIONS} commands run at once"


def run(command: str, *, cwd: str | None, run_command: str, tty: bool, yield_ms: int,
        shown_cwd: str | None = None, title: str | None = None) -> Reply:
    from openprogram.host.processes import current_owner

    session_id, _, _ = current_owner()
    store = ProcessStore()
    note = make_room(store, session_id)
    started = time.monotonic()
    record = start(command, cwd=cwd, store=store, tty=tty, run_command=run_command)
    if record["status"] in {"failed", "unknown"}:
        raise RuntimeError(f"command launch {record['status']}")
    if shown_cwd != record.get("cwd"):
        store.update(record["id"], cwd=shown_cwd)
    if isinstance(title, str) and title.strip():
        store.update(record["id"], title=title.strip()[:120])
    reply = collect(store, record["id"], yield_ms, started, stop_on_cancel=True)
    if reply.running:
        reply.record = store.update(record["id"], background=True)
    else:
        # Its whole output is in this reply; only background sessions are kept.
        store.remove(record["id"])
    reply.note = "\n".join(filter(None, (note, reply.note)))
    return reply


def find(store: ProcessStore, process_id: str) -> dict | None:
    from openprogram.host.processes import current_owner

    session_id, _, _ = current_owner()
    record = store.get(process_id)
    if record is not None and record["session_id"] == session_id:
        return record
    # A sub-agent's command belongs to this conversation through its execution.
    return next((item for item in scoped_records(store, session_id) if item["id"] == process_id), None)


def write(process_id: str, chars: str, yield_ms: int | None) -> Reply:
    store = ProcessStore()
    record = find(store, process_id)
    if record is None:
        raise LookupError(f"no command session {process_id} in this conversation")
    started = time.monotonic()
    if chars and record["status"] in LIVE:
        if record.get("tty"):
            control(store, process_id, "write", chars)
            # Give the command a moment to react before reading.
            time.sleep(0.1)
        elif chars == INTERRUPT:
            control(store, process_id, "interrupt")
        else:
            raise ValueError("stdin is closed for this session: it was started without tty. "
                             "Send \\x03 to interrupt it, or rerun the command with tty=true to type into it.")
    return collect(store, process_id, clamp_write_yield(yield_ms, chars), started)


def sandbox_denial(reply: Reply) -> str:
    """The sandbox explanation when a sandboxed command most likely failed on it."""
    record = reply.record
    if not record.get("sandboxed") or record["status"] not in TERMINAL:
        return ""
    from openprogram.host import sandbox

    if not sandbox.is_sandbox_denial(record["exit_code"] or 0, reply.output, "", sandboxed=True):
        return ""
    if sandbox.is_network_denial(record["exit_code"] or 0, reply.output, "", sandboxed=True):
        return sandbox.network_denial_text()
    hit = sandbox.match_deny_read(f"{record['command']}\n{reply.output}")
    return sandbox.named_denial_text(*(hit or (None, None)))
