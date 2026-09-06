"""Durable managed-process records, bounded output and supervisor commands."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_LINES = 2000
MAX_INPUT_BYTES = 64 * 1024
LIVE = frozenset({"starting", "running", "stopping"})
ACTIVE = LIVE | {"unknown"}
PUBLIC_FIELDS = (
    "id", "session_id", "execution_id", "tool_call_id", "command", "cwd",
    "status", "started_at", "ended_at", "pid", "exit_code", "backend_id", "truncated",
)


def process_identity(pid: int | None) -> str | None:
    """Read birth identity only; recorded PIDs are never used to send signals."""
    if not pid:
        return None
    if os.name == "posix":
        value = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                               capture_output=True, text=True, timeout=3)
        return value.stdout.strip() or None
    # Windows process handles are owned exclusively by the live supervisor;
    # portable inspection relies on its durable heartbeat, never PID reuse.
    return None


class ProcessStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from openprogram.paths import get_state_dir
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

    def create(self, *, session_id, execution_id, tool_call_id, command, cwd, backend_id):
        record = dict(id=uuid.uuid4().hex, session_id=session_id, execution_id=execution_id,
                      tool_call_id=tool_call_id, command=command, cwd=cwd, backend_id=backend_id,
                      status="starting", started_at=time.time(), ended_at=None, pid=None,
                      exit_code=None, truncated=False, supervisor_pid=None,
                      supervisor_identity=None, pid_identity=None, heartbeat=time.time())
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

    def append_output(self, process_id, chunk: bytes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record,output,output_start FROM processes WHERE id=?", (process_id,)).fetchone()
            if row is None:
                return
            full = bytes(row["output"]) + chunk
            output = full[-MAX_OUTPUT_BYTES:]
            lines = output.splitlines(keepends=True)
            if len(lines) > MAX_OUTPUT_LINES:
                output = b"".join(lines[-MAX_OUTPUT_LINES:])
            dropped = len(full) - len(output)
            record = json.loads(row["record"])
            record["truncated"] = record["truncated"] or bool(dropped)
            db.execute("UPDATE processes SET output=?,output_start=?,record=? WHERE id=?",
                       (output, row["output_start"] + dropped, json.dumps(record), process_id))

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

    def command(self, process_id, kind, text=None):
        if kind not in {"stop", "write"}:
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
    return {**{key: record.get(key) for key in PUBLIC_FIELDS},
            "can_stop": record["status"] in LIVE,
            "display": command_display(record.get("command") or "")}
