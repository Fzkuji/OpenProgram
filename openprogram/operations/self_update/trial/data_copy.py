"""The data a trial runs on: a sanitized copy of the owner's state.

What is copied is an allowlist, so a new credential file can never slip in the
way it could past a denylist. ``auth/``, ``mcp_tokens/``, ``mcp_servers.json``,
channel state, ``chrome-profile/``, ``runtime/``, ``self-updates/``,
``worktrees/``, logs, recordings and meetings are not on it. A fresh
``owner.json`` is generated.

Sanitizing is what stops anything live from repeating: tool paths are absolute
and unaffected by ``HOME``, so on Windows, where no OS sandbox exists, it is
the only thing protecting real project files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any
import uuid

from .fake_model import MODEL_ID, PROVIDER_ID

LAST_USED_KEY = "last_used_model"
DATABASE_LIMIT_BYTES = 4 * 1024 ** 3
SAMPLE_ROWS = 2_000
RECENT_CONVERSATIONS = 3
DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
# Top-level entries a trial needs besides databases, the config and conversations.
_COPIED_ENTRIES = (
    "agents", "agents.json", "programs", "programs_meta.json", "functions_meta.json",
    "program-sources.json", "skills", "skills.json", "session_aliases.json", "models",
)
_SKIPPED_SUFFIXES = (".pid", ".port", ".lock", "-wal", "-shm")
_SKIPPED_NAMES = frozenset({".git", "node_modules", "__pycache__", ".venv"})
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|apikey|secret|password|passwd|token|authorization|cookie|credentials?|"
    r"bearer|private[_-]?key|access[_-]?key|refresh[_-]?token|access[_-]?token)(?:$|[_-])",
    re.IGNORECASE,
)
_ACTIVE_JOB_STATUSES = frozenset({"pending", "queued", "running"})
_REASON = "self_update_trial_sanitized"
_RESTART_REQUESTED = "execution.restart.requested"
_RESTART_SETTLED = "execution.restart.settled"


class DataCopyError(RuntimeError):
    """The copy cannot be made, and the update stops with this reason."""


@dataclass
class DatabaseCopy:
    name: str
    size_bytes: int
    sampled: bool = False
    full_rows: dict[str, int] = field(default_factory=dict)
    sampled_rows: dict[str, int] = field(default_factory=dict)

    @property
    def scale(self) -> float:
        """How many times larger the real database is than this sample."""
        copied = sum(self.sampled_rows.values())
        return sum(self.full_rows.values()) / copied if self.sampled and copied else 1.0


@dataclass
class CopyReport:
    state_dir: Path
    databases: list[DatabaseCopy] = field(default_factory=list)
    conversations: list[str] = field(default_factory=list)
    sanitized: dict[str, int] = field(default_factory=dict)

    @property
    def sampled(self) -> list[str]:
        return [item.name for item in self.databases if item.sampled]


def is_database(path: Path) -> bool:
    return path.suffix.lower() in DATABASE_SUFFIXES


def _backup(source: Path, target: Path) -> None:
    """A consistent copy of a database the live worker is writing."""
    reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        writer = sqlite3.connect(target)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    target.chmod(0o600)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy_sampled(source: Path, target: Path) -> DatabaseCopy:
    """The schema and the first rows of each table of a database too large to copy."""
    report = DatabaseCopy(source.name, source.stat().st_size, sampled=True)
    reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    writer = sqlite3.connect(target)
    try:
        writer.execute("PRAGMA foreign_keys = OFF")
        writer.execute(f"PRAGMA user_version = {int(reader.execute('PRAGMA user_version').fetchone()[0])}")
        objects = reader.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
        ).fetchall()
        for kind, _name, statement in objects:
            if kind == "table":
                writer.execute(statement)
        virtual = {
            name for kind, name, statement in objects
            if kind == "table" and statement.upper().startswith("CREATE VIRTUAL")
        }
        for kind, name, _statement in objects:
            if kind != "table" or name in virtual or any(name.startswith(f"{other}_") for other in virtual):
                continue
            table = _quote(name)
            report.full_rows[name] = int(reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            rows = reader.execute(f"SELECT * FROM {table} LIMIT {SAMPLE_ROWS}").fetchall()
            if rows:
                marks = ",".join("?" * len(rows[0]))
                writer.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
            report.sampled_rows[name] = len(rows)
        for kind, _name, statement in objects:
            if kind != "table":
                writer.execute(statement)
        writer.commit()
    except sqlite3.Error as exc:
        raise DataCopyError(f"migration untested: {source.name} could not be sampled ({exc})") from exc
    finally:
        writer.close()
        reader.close()
    target.chmod(0o600)
    return report


def _copy_databases(source: Path, target: Path, *, limit: int) -> list[DatabaseCopy]:
    """Every top-level database; the largest are sampled until the total fits."""
    found = sorted((path for path in source.iterdir() if path.is_file() and is_database(path)),
                   key=lambda path: path.stat().st_size)
    total, whole, sampled = 0, [], []
    for path in found:
        size = path.stat().st_size
        (whole if total + size <= limit else sampled).append(path)
        total += size if total + size <= limit else 0
    reports = []
    for path in whole:
        try:
            _backup(path, target / path.name)
        except sqlite3.Error as exc:
            raise DataCopyError(f"{path.name} could not be copied: {exc}") from exc
        reports.append(DatabaseCopy(path.name, path.stat().st_size))
    reports += [_copy_sampled(path, target / path.name) for path in sampled]
    return reports


def _scrub(value: Any) -> Any:
    """``value`` without any key that holds a credential."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()
                if not (isinstance(key, str) and _SECRET_KEY.search(key))}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _loopback_provider(model_base_url: str) -> dict[str, Any]:
    return {
        "enabled": True, "source": "custom", "local": True, "label": "Trial", "base_url": model_base_url,
        "models": [{
            "id": MODEL_ID, "name": "Trial model", "provider": PROVIDER_ID, "api": "openai-completions",
            "base_url": model_base_url, "enabled": True, "source": "manual", "input": ["text"],
            "context_window": 200_000, "max_tokens": 4096,
        }],
    }


