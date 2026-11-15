"""Durable managed-process records, bounded output and supervisor commands."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_LINES = 2000
MAX_INPUT_BYTES = 64 * 1024
LIVE = frozenset({"starting", "running", "stopping"})
ACTIVE = LIVE | {"unknown"}
TERMINAL = frozenset({"exited", "stopped", "failed"})
COMMAND_KINDS = frozenset({"stop", "write", "interrupt"})
# A finished record, with its output, is kept this long after it ends; one
# whose supervisor went silent, this long after its last heartbeat.
RETENTION_SECONDS = 24 * 3600
PUBLIC_FIELDS = (
    "id", "session_id", "execution_id", "tool_call_id", "command", "cwd",
    "status", "started_at", "ended_at", "pid", "exit_code", "backend_id", "truncated",
    "tty", "background", "title",
)


def process_identity(pid: int | None) -> str | None:
    """Read birth identity only; recorded PIDs are never used to send signals."""
    if not pid:
        return None
    if sys.platform.startswith("linux"):
        # Read /proc so an image without ps still runs commands, in the format
        # `ps -o lstart=` prints, so identities recorded by ps keep matching.
        return _proc_lstart(pid)
    if os.name == "posix":
        value = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                               capture_output=True, text=True, timeout=3)
        return value.stdout.strip() or None
    # Windows process handles are owned exclusively by the live supervisor;
    # portable inspection relies on its durable heartbeat, never PID reuse.
    return None



_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _proc_lstart(pid: int, proc_root: str = "/proc") -> str | None:
    """A process's start time as procps ``ps -o lstart=`` prints it.

    procps takes the boot time from ``btime`` in ``/proc/stat``, adds the
    process's ``starttime`` clock ticks divided down to whole seconds, and
    prints the local time with ``ctime`` (C-locale names, day padded with a
    space).
    """
    try:
        # comm may contain spaces and parentheses; fields begin after its last ')'.
        fields = Path(proc_root, str(pid), "stat").read_text(encoding="utf-8").rsplit(")", 1)[-1].split()
        ticks = int(fields[19])
        boot = next(int(line.split()[1]) for line in Path(proc_root, "stat").read_text(encoding="utf-8").splitlines()
                    if line.startswith("btime "))
        when = time.localtime(boot + ticks // os.sysconf("SC_CLK_TCK"))
    except (OSError, IndexError, ValueError, StopIteration, UnicodeError):
        return None
    return (f"{_DAYS[when.tm_wday]} {_MONTHS[when.tm_mon - 1]} {when.tm_mday:2d} "
            f"{when.tm_hour:02d}:{when.tm_min:02d}:{when.tm_sec:02d} {when.tm_year}")

class ProcessStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from openprogram.state.paths import get_state_dir
            path = get_state_dir() / "processes.db"
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS processes (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    execution_id TEXT, record TEXT NOT NULL, output BLOB NOT NULL DEFAULT X'',
                    output_start INTEGER NOT NULL DEFAULT 0, poll_offset INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS processes_session ON processes(session_id);
                CREATE INDEX IF NOT EXISTS processes_execution ON processes(execution_id);
                CREATE TABLE IF NOT EXISTS process_commands (
                    id TEXT PRIMARY KEY, process_id TEXT NOT NULL, kind TEXT NOT NULL,
                    input TEXT, status TEXT NOT NULL, error TEXT, created_at REAL NOT NULL
                );
            """)
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, *, session_id, execution_id, tool_call_id, command, cwd, backend_id,
               tty=False, sandboxed=False):
        # ``background``: a command call returned while this was still running.
        # ``reported``: the agent has seen its exit, so no exit notice is due.
        record = dict(id=uuid.uuid4().hex, session_id=session_id, execution_id=execution_id,
                      tool_call_id=tool_call_id, command=command, cwd=cwd, backend_id=backend_id,
                      status="starting", started_at=time.time(), ended_at=None, pid=None,
                      exit_code=None, truncated=False, supervisor_pid=None,
                      supervisor_identity=None, pid_identity=None, heartbeat=time.time(),
                      tty=bool(tty), sandboxed=bool(sandboxed), background=False,
                      reported=False, last_used=time.time())
        with self.connect() as db:
            db.execute("INSERT INTO processes(id,session_id,execution_id,record) VALUES(?,?,?,?)",
                       (record["id"], session_id, execution_id, json.dumps(record)))
        return record

    def get(self, process_id, *, reconcile=True):
        with self.connect() as db:
            row = db.execute("SELECT record FROM processes WHERE id=?", (process_id,)).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        if reconcile and record["status"] in LIVE and time.time() - record["heartbeat"] > 15:
            expected = record.get("supervisor_identity")
            if os.name == "posix" and expected and process_identity(record.get("supervisor_pid")) == expected:
                return record
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT record FROM processes WHERE id=?", (process_id,)).fetchone()
                if row:
                    current = json.loads(row[0])
                    if current["status"] in LIVE and current["heartbeat"] == record["heartbeat"]:
                        current.update(status="unknown", ended_at=None)
                        db.execute("UPDATE processes SET record=? WHERE id=?", (json.dumps(current), process_id))
            return self.get(process_id, reconcile=False)
        return record

    def update(self, process_id, **fields):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM processes WHERE id=?", (process_id,)).fetchone()
            if row is None:
                raise KeyError("managed process not found")
            record = json.loads(row[0])
            record.update(fields)
            db.execute("UPDATE processes SET record=? WHERE id=?", (json.dumps(record), process_id))
        return record

    def list(self, session_id=None, execution_ids=()):
        with self.connect() as db:
            if session_id is None:
                rows = db.execute("SELECT id FROM processes ORDER BY rowid DESC").fetchall()
            else:
                ids = tuple(execution_ids)
                suffix = " OR execution_id IN (" + ",".join("?" for _ in ids) + ")" if ids else ""
                rows = db.execute("SELECT id FROM processes WHERE session_id=?" + suffix + " ORDER BY rowid DESC",
                                  (session_id, *ids)).fetchall()
        return [record for row in rows if (record := self.get(row[0])) is not None]

    def live_count(self, session_id):
        with self.connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM processes WHERE session_id=? AND json_extract(record,'$.status')"
                " IN ('starting','running','stopping')", (session_id,)).fetchone()[0]

    def unreported_exits(self):
        """Background commands that ended on their own and whose exit no reply has shown."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT record FROM processes WHERE json_extract(record,'$.background')=1"
                " AND json_extract(record,'$.reported')=0"
                " AND json_extract(record,'$.status') IN ('exited','failed') ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_output(self, process_id, chunk: bytes):
        from . import progress

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record,output,output_start,poll_offset FROM processes WHERE id=?",
                             (process_id,)).fetchone()
            if row is None:
                return
            old = bytes(row["output"])
            # Only the unfinished last line can still be redrawn; it and the
            # new chunk are stored as a terminal shows them.
            start = old.rfind(b"\n") + 1
            region = old[start:] + chunk
            region_at = row["output_start"] + start
            read = row["poll_offset"] - region_at if row["poll_offset"] > region_at else None
            collapsed, mapped = progress.collapse(region, read)
            poll_offset = row["poll_offset"] if mapped is None else region_at + mapped
            full = old[:start] + collapsed
            output = full[-MAX_OUTPUT_BYTES:]
            lines = output.split(b"\n")
            if len(lines) > MAX_OUTPUT_LINES + 1:
                output = b"\n".join(lines[-(MAX_OUTPUT_LINES + 1):])
            dropped = len(full) - len(output)
            record = json.loads(row["record"])
            record["truncated"] = record["truncated"] or bool(dropped)
            record["progress"] = progress.merge(record.get("progress"),
                                                progress.parse(region.decode("utf-8", errors="replace")))
            record["last_line"] = progress.last_line(output)
            db.execute("UPDATE processes SET output=?,output_start=?,poll_offset=?,record=? WHERE id=?",
                       (output, row["output_start"] + dropped, poll_offset, json.dumps(record), process_id))

    def output(self, process_id, *, poll=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if poll else "BEGIN")
            row = db.execute("SELECT output,output_start,poll_offset FROM processes WHERE id=?", (process_id,)).fetchone()
            if row is None:
                raise KeyError("managed process not found")
            output = bytes(row["output"])
            if poll:
                start = max(0, row["poll_offset"] - row["output_start"])
                db.execute("UPDATE processes SET poll_offset=? WHERE id=?",
                           (row["output_start"] + len(output), process_id))
                output = output[start:]
        return output.decode("utf-8", errors="replace")

    def take(self, process_id):
        """Output the agent has not read yet, and how many bytes were dropped before it could."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT output,output_start,poll_offset FROM processes WHERE id=?", (process_id,)).fetchone()
            if row is None:
                raise KeyError("managed process not found")
            output = bytes(row["output"])
            start = row["poll_offset"] - row["output_start"]
            db.execute("UPDATE processes SET poll_offset=? WHERE id=?",
                       (row["output_start"] + len(output), process_id))
        return output[max(0, start):], max(0, -start)

    def command(self, process_id, kind, text=None):
        if kind not in COMMAND_KINDS:
            raise ValueError("invalid process control")
        if text is not None and len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("process input exceeds 64 KiB")
        record = self.get(process_id)
        if record is None:
            raise KeyError("managed process not found")
        if record["status"] == "unknown":
            raise RuntimeError("process supervisor unavailable; process exit is unconfirmed")
        if record["status"] not in LIVE:
            if kind == "stop":
                return None
            raise RuntimeError("managed process is not running")
        command_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM processes WHERE id=?", (process_id,)).fetchone()
            if row is None:
                raise KeyError("managed process not found")
            current_status = json.loads(row[0])["status"]
            if current_status == "unknown":
                raise RuntimeError("process supervisor unavailable; process exit is unconfirmed")
            if current_status not in LIVE:
                if kind == "stop":
                    return None
                raise RuntimeError("managed process is not running")
            pending = db.execute("SELECT COUNT(*) FROM process_commands WHERE process_id=? AND status='pending'",
                                 (process_id,)).fetchone()[0]
            if pending >= 32:
                raise RuntimeError("managed process command queue is full")
            db.execute("INSERT INTO process_commands VALUES(?,?,?,?,?,?,?)",
                       (command_id, process_id, kind, text, "pending", None, time.time()))
        return command_id

    def commands(self, process_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM process_commands WHERE process_id=? AND status='pending' ORDER BY created_at", (process_id,))]

    def acknowledge(self, command_id, error=None):
        with self.connect() as db:
            db.execute("UPDATE process_commands SET status=?,error=?,input=NULL WHERE id=?",
                       ("failed" if error else "applied", error, command_id))

    def command_result(self, command_id):
        with self.connect() as db:
            row = db.execute("SELECT status,error FROM process_commands WHERE id=?", (command_id,)).fetchone()
            return dict(row) if row else None

    def prune(self, now=None):
        """Drop records past retention, with their output and commands."""
        cutoff = (now or time.time()) - RETENTION_SECONDS
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ids = [row[0] for row in db.execute(
                "SELECT id FROM processes WHERE"
                " (json_extract(record,'$.status') IN ('exited','stopped','failed')"
                "  AND json_extract(record,'$.ended_at') < ?)"
                " OR (json_extract(record,'$.status')='unknown' AND json_extract(record,'$.heartbeat') < ?)",
                (cutoff, cutoff))]
            db.executemany("DELETE FROM process_commands WHERE process_id=?", [(i,) for i in ids])
            db.executemany("DELETE FROM processes WHERE id=?", [(i,) for i in ids])
        return len(ids)

    def remove(self, process_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM processes WHERE id=?", (process_id,)).fetchone()
            if row and json.loads(row[0])["status"] in ACTIVE:
                raise RuntimeError("stop the process before removing its record")
            db.execute("DELETE FROM process_commands WHERE process_id=?", (process_id,))
            db.execute("DELETE FROM processes WHERE id=?", (process_id,))


def public_record(record):
    from .presentation import command_display
    from .progress import public
    return {**{key: record.get(key) for key in PUBLIC_FIELDS},
            "can_stop": record["status"] in LIVE,
            "display": command_display(record.get("command") or ""),
            "progress": public(record.get("progress")),
            "last_line": record.get("last_line")}