def _write_config(source: Path, target: Path, model_base_url: str) -> None:
    config = _read_json(source / "config.json")
    config = _scrub(config) if isinstance(config, dict) else {}
    config.pop("hooks", None)
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    providers = {name: value for name, value in providers.items() if isinstance(value, dict)}
    providers[PROVIDER_ID] = _loopback_provider(model_base_url)
    config["providers"] = providers
    config[LAST_USED_KEY] = {"provider": PROVIDER_ID, "model": MODEL_ID}
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    config["execution"] = {**execution, "auto_resume_window_seconds": 0}
    config.pop("ui", None)  # A saved port must not override the trial's.
    _write_json(target / "config.json", config)


def _copy_entry(source: Path, target: Path) -> None:
    if source.is_symlink():
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if child.name not in _SKIPPED_NAMES:
                _copy_entry(child, target / child.name)
    elif source.is_file() and not source.name.endswith(_SKIPPED_SUFFIXES):
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_database(source):
            _backup(source, target)
        else:
            shutil.copy2(source, target)


def _copy_scrubbed_tree(source: Path, target: Path) -> None:
    """Copy a tree, taking credentials out of every JSON file in it."""
    _copy_entry(source, target)
    if target.is_dir():
        files = [path for path in target.rglob("*.json") if path.is_file()]
    else:
        files = [target] if target.suffix == ".json" and target.is_file() else []
    for path in files:
        value = _read_json(path)
        if value is not None:
            _write_json(path, _scrub(value))


def _chat_directories(projects: Path) -> list[Path]:
    if not projects.is_dir():
        return []
    return [path for project in projects.iterdir() if project.is_dir() and not project.name.startswith(".")
            for chats in [project / "chats"] if chats.is_dir()
            for path in chats.iterdir() if path.is_dir() and not path.name.startswith(".")]


def _last_change(chat: Path) -> float:
    try:
        return max([chat.stat().st_mtime] + [path.stat().st_mtime for path in chat.iterdir()])
    except OSError:
        return 0.0


def _has_session_log(chat: Path) -> bool:
    from openprogram.state.store.session.placement import session_looks_present

    return session_looks_present(chat)


def recent_conversations(projects: Path) -> list[Path]:
    """The conversations a trial copies: the newest ones that have a session log."""
    with_logs = [chat for chat in _chat_directories(projects) if _has_session_log(chat)]
    return sorted(with_logs, key=_last_change, reverse=True)[:RECENT_CONVERSATIONS]


def _copy_projects(source: Path, target: Path) -> list[str]:
    """Project metadata and the most recent conversations; not project files."""
    from_root, to_root = source / "projects", target / "projects"
    if not from_root.is_dir():
        return []
    to_root.mkdir(parents=True, exist_ok=True)
    for entry in from_root.iterdir():
        if entry.is_file():
            _copy_entry(entry, to_root / entry.name)
        elif entry.is_dir() and not entry.name.startswith(".") and (entry / "settings.json").is_file():
            _copy_entry(entry / "settings.json", to_root / entry.name / "settings.json")
    folders = _chat_directories(from_root)
    if folders and not any(_has_session_log(chat) for chat in folders):
        raise DataCopyError("no conversation folder holds a session log, so the run state cannot be made safe to start on")
    chats = recent_conversations(from_root)
    for chat in chats:
        relative = chat.relative_to(from_root)
        _copy_entry(chat, to_root / relative)
    return [chat.name for chat in chats]


def _set_agents_model(target: Path) -> None:
    """Every Agent answers from the one loopback model."""
    agents = target / "agents"
    if not agents.is_dir():
        return
    for path in agents.glob("*/agent.json"):
        value = _read_json(path)
        if isinstance(value, dict):
            value["model"] = {"provider": PROVIDER_ID, "id": MODEL_ID}
            _write_json(path, value)


def _write_owner(target: Path) -> None:
    _write_json(target / "owner.json", {"version": 1, "principal_id": f"owner/install/{uuid.uuid4().hex[:16]}"})


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")}


def _open_row_endings(now: float) -> tuple:
    """Every logged run row that can be unfinished: (table, count, column, finished values, what ends it)."""
    from openprogram.runtime.execution.attempts import AttemptStatus
    from openprogram.runtime.execution.effects import TERMINAL_EFFECT_STATUSES
    from openprogram.runtime.execution.model import TERMINAL_COMMAND_STATUSES, TERMINAL_EXECUTION_STATUSES
    from openprogram.runtime.execution.waits import _TERMINAL_WAIT_STATUSES
    from openprogram.runtime.jobs.resource_governance.limits import _NO_TERMINAL_BLOCK

    def values(statuses) -> frozenset:
        return frozenset(status.value for status in statuses)

    def first_set(column: str):
        return lambda row: row[column] or now

    return (
        ("executions", "executions", "status", values(TERMINAL_EXECUTION_STATUSES),
         {"status": "interrupted", "reason_code": _REASON, "current_attempt_id": None, "owner_lease_json": "{}",
          "terminal_at": first_set("terminal_at"), "updated_at": now}),
        ("attempts", "attempts", "status", frozenset({AttemptStatus.ENDED.value}),
         {"status": AttemptStatus.ENDED.value, "outcome": _REASON, "ended_at": first_set("ended_at"), "updated_at": now}),
        ("execution_waits", "waits", "status", values(_TERMINAL_WAIT_STATUSES),
         {"status": "cancelled", "claim_owner": None, "claim_expires_at": None,
          "resolved_at": first_set("resolved_at"), "updated_at": now}),
        ("commands", "commands", "status", values(TERMINAL_COMMAND_STATUSES),
         {"status": "rejected", "rejection_code": _REASON, "updated_at": now}),
        ("effects", "effects", "status", values(TERMINAL_EFFECT_STATUSES),
         {"status": "not_committed", "resolved_at": first_set("resolved_at"), "updated_at": now}),
        ("execution_resource_intents", "resource_intents", "state", frozenset({"applied"}),
         {"state": "applied", "claim_owner": None, "claim_expires_at": None, "last_error": _REASON,
          "completed_at": first_set("completed_at"), "updated_at": now}),
        ("job_admissions", "admissions", "state", frozenset({"released"}),
         {"state": "released", "queue_state": "released", "dispatch_ready": 0, **_NO_TERMINAL_BLOCK,
          "owner_instance_id": None, "resume_parent_msg_id": None, "reason_code": _REASON,
          "released_at": first_set("released_at")}),
        ("job_finalizations", "finalizations", "state", frozenset({"completed"}),
         {"state": "completed", "completed_at": first_set("completed_at")}),
        ("usage_reservations", "reservations", "state", frozenset({"released", "settled"}), {"state": "released"}),
    )


def _is_open(column: str, finished: frozenset):
    return lambda row: row[column] not in finished


def _copied_run_tables(target: Path, session_ids):
    """The copy's run tables, read through the copy's own conversations."""
    from openprogram.runtime.execution.store.run_tables import RunTables, SessionRunLog
    from openprogram.state.store.session.session_store.service import SessionStore

    class CopiedRunLog(SessionRunLog):
        def __init__(self) -> None:
            super().__init__(target)
            self._copied = SessionStore(target / "projects")

        def _sessions(self):
            return self._copied

    tables = RunTables(CopiedRunLog())
    for session_id in session_ids:
        tables.ensure_session(session_id)
    return tables


def _unsettled_restarts(rows) -> list[dict]:
    """Restart intents nothing answered, for executions the copy still has."""
    settled = {(row["execution_id"], json.loads(row["payload_json"]).get("request_sequence"))
               for row in rows.rows("execution_events", kind=_RESTART_SETTLED)}
    return [row for row in rows.rows("execution_events", kind=_RESTART_REQUESTED)
            if (row["execution_id"], row["sequence"]) not in settled and rows.get("executions", row["execution_id"])]


def _copied_sessions(target: Path) -> dict[str, Path]:
    """The conversations in the copy. One without a log means the run store is not where this code expects it."""
    from openprogram.state.store.session.placement import iter_session_dirs

    projects = target / "projects"
    missing = sorted(chat.name for chat in _chat_directories(projects) if not _has_session_log(chat))
    if missing:
        raise DataCopyError(f"copied conversations have no session log, so their run state cannot be made safe: {missing}")
    return dict(iter_session_dirs(projects))


def _sanitize_run_state(target: Path) -> dict[str, int]:
    """Nothing in the copy may look like it is still running.

    Run state is whole rows on ``turn.rows`` lines of each conversation's log,
    the last line for a row winning. Earlier lines are history and stay as they
    are: the same append the worker makes ends what is unfinished, so the copy
    keeps a valid log. Reading the copy back proves it.
    """
    from openprogram.runtime.execution.store.run_tables import RowConflict

    sessions = _copied_sessions(target)
    tables = _copied_run_tables(target, sessions)
    endings = _open_row_endings(time.time())
    counts = {"restart_intents": 0}
    try:
        with tables.transaction() as rows:
            for table, count, column, finished, ending in endings:
                counts[count] = rows.update(table, ending, where=_is_open(column, finished))
    except (RowConflict, ValueError, KeyError) as exc:
        raise DataCopyError(f"the copied run state could not be ended: {exc}") from exc
    owners: dict[str, list[dict]] = {}
    for row in _unsettled_restarts(tables.handle):
        session_id = tables.handle.get("executions", row["execution_id"])["session_id"]
        owners.setdefault(session_id, []).append({"t": "execution_events", "gone": [row["sequence"]]})
    for session_id, lines in owners.items():
        tables.log.append(session_id, lines)
        counts["restart_intents"] += len(lines)
    reread = _copied_run_tables(target, sessions)
    for table, _count, column, finished, _ending in endings:
        if reread.handle.exists(table, where=_is_open(column, finished)):
            raise DataCopyError(f"{table} in the copy still holds unfinished rows after sanitizing")
    if _unsettled_restarts(reread.handle):
        raise DataCopyError("the copy still holds a restart intent after sanitizing")
    return counts


def _copy_run_logs(source: Path, target: Path, conversations: list[str]) -> None:
    """The run logs beside the conversations: workflow revisions, and which conversation each run is in."""
    from openprogram.runtime.execution.store.run_tables import RunLogFiles

    origin, copy = RunLogFiles(source), RunLogFiles(target)
    if origin.global_path.is_file():
        shutil.copy2(origin.global_path, copy.global_path)
    kept = set(conversations)
    for execution_id, session_id in origin.read_index().items():
        if session_id in kept:
            copy.add_to_index(execution_id, session_id)


def _clear_tables(path: Path, tables: tuple[str, ...]) -> int:
    """Rows that describe processes and operations of the live instance."""
    if not path.is_file():
        return 0
    removed = 0
    connection = sqlite3.connect(path)
    try:
        for table in tables:
            if _table_columns(connection, table):
                removed += connection.execute(f"DELETE FROM {_quote(table)}").rowcount
        connection.commit()
    finally:
        connection.close()
    return removed


def _end_jobs(target: Path) -> int:
    """Queued and running Jobs in the copied conversations end here; each log keeps a ``jobs.json`` document."""
    from openprogram.state.store.session.session_store.service import SessionStore

    store = SessionStore(target / "projects")
    ended = 0
    for directory in _copied_sessions(target).values():
        document = store.state_file_at(directory, "jobs.json")
        if not document.exists():
            continue
        try:
            value = json.loads(document.read_text(encoding="utf-8"))
        except ValueError:
            continue
        jobs = value.get("jobs") if isinstance(value, dict) else None
        if not isinstance(jobs, dict):
            continue
        active = [job for job in jobs.values() if isinstance(job, dict) and job.get("status") in _ACTIVE_JOB_STATUSES]
        for job in active:
            job["status"] = "cancelled"
            job["completed_at"] = job.get("completed_at") or time.time()
            job["error"] = _REASON
        if active:
            document.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            ended += len(active)
    return ended


def sanitize(target: Path) -> dict[str, int]:
    """So that startup recovery, run on this copy, has nothing live to repeat."""
    counts = _sanitize_run_state(target)
    counts["processes"] = _clear_tables(target / "processes.db", ("processes", "process_commands"))
    counts["resources"] = _clear_tables(target / "session-resources.db", ("resource_uses",))
    counts["jobs"] = _end_jobs(target)
    return counts


def copy_state(source: Path, target: Path, *, model_base_url: str,
               database_limit: int = DATABASE_LIMIT_BYTES) -> CopyReport:
    """Make ``target`` the sanitized state directory of a trial."""
    source, target = Path(source), Path(target)
    if not source.is_dir():
        raise DataCopyError(f"state directory not found: {source}")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = CopyReport(target)
    report.databases = _copy_databases(source, target, limit=database_limit)
    _write_config(source, target, model_base_url)
    for name in _COPIED_ENTRIES:
        if (source / name).exists():
            _copy_scrubbed_tree(source / name, target / name)
    _set_agents_model(target)
    report.conversations = _copy_projects(source, target)
    _copy_run_logs(source, target, report.conversations)
    _write_owner(target)
    report.sanitized = sanitize(target)
    os.chmod(target, 0o700)
    return report


__all__ = ["CopyReport", "DataCopyError", "DatabaseCopy", "copy_state", "is_database", "recent_conversations", "sanitize"]
