"""Retained browser Page descriptors, write fencing, and control projection."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from openprogram.session_resources import display_target

_log = logging.getLogger(__name__)
_PROCESS_INCARNATION = uuid.uuid4().hex[:12]
UNASSIGNED = "unassigned"
_FROZEN_LIFECYCLES = frozenset({
    "closed", "unavailable", "restoring", "restore_failed", "superseded",
})
_RESTORABLE_LIFECYCLES = frozenset({
    "restoring", "unavailable", "restore_failed",
})
_FROZEN_SQL = (
    "'closed','unavailable','restoring','restore_failed','superseded'"
)


def resource_db_path() -> Path:
    from openprogram.paths import get_state_dir
    return get_state_dir() / "session-resources.db"


def writes_fenced(page_key: str) -> bool:
    """Compatibility for older callers: pages have no independent write restriction."""
    return False


def last_input_seq(page_key: str) -> int:
    return 0


def fence_page_writes(page_key: str, *, input_seq: int | None = None) -> bool:
    """Legacy no-op. Execution dispatch guards own pause and cancellation."""
    return False


def clear_page_write_fence(page_key: str) -> None:
    return None


class BrowserResourceStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else resource_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS browser_resources (
                resource_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                target TEXT NOT NULL, tab_id TEXT, window_id TEXT,
                connection_generation INTEGER NOT NULL, generation INTEGER NOT NULL,
                sequence INTEGER NOT NULL, lifecycle TEXT NOT NULL, live INTEGER NOT NULL,
                last_operation TEXT, updated_at REAL NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS browser_resource_associations (
                id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, session_id TEXT,
                conversation_session_id TEXT, execution_id TEXT,
                user_message_id TEXT, assistant_message_id TEXT, agent_name TEXT,
                created_at REAL NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS browser_resource_control (
                resource_id TEXT PRIMARY KEY, control_state TEXT NOT NULL,
                pause_command_id TEXT, pause_execution_id TEXT,
                last_input_seq INTEGER NOT NULL)"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS browser_assoc_conversation "
                "ON browser_resource_associations(conversation_session_id)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS browser_resource_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS browser_resource_presentation (
                resource_id TEXT PRIMARY KEY, presentation_id TEXT NOT NULL)"""
            )
            meta = db.execute(
                "SELECT value FROM browser_resource_meta WHERE key='incarnation'",
            ).fetchone()
            if meta is None:
                db.execute(
                    "INSERT INTO browser_resource_meta VALUES ('incarnation', ?)",
                    (_PROCESS_INCARNATION,),
                )
            elif meta["value"] != _PROCESS_INCARNATION:
                now = time.time()
                db.execute(
                    """UPDATE browser_resources SET live=0, lifecycle='unavailable',
                    sequence=sequence+1, connection_generation=0, updated_at=?
                    WHERE live=1 AND lifecycle NOT IN ('closed','superseded')""",
                    (now,),
                )
                db.execute(
                    """UPDATE browser_resource_control SET control_state='idle',
                    pause_command_id=NULL, pause_execution_id=NULL
                    WHERE resource_id IN (
                        SELECT resource_id FROM browser_resources
                        WHERE lifecycle='unavailable' AND live=0
                    )"""
                )
                db.execute(
                    "UPDATE browser_resource_meta SET value=? WHERE key='incarnation'",
                    (_PROCESS_INCARNATION,),
                )
        if os.name == "posix":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def retain(
        self,
        *,
        page_key: str,
        window_id: str = "",
        tab_id: str = "",
        title: str = "",
        target: str = "",
        connection_generation: int = 0,
        session_id: str | None = None,
        execution_id: str | None = None,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        agent_name: str | None = None,
        conversation_session_id: str | None = None,
        live: bool = True,
    ) -> str:
        resource_id = str(page_key)
        now = time.time()
        sanitized = display_target(target)
        conversation = conversation_session_id or session_id
        with self.connect() as db:
            current = db.execute(
                "SELECT generation, sequence, lifecycle FROM browser_resources "
                "WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
            incoming_generation = int(connection_generation or 0)
            generation = max(incoming_generation, 1)
            sequence = 0
            existing_lifecycle = ""
            if current is not None:
                generation = max(int(current["generation"] or 1), generation)
                sequence = int(current["sequence"] or 0)
                existing_lifecycle = str(current["lifecycle"] or "")
            # Closed/unavailable metadata is not a live target. A new Page needs
            # a new page_key; the same resource_id must not flip back to idle.
            # Incoming epoch is stored as excluded.connection_generation so the
            # UPDATE predicate can reject zero/older writers without Python
            # rewriting them to the current epoch.
            if existing_lifecycle in _FROZEN_LIFECYCLES:
                live = False
            db.execute(
                f"""INSERT INTO browser_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(resource_id) DO UPDATE SET
                title=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.title
                    WHEN excluded.connection_generation=0 THEN browser_resources.title
                    WHEN excluded.connection_generation < browser_resources.connection_generation
                    THEN browser_resources.title
                    WHEN excluded.title='' THEN browser_resources.title
                    ELSE excluded.title END,
                target=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.target
                    WHEN excluded.connection_generation=0 THEN browser_resources.target
                    WHEN excluded.connection_generation < browser_resources.connection_generation
                    THEN browser_resources.target
                    WHEN excluded.target='' THEN browser_resources.target
                    ELSE excluded.target END,
                tab_id=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.tab_id
                    WHEN excluded.connection_generation=0 THEN browser_resources.tab_id
                    WHEN excluded.connection_generation < browser_resources.connection_generation
                    THEN browser_resources.tab_id
                    WHEN ifnull(browser_resources.tab_id,'') != '' THEN browser_resources.tab_id
                    ELSE excluded.tab_id END,
                window_id=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.window_id
                    WHEN excluded.connection_generation=0 THEN browser_resources.window_id
                    WHEN excluded.connection_generation < browser_resources.connection_generation
                    THEN browser_resources.window_id
                    WHEN ifnull(browser_resources.window_id,'') != '' THEN browser_resources.window_id
                    ELSE excluded.window_id END,
                connection_generation=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.connection_generation
                    WHEN excluded.connection_generation=0 THEN browser_resources.connection_generation
                    WHEN excluded.connection_generation < browser_resources.connection_generation
                    THEN browser_resources.connection_generation
                    ELSE excluded.connection_generation END,
                generation=excluded.generation,
                lifecycle=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN browser_resources.lifecycle ELSE excluded.lifecycle END,
                live=CASE WHEN browser_resources.lifecycle IN ({_FROZEN_SQL})
                    THEN 0 ELSE excluded.live END,
                updated_at=excluded.updated_at""",
                (
                    resource_id, "web", str(title or "")[:160], sanitized,
                    str(tab_id or "")[:512], str(window_id or "")[:160],
                    incoming_generation, generation, sequence,
                    "connected" if live else "unavailable", 1 if live else 0,
                    None, now,
                ),
            )
            if db.execute(
                "SELECT 1 FROM browser_resource_presentation WHERE resource_id=?",
                (resource_id,),
            ).fetchone() is None:
                db.execute(
                    "INSERT INTO browser_resource_presentation VALUES (?,?)",
                    (resource_id, resource_id),
                )
            assoc_id = _association_id(resource_id, conversation, execution_id)
            existing = db.execute(
                "SELECT id FROM browser_resource_associations WHERE resource_id=? AND "
                "ifnull(conversation_session_id,'')=? AND ifnull(execution_id,'')=?",
                (resource_id, conversation or "", execution_id or ""),
            ).fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO browser_resource_associations VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        assoc_id, resource_id, session_id, conversation, execution_id,
                        user_message_id, assistant_message_id, agent_name, now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE browser_resource_associations SET session_id=?, agent_name=?,
                    user_message_id=COALESCE(?, user_message_id),
                    assistant_message_id=COALESCE(?, assistant_message_id)
                    WHERE id=?""",
                    (session_id, agent_name, user_message_id, assistant_message_id, existing["id"]),
                )
                assoc_id = existing["id"]
            if db.execute(
                "SELECT 1 FROM browser_resource_control WHERE resource_id=?",
                (resource_id,),
            ).fetchone() is None:
                db.execute(
                    "INSERT INTO browser_resource_control VALUES (?,?,?,?,?)",
                    (resource_id, "idle" if live else "closed", None, None, 0),
                )
        return assoc_id

    def update_display(
        self, resource_id: str, *, title: str = "", target: str = "",
        connection_generation: int | None = None,
    ) -> None:
        if not resource_id:
            return
        title = str(title or "")[:160]
        sanitized = display_target(target) if target else ""
        if not title and not sanitized:
            return
        incoming = int(connection_generation or 0)
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resources SET
                title=CASE WHEN ?='' THEN title ELSE ? END,
                target=CASE WHEN ?='' THEN target ELSE ? END,
                updated_at=?
                WHERE resource_id=?
                AND lifecycle NOT IN ('closed','unavailable','restoring','restore_failed','superseded')
                AND (
                    connection_generation=0
                    OR (? > 0 AND ? >= connection_generation)
                )""",
                (
                    title, title, sanitized, sanitized, time.time(), resource_id,
                    incoming, incoming,
                ),
            )

    def clear_execution_use(self, execution_id: str) -> None:
        if not execution_id:
            return
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resource_control SET control_state='idle'
                WHERE resource_id IN (
                    SELECT resource_id FROM browser_resource_associations WHERE execution_id=?
                ) AND control_state IN ('active','idle')""",
                (execution_id,),
            )

    def mark_unavailable(self, resource_id: str, *, reason: str = "disconnected") -> None:
        del reason
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resources SET live=0, lifecycle='unavailable',
                sequence=sequence+1, updated_at=?
                WHERE resource_id=? AND lifecycle NOT IN ('closed','superseded')""",
                (time.time(), resource_id),
            )
            db.execute(
                """UPDATE browser_resource_control SET control_state='idle',
                pause_command_id=NULL, pause_execution_id=NULL
                WHERE resource_id=? AND control_state != 'closed'""",
                (resource_id,),
            )
        clear_page_write_fence(resource_id)

    def mark_window_restorable(self, window_id: str, lifecycle: str) -> list[str]:
        if not window_id or lifecycle not in {"restoring", "restore_failed"}:
            return []
        ids = []
        with self.connect() as db:
            rows = db.execute(
                """SELECT resource_id FROM browser_resources
                WHERE window_id=? AND lifecycle IN ('restoring','unavailable','restore_failed')""",
                (window_id,),
            ).fetchall()
            ids = [row["resource_id"] for row in rows]
        for resource_id in ids:
            if lifecycle == "restoring":
                self.mark_restoring(resource_id)
            else:
                self.mark_restore_failed(resource_id)
        return ids

    def mark_restoring(self, resource_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resources SET live=0, lifecycle='restoring',
                sequence=sequence+1, updated_at=?
                WHERE resource_id=? AND lifecycle NOT IN ('closed','superseded')""",
                (time.time(), resource_id),
            )
            db.execute(
                """UPDATE browser_resource_control SET control_state='idle',
                pause_command_id=NULL, pause_execution_id=NULL
                WHERE resource_id=?""",
                (resource_id,),
            )
        clear_page_write_fence(resource_id)

    def mark_restore_failed(self, resource_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resources SET live=0, lifecycle='restore_failed',
                sequence=sequence+1, updated_at=?
                WHERE resource_id=? AND lifecycle NOT IN ('closed','superseded')""",
                (time.time(), resource_id),
            )
            db.execute(
                """UPDATE browser_resource_control SET control_state='idle',
                pause_command_id=NULL, pause_execution_id=NULL
                WHERE resource_id=?""",
                (resource_id,),
            )
        clear_page_write_fence(resource_id)

    def adopt_successor(
        self,
        *,
        successor_id: str,
        window_id: str,
        tab_id: str,
        connection_generation: int = 0,
        title: str = "",
        target: str = "",
    ) -> str:
        """Mint a live successor Page and move verified tab ownership here.

        Predecessor must share exact window_id and tab_id and must not be
        closed. Associations are copied; act authority is not.
        """
        if not successor_id or not window_id or not tab_id:
            return successor_id
        now = time.time()
        sanitized = display_target(target) if target else ""
        incoming = int(connection_generation or 0)
        with self.connect() as db:
            closed = db.execute(
                """SELECT 1 FROM browser_resources
                WHERE window_id=? AND tab_id=? AND lifecycle='closed'
                AND resource_id=?""",
                (window_id, tab_id, successor_id),
            ).fetchone()
            if closed is not None:
                return successor_id
            pred = db.execute(
                """SELECT * FROM browser_resources
                WHERE window_id=? AND tab_id=? AND resource_id!=?
                AND lifecycle IN ('restoring','unavailable','restore_failed')
                ORDER BY updated_at DESC""",
                (window_id, tab_id, successor_id),
            ).fetchone()
            if pred is not None and str(pred["lifecycle"] or "") == "closed":
                pred = None
            if pred is None:
                return successor_id
            pred_title = str((pred["title"] if pred else "") or "")
            pred_target = str((pred["target"] if pred else "") or "")
            title_out = (str(title or "")[:160] or pred_title)[:160]
            target_out = sanitized or pred_target
            pred_generation = int(pred["generation"] or 1)
            pred_sequence = int(pred["sequence"] or 0)
            generation = max(incoming, pred_generation + 1)
            sequence = pred_sequence + 1
            current = db.execute(
                "SELECT generation, sequence, lifecycle FROM browser_resources WHERE resource_id=?",
                (successor_id,),
            ).fetchone()
            if current is not None:
                if str(current["lifecycle"] or "") in {"closed", "superseded"}:
                    return successor_id
                generation = max(int(current["generation"] or 1), generation)
                sequence = max(int(current["sequence"] or 0) + 1, sequence)
            db.execute(
                """INSERT INTO browser_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(resource_id) DO UPDATE SET
                title=CASE WHEN excluded.title='' THEN browser_resources.title ELSE excluded.title END,
                target=CASE WHEN excluded.target='' THEN browser_resources.target ELSE excluded.target END,
                tab_id=excluded.tab_id,
                window_id=excluded.window_id,
                connection_generation=excluded.connection_generation,
                generation=excluded.generation,
                sequence=excluded.sequence,
                lifecycle='connected',
                live=1,
                updated_at=excluded.updated_at""",
                (
                    successor_id, "web", title_out, target_out,
                    tab_id, window_id, incoming, generation, sequence,
                    "connected", 1, None, now,
                ),
            )
            if pred is not None:
                pred_id = pred["resource_id"]
                pred_pres = db.execute(
                    "SELECT presentation_id FROM browser_resource_presentation WHERE resource_id=?",
                    (pred_id,),
                ).fetchone()
                presentation_id = str(
                    (pred_pres["presentation_id"] if pred_pres else pred_id) or pred_id
                )
                db.execute(
                    """INSERT INTO browser_resource_presentation VALUES (?,?)
                    ON CONFLICT(resource_id) DO UPDATE SET presentation_id=excluded.presentation_id""",
                    (successor_id, presentation_id),
                )
                assocs = db.execute(
                    "SELECT * FROM browser_resource_associations WHERE resource_id=?",
                    (pred_id,),
                ).fetchall()
                for assoc in assocs:
                    db.execute(
                        """DELETE FROM browser_resource_associations
                        WHERE resource_id=? AND id!=?
                        AND ifnull(conversation_session_id,'')=?
                        AND ifnull(execution_id,'')=?""",
                        (
                            successor_id, assoc["id"],
                            assoc["conversation_session_id"] or "",
                            assoc["execution_id"] or "",
                        ),
                    )
                    db.execute(
                        "UPDATE browser_resource_associations SET resource_id=? WHERE id=?",
                        (successor_id, assoc["id"]),
                    )
                db.execute(
                    """UPDATE browser_resources SET live=0, lifecycle='superseded',
                    sequence=sequence+1, updated_at=? WHERE resource_id=?""",
                    (now, pred_id),
                )
            existing_control = db.execute(
                "SELECT last_input_seq FROM browser_resource_control WHERE resource_id=?",
                (successor_id,),
            ).fetchone()
            last_seq = int((existing_control["last_input_seq"] if existing_control else 0) or 0)
            if existing_control is None:
                db.execute(
                    "INSERT INTO browser_resource_control VALUES (?,?,?,?,?)",
                    (successor_id, "idle", None, None, last_seq),
                )
            else:
                db.execute(
                    """UPDATE browser_resource_control SET control_state='idle',
                    pause_command_id=NULL, pause_execution_id=NULL,
                    last_input_seq=? WHERE resource_id=?""",
                    (last_seq, successor_id),
                )
        clear_page_write_fence(successor_id)
        return successor_id

    def mark_closed(self, resource_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE browser_resources SET live=0, lifecycle='closed',
                sequence=sequence+1, updated_at=? WHERE resource_id=?""",
                (time.time(), resource_id),
            )
            db.execute(
                """UPDATE browser_resource_control SET control_state='closed',
                pause_command_id=NULL, pause_execution_id=NULL
                WHERE resource_id=?""",
                (resource_id,),
            )
        clear_page_write_fence(resource_id)

    def bump_sequence(self, resource_id: str) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT sequence FROM browser_resources WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
            sequence = int(row["sequence"] or 0) + 1 if row else 1
            db.execute(
                "UPDATE browser_resources SET sequence=?, updated_at=? WHERE resource_id=?",
                (sequence, time.time(), resource_id),
            )
            return sequence

    def set_control_state(
        self, resource_id: str, state: str, *, command_id: str | None = None,
        execution_id: str | None = None, input_seq: int | None = None,
        bump: bool = True, clear_pause: bool = False,
    ) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM browser_resource_control WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
            pause_id = None if clear_pause else command_id
            pause_exec = None if clear_pause else execution_id
            if row is None:
                db.execute(
                    "INSERT INTO browser_resource_control VALUES (?,?,?,?,?)",
                    (resource_id, state, pause_id, pause_exec, int(input_seq or 0)),
                )
                changed = True
            else:
                changed = row["control_state"] != state
                if clear_pause:
                    db.execute(
                        """UPDATE browser_resource_control SET control_state=?,
                        pause_command_id=NULL, pause_execution_id=NULL,
                        last_input_seq=CASE WHEN ? IS NULL THEN last_input_seq ELSE ? END
                        WHERE resource_id=?""",
                        (state, input_seq, int(input_seq or 0), resource_id),
                    )
                else:
                    db.execute(
                        """UPDATE browser_resource_control SET control_state=?,
                        pause_command_id=COALESCE(?, pause_command_id),
                        pause_execution_id=COALESCE(?, pause_execution_id),
                        last_input_seq=CASE WHEN ? IS NULL THEN last_input_seq ELSE ? END
                        WHERE resource_id=?""",
                        (state, command_id, execution_id, input_seq, int(input_seq or 0), resource_id),
                    )
        if bump and changed:
            self.bump_sequence(resource_id)

    def record_operation(self, resource_id: str, operation: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(operation), ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                "UPDATE browser_resources SET last_operation=?, updated_at=? WHERE resource_id=?",
                (payload, time.time(), resource_id),
            )

    def page_keys_for_tab(self, window_id: str, tab_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT resource_id FROM browser_resources WHERE window_id=? AND tab_id=?",
                (window_id, tab_id),
            ).fetchall()
        return [row["resource_id"] for row in rows]

    def associations_for_page(self, resource_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM browser_resource_associations WHERE resource_id=?",
                (resource_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM browser_resources WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
            control = db.execute(
                "SELECT * FROM browser_resource_control WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        if control is not None:
            data.update(dict(control))
        with self.connect() as db:
            pres = db.execute(
                "SELECT presentation_id FROM browser_resource_presentation WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
        if pres is not None:
            data["presentation_id"] = pres["presentation_id"]
        return data

    def list_rows(
        self,
        conversation_session_id: str,
        *,
        executions=(),
        parents: Mapping[str, str | None] | None = None,
        session_store=None,
        execution_inputs: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        executions_by_id = {item.execution_id: item for item in executions}
        parents = dict(parents or {})
        inputs = dict(execution_inputs or {})
        with self.connect() as db:
            associations = [dict(row) for row in db.execute(
                "SELECT * FROM browser_resource_associations"
            )]
        visible_ids = []
        for assoc in associations:
            if not _association_visible(
                assoc, conversation_session_id, executions_by_id,
            ):
                continue
            rid = assoc["resource_id"]
            if rid not in visible_ids:
                visible_ids.append(rid)
        for rid in visible_ids:
            reconcile_resource_control(
                rid, store=self, executions_by_id=executions_by_id,
            )
        with self.connect() as db:
            resources = {
                row["resource_id"]: dict(row)
                for row in db.execute("SELECT * FROM browser_resources")
            }
            presentations = {
                row["resource_id"]: row["presentation_id"]
                for row in db.execute("SELECT resource_id, presentation_id FROM browser_resource_presentation")
            }
            for rid, resource in resources.items():
                resource["presentation_id"] = presentations.get(rid) or rid
            controls = {
                row["resource_id"]: dict(row)
                for row in db.execute("SELECT * FROM browser_resource_control")
            }
        rows = []
        seen = {}
        for assoc in associations:
            if not _association_visible(
                assoc, conversation_session_id, executions_by_id,
            ):
                continue
            resource = resources.get(assoc["resource_id"])
            if resource is None:
                continue
            if (resource.get("lifecycle") or "") == "superseded":
                continue
            anchor = _projected_anchor(
                assoc, conversation_session_id, executions_by_id, parents, inputs,
            )
            branch_id, branch_name = (None, None)
            if anchor:
                branch_id, branch_name = resolve_stable_branch(
                    conversation_session_id, anchor, session_store=session_store,
                )
            control = controls.get(assoc["resource_id"]) or {}
            controller_id = control.get("pause_execution_id") or assoc.get("execution_id")
            row = _public_row(
                resource, assoc, control, conversation_session_id, branch_id, branch_name,
                execution=executions_by_id.get(controller_id or ""),
            )
            previous = seen.get(row["id"])
            if previous is None or _row_is_newer(row, previous, executions_by_id):
                seen[row["id"]] = row
        return list(seen.values())


def _row_is_newer(row, previous, executions_by_id) -> bool:
    current = executions_by_id.get(row.get("execution_id") or "")
    prior = executions_by_id.get(previous.get("execution_id") or "")
    current_live = getattr(getattr(current, "status", None), "value", "") in {
        "running", "pausing", "paused", "queued",
    }
    prior_live = getattr(getattr(prior, "status", None), "value", "") in {
        "running", "pausing", "paused", "queued",
    }
    if current_live != prior_live:
        return current_live
    return int(row.get("sequence") or 0) >= int(previous.get("sequence") or 0)


def _association_id(resource_id, conversation, execution_id) -> str:
    return "browser:" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{resource_id}:{conversation or ''}:{execution_id or ''}",
    ).hex[:20]


def _association_visible(assoc, conversation_session_id, executions_by_id) -> bool:
    if assoc.get("conversation_session_id") == conversation_session_id:
        return True
    if assoc.get("session_id") == conversation_session_id:
        return True
    execution_id = assoc.get("execution_id")
    execution = executions_by_id.get(execution_id) if execution_id else None
    return execution is not None


def _projected_anchor(assoc, conversation_session_id, executions_by_id, parents, inputs):
    execution_id = assoc.get("execution_id")
    seen = set()
    current = execution_id
    while current and current not in seen:
        seen.add(current)
        execution = executions_by_id.get(current)
        if execution is not None and execution.session_id == conversation_session_id:
            record = inputs.get(current)
            if record is not None:
                assistant = getattr(record, "assistant_message_id", None)
                user = getattr(record, "user_message_id", None)
                if assistant or user:
                    return assistant or user
        current = parents.get(current) or getattr(execution, "parent_execution_id", None)
    if assoc.get("session_id") == conversation_session_id:
        return assoc.get("assistant_message_id") or assoc.get("user_message_id")
    return None


def _public_row(resource, assoc, control, conversation_session_id, branch_id, branch_name, execution=None):
    live = bool(resource.get("live"))
    lifecycle = resource.get("lifecycle") or "unavailable"
    fenced = writes_fenced(resource["resource_id"])
    stored_state = control.get("control_state") or "idle"
    if lifecycle == "closed":
        control_state = "closed"
        status = "closed"
    elif lifecycle == "restore_failed":
        control_state = "idle"
        status = "restore_failed"
    elif lifecycle == "restoring":
        control_state = "idle"
        status = "restoring"
    elif not live or lifecycle == "unavailable":
        control_state = "idle"
        status = "unknown"
    elif stored_state == "waiting":
        control_state = "waiting"
        status = "open"
    elif stored_state == "paused":
        control_state = "paused"
        status = "open"
    elif stored_state in {"yielding", "stop_unconfirmed"}:
        control_state = stored_state
        status = "in_use"
    elif fenced:
        control_state = "yielding"
        status = "in_use"
    elif execution is not None and not _active_execution(execution) and not fenced:
        control_state = "idle"
        status = "open"
    elif stored_state == "active":
        control_state = "active"
        status = "in_use"
    else:
        control_state = stored_state if stored_state in {"idle", "active", "paused", "waiting"} else "idle"
        status = "open" if live else "unknown"
    operation = None
    raw = resource.get("last_operation")
    if raw:
        try:
            operation = json.loads(raw)
        except (TypeError, ValueError):
            operation = None
    key = branch_id or UNASSIGNED
    presentation = resource.get("presentation_id") or resource["resource_id"]
    return {
        "id": f"browser:{presentation}:{key}",
        "resource_id": resource["resource_id"],
        "session_id": assoc.get("session_id"),
        "conversation_session_id": conversation_session_id,
        "execution_id": assoc.get("execution_id"),
        "branch_id": branch_id,
        "branch_name": branch_name,
        "agent_name": assoc.get("agent_name"),
        "tab_id": resource.get("tab_id") or "",
        "window_id": resource.get("window_id") or "",
        "kind": "web",
        "title": resource.get("title") or "",
        "target": resource.get("target") or "",
        "status": status,
        "control_state": control_state,
        "generation": int(resource.get("generation") or 1),
        "sequence": int(resource.get("sequence") or 0),
        "source": "browser",
        "last_operation": operation,
    }


def _is_conversation_node(node) -> bool:
    from openprogram.context.nodes import ROLE_CODE, ROLE_LLM, ROLE_USER
    from openprogram.store.session.session_store import _node_caller

    metadata = node.metadata or {}
    if metadata.get("display") in {"root", "runtime"}:
        return False
    if metadata.get("function") == "attach":
        return False
    if str(node.name or "").startswith("context/"):
        return False
    if (
        node.role == ROLE_CODE
        and (_node_caller(node) or "ROOT") == "ROOT"
        and bool(node.name or metadata.get("function"))
    ):
        return True
    if node.role not in {ROLE_USER, ROLE_LLM}:
        return False
    caller = _node_caller(node)
    if caller and caller != "ROOT":
        return False
    return True


def _load_nodes(session_id: str, session_store=None):
    if session_store is None:
        from openprogram.agent.session_db import default_db
        session_store = default_db()
    from openprogram.store import SessionNodeWriter
    return SessionNodeWriter(session_store, session_id).load().nodes, session_store


def _children_by_predecessor(nodes: Mapping[str, Any]):
    children: dict[str, list] = {}
    for node in nodes.values():
        if not _is_conversation_node(node):
            continue
        predecessor = node.predecessor or "ROOT"
        children.setdefault(predecessor, []).append(node)
    for key, items in children.items():
        items.sort(key=lambda node: (
            node.seq if getattr(node, "seq", -1) >= 0 else 10**18,
            getattr(node, "created_at", 0) or 0,
            node.id,
        ))
        children[key] = items
    return children


def _origin_id(nodes: Mapping[str, Any], node_id: str | None) -> str | None:
    if not node_id or node_id == "ROOT":
        return None
    children = _children_by_predecessor(nodes)
    path = []
    seen = set()
    current = node_id
    while current and current not in seen and current != "ROOT":
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            break
        path.append(node)
        predecessor = node.predecessor
        current = predecessor if predecessor and predecessor != "ROOT" else None
    path.reverse()
    if not path:
        return None
    origin = path[0].id
    for node in path:
        siblings = children.get(node.predecessor or "ROOT") or []
        if siblings and siblings[0].id != node.id:
            origin = node.id
    return origin


def _ref_for_origin(session_store, session_id: str, origin: str, nodes, anchor: str) -> str | None:
    pair = session_store._open(session_id) if session_store is not None else None
    if pair is None:
        return None
    refs = dict((pair[1].meta or {}).get("branch_refs") or {})
    matches = []
    for ref_id, ref in refs.items():
        if not isinstance(ref, dict):
            continue
        head = ref.get("head_id")
        if not head or _origin_id(nodes, head) != origin:
            continue
        if head == anchor or _is_ancestor(nodes, anchor, head):
            matches.append((ref_id, head))
    if not matches:
        return None
    matches.sort(key=lambda item: 0 if item[1] == anchor else 1)
    return matches[0][0]


def _is_ancestor(nodes, ancestor_id: str, descendant_id: str) -> bool:
    seen = set()
    current = descendant_id
    while current and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            return False
        current = node.predecessor
        if current == "ROOT":
            return False
    return False


def _entry_branch_name(info) -> str | None:
    label = info.get("name") if isinstance(info, dict) else info
    if isinstance(label, str) and label.strip():
        return label.strip()
    return None


def _named_branch_entries(session_store, session_id: str, nodes: Mapping[str, Any]):
    named = {}
    try:
        session = session_store.get_session(session_id) or {}
    except Exception:
        session = {}
    raw = session.get("branches")
    if isinstance(raw, dict):
        named.update(raw)
    if named:
        return named
    for node_id in nodes:
        try:
            meta = session_store.get_branch_meta(session_id, node_id)
        except Exception:
            continue
        if meta:
            named[node_id] = meta
    try:
        for tip in session_store.list_branches(session_id):
            head = tip.get("head_msg_id")
            if head and head not in named:
                named[head] = tip
    except Exception:
        _log.debug("browser branch listing unavailable session_id=%s", session_id, exc_info=True)
    return named


def _branch_name_for_origin(session_store, session_id: str, origin: str | None, nodes):
    if not origin or session_store is None:
        return None
    matches = []
    for head_id, info in _named_branch_entries(session_store, session_id, nodes).items():
        name = _entry_branch_name(info)
        if not name or _origin_id(nodes, head_id) != origin:
            continue
        updated = 0.0
        if isinstance(info, dict):
            updated = float(info.get("updated_at") or info.get("created_at") or 0)
        seq = getattr(nodes.get(head_id), "seq", -1)
        matches.append((updated, seq if seq is not None else -1, str(head_id), name))
    if not matches:
        return None
    matches.sort()
    return matches[-1][3]


def resolve_stable_branch(session_id: str, anchor_id: str | None, *, session_store=None):
    if not session_id or not anchor_id:
        return None, None
    nodes, session_store = _load_nodes(session_id, session_store)
    origin = _origin_id(nodes, anchor_id)
    if not origin:
        return None, None
    ref = _ref_for_origin(session_store, session_id, origin, nodes, anchor_id)
    branch_id = ref or f"{session_id}:{origin}"
    return branch_id, _branch_name_for_origin(session_store, session_id, origin, nodes)


def current_branch(session_id: str, *, session_store=None):
    if session_store is None:
        from openprogram.agent.session_db import default_db
        session_store = default_db()
    pair = session_store._open(session_id)
    if pair is None:
        return None, None
    meta = pair[1].meta or {}
    active = meta.get("active_branch_id")
    refs = meta.get("branch_refs") or {}
    if isinstance(active, str) and isinstance(refs, dict) and active in refs:
        head = (refs.get(active) or {}).get("head_id")
        nodes, session_store = _load_nodes(session_id, session_store)
        origin = _origin_id(nodes, head)
        return active, _branch_name_for_origin(
            session_store, session_id, origin, nodes,
        )
    session = session_store.get_session(session_id) or {}
    head = session.get("head_id") or pair[1].head_id
    return resolve_stable_branch(session_id, head, session_store=session_store)


def _current_attribution():
    session_id = None
    execution_id = None
    try:
        from openprogram.processes import current_owner
        session_id, execution_id, _ = current_owner()
    except Exception:
        from openprogram.agent.run_control import get_current_execution_id, get_current_session_id
        session_id = get_current_session_id()
        execution_id = get_current_execution_id()
    user_message_id = assistant_message_id = agent_name = None
    if execution_id:
        try:
            from openprogram.execution import default_store
            store = default_store()
            record = store.get_execution_input(execution_id)
            if record is not None:
                user_message_id = record.user_message_id
                assistant_message_id = record.assistant_message_id
            display = None
            try:
                from openprogram.execution.public import _display_metadata
                execution = store.get_execution(execution_id)
                display = _display_metadata(store, execution, None) if execution else None
            except Exception:
                display = None
            if display:
                agent_name = display.get("label")
                user_message_id = user_message_id or display.get("user_message_id")
                assistant_message_id = assistant_message_id or display.get("assistant_message_id")
        except Exception:
            _log.debug("browser execution attribution metadata unavailable execution_id=%s",
                       execution_id, exc_info=True)
    return {
        "session_id": session_id,
        "execution_id": execution_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "agent_name": agent_name,
        "conversation_session_id": session_id,
    }


def attribute_operating_page(page_key: str) -> None:
    """Retain the current invocation's association. Operating/active is set
    only from an admitted act dispatch receipt. Does not publish native
    window/tab or title/URL."""
    if not page_key:
        return
    attribution = _current_attribution()
    if not attribution.get("execution_id") and not attribution.get("session_id"):
        return
    BrowserResourceStore().retain(page_key=page_key, live=True, **attribution)


def publish_bound_page(
    page_key: str,
    *,
    window_id: str = "",
    tab_id: str = "",
    connection_generation: int = 0,
    title: str = "",
    target: str = "",
) -> None:
    """Write trusted binding identity and controller display in one retain."""
    if not page_key:
        return
    BrowserResourceStore().retain(
        page_key=page_key, window_id=window_id, tab_id=tab_id,
        title=title, target=target,
        connection_generation=int(connection_generation or 0),
        live=True, **_current_attribution(),
    )


def retain_from_binding(
    binding_id: str, window_id: str, tab_id: str, target_id: str,
    connection_generation: int, title: str = "", target: str = "",
) -> None:
    from openprogram.webui.ws_actions import webtab
    page_key = webtab.binding_page_key(binding_id)
    if not page_key:
        page_key = f"page:{target_id}"
    try:
        BrowserResourceStore().retain(
            page_key=page_key, window_id=window_id, tab_id=tab_id,
            title=title, target=target, connection_generation=connection_generation,
            live=True, **_current_attribution(),
        )
    except Exception:
        _log.warning("browser resource retain unavailable: %s", target_id)


def mark_binding_unavailable(binding_id: str | None = None, *, page_key: str = "") -> None:
    key = page_key
    if not key and binding_id:
        from openprogram.webui.ws_actions import webtab
        key = webtab.binding_page_key(binding_id)
    if not key:
        return
    try:
        BrowserResourceStore().mark_unavailable(key)
    except Exception:
        _log.warning("browser resource unavailable mark failed")


def sanitize_operation(
    *, action: str, arguments: Mapping[str, Any] | None, result: Any,
    frame_id: str | None = None, geometry_revision: int | None = None,
    phase: str, operation_id: str | None = None, viewport: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    arguments = dict(arguments or {})
    point = None
    viewport_width = viewport_height = None
    if isinstance(viewport, Mapping):
        try:
            viewport_width = float(viewport.get("width"))
            viewport_height = float(viewport.get("height"))
        except (TypeError, ValueError):
            viewport_width = viewport_height = None
        if not viewport_width or not viewport_height or viewport_width <= 0 or viewport_height <= 0:
            viewport_width = viewport_height = None
    if action == "click" and viewport_width and viewport_height:
        receipt_point = result.get("point") if isinstance(result, dict) else None
        loc_x = loc_y = None
        if isinstance(receipt_point, dict) and {"x", "y"} <= set(receipt_point):
            try:
                left = float(receipt_point["x"])
                top = float(receipt_point["y"])
                box_w = float(receipt_point.get("width") or 0)
                box_h = float(receipt_point.get("height") or 0)
                loc_x = left + box_w / 2 if box_w > 0 else left
                loc_y = top + box_h / 2 if box_h > 0 else top
            except (TypeError, ValueError):
                loc_x = loc_y = None
        elif arguments.get("x") is not None and arguments.get("y") is not None:
            try:
                loc_x, loc_y = float(arguments["x"]), float(arguments["y"])
            except (TypeError, ValueError):
                loc_x = loc_y = None
        if loc_x is not None and loc_y is not None:
            point = {
                "x": loc_x, "y": loc_y,
                "width": viewport_width, "height": viewport_height,
            }
    error = None
    if isinstance(result, dict) and result.get("ok") is False:
        error = str(result.get("reason_code") or "failed")[:120]
    payload = {
        "id": operation_id or ("op_" + uuid.uuid4().hex[:12]),
        "action": str(action or "")[:64],
        "phase": phase,
        "timestamp": time.time(),
    }
    if frame_id:
        payload["frame_id"] = str(frame_id)[:120]
    if geometry_revision is not None:
        payload["geometry_revision"] = int(geometry_revision)
    if point is not None:
        payload["point"] = point
    if error:
        payload["error"] = error
    return payload


def report_browser_operation(
    page_key: str, operation: Mapping[str, Any], *, follow: bool,
    execution_id: str | None = None,
) -> None:
    if not page_key:
        return
    store = BrowserResourceStore()
    store.record_operation(page_key, operation)
    store.bump_sequence(page_key)
    if follow:
        store.set_control_state(page_key, "active")
    associations = store.associations_for_page(page_key)
    row = next(
        (item for item in associations if item.get("execution_id") == execution_id),
        associations[0] if associations else None,
    )
    conversation = (row or {}).get("conversation_session_id") or (row or {}).get("session_id") or ""
    public = None
    if conversation:
        try:
            rows, _, _ = project_conversation_resources(conversation)
            public = next(
                (
                    item for item in rows
                    if item["resource_id"] == page_key
                    and item.get("execution_id") == execution_id
                ),
                None,
            )
            if public is None:
                public = next(
                    (
                        item for item in rows
                        if item["resource_id"] == page_key
                        and (
                            not execution_id
                            or item.get("execution_id") == (row or {}).get("execution_id")
                        )
                    ),
                    None,
                )
        except Exception:
            public = None
    resource = store.get_resource(page_key)
    if public is None and resource is not None:
        public = _public_row(
            resource, row or {"session_id": None, "execution_id": execution_id, "agent_name": None},
            resource, conversation, None, None,
        )
    if public is None:
        return
    public["last_operation"] = dict(operation)
    emit_browser_resource(public, page_key=page_key)


def emit_browser_resource(row: Mapping[str, Any], *, page_key: str) -> None:
    from openprogram.webui.ws_actions import webtab
    from openprogram.webui import server as _s
    payload = json.dumps({"type": "browser.resource", "data": dict(row)}, default=str)
    owners = []
    window_id = row.get("window_id")
    with webtab._lock:
        for entry in webtab._bindings.values():
            if webtab.page_key_for_revision(entry[5]) == page_key:
                owners.append(entry[0])
        if window_id:
            for ws, wid in webtab._desktop_windows.items():
                if wid == window_id:
                    owners.append(ws)
    seen = set()
    loop = getattr(_s, "_loop", None)
    from openprogram.webui.ws_delivery import send_to_connection
    for ws in owners:
        if ws in seen:
            continue
        seen.add(ws)
        try:
            send_to_connection(ws, payload, loop)
        except Exception:
            _log.warning("browser.resource emit failed")


def project_conversation_resources(conversation_session_id: str) -> tuple[list[dict[str, Any]], str | None, str | None]:
    from openprogram.execution import default_store
    from openprogram.execution.conversation_scope import (
        conversation_executions, conversation_parent_ids,
    )
    from openprogram.agent.session_db import default_db

    store = default_store()
    executions = list(conversation_executions(store, conversation_session_id)) if store else []
    parents = conversation_parent_ids(store, conversation_session_id) if store else {}
    inputs = {}
    if store is not None:
        for item in executions:
            try:
                record = store.get_execution_input(item.execution_id)
            except Exception:
                record = None
            if record is not None:
                inputs[item.execution_id] = record
    session_store = default_db()
    rows = BrowserResourceStore().list_rows(
        conversation_session_id, executions=executions, parents=parents,
        session_store=session_store, execution_inputs=inputs,
    )
    current_id, current_name = current_branch(
        conversation_session_id, session_store=session_store,
    )
    return rows, current_id, current_name


def _wait_open_paused(execution) -> bool:
    if execution is None:
        return False
    status = getattr(getattr(execution, "status", None), "value", None) or str(
        getattr(execution, "status", "") or ""
    )
    return status == "paused" and getattr(execution, "reason_code", None) == "wait_open"


def _control_state_for_execution(execution, unresolved: bool, fenced: bool) -> str:
    if execution is None:
        return "yielding" if fenced else "idle"
    status = getattr(getattr(execution, "status", None), "value", None) or str(
        getattr(execution, "status", "") or ""
    )
    if _wait_open_paused(execution):
        return "waiting"
    if status == "paused" and not unresolved:
        return "paused"
    if status == "paused" and unresolved:
        return "yielding"
    if status in {"pausing"} or (fenced and status == "running"):
        return "yielding"
    if fenced:
        return "stop_unconfirmed"
    return "idle"


def _active_execution(execution) -> bool:
    status = getattr(execution, "status", None)
    value = getattr(status, "value", status)
    if isinstance(value, str):
        return value not in {"completed", "failed", "cancelled", "interrupted"}
    return status is not None


def _status_value(execution) -> str:
    status = getattr(execution, "status", None)
    value = getattr(status, "value", status)
    return str(value or "")


def _inflight_pause_command(resource: Mapping[str, Any] | None) -> str | None:
    if not resource:
        return None
    pending = resource.get("pause_command_id")
    if pending and (resource.get("control_state") or "") in {
        "yielding", "paused", "stop_unconfirmed",
    }:
        return str(pending)
    return None


def _page_admission_state(page_key: str) -> tuple[bool, bool]:
    """Return (has_live_write_lease, has_inflight_primitive) for one Page."""
    from openprogram.programs.workflow.browser.web_use_runtime import get_registry
    registry = get_registry()
    with registry._lock:
        session_id = registry._page_leases.get(page_key, "")
        session = registry._sessions.get(session_id)
        if session is None or session.closed or session.closing:
            return False, False
        inflight = int(getattr(session, "inflight_ops", 0) or 0) > 0
        return True, inflight


def _unresolved_effects(execution_id: str) -> tuple[bool, bool]:
    """Return (has_unresolved, lookup_failed). Lookup errors are fail-closed."""
    if not execution_id:
        return False, False
    try:
        from openprogram.execution import default_control_service
        return bool(default_control_service().effects.list_unresolved(execution_id)), False
    except Exception:
        return True, True


def reconcile_resource_control(
    resource_id: str,
    *,
    store: BrowserResourceStore | None = None,
    executions_by_id: Mapping[str, Any] | None = None,
) -> None:
    """Persist GET/resume control_state from Runtime + lease/effect checks.

    Sequence bumps only when the stored state actually changes. Paused is
    stored only after Runtime paused and unresolved-effect lookup succeeds
    with an empty set. A terminal owner becomes idle only when there is no
    live write lease, no inflight primitive, and no unresolved/unknown
    effects. Failed effect lookup is never treated as a clean release.
    """
    if not resource_id:
        return
    store = store or BrowserResourceStore()
    resource = store.get_resource(resource_id)
    if resource is None:
        return
    if not resource.get("live") or (resource.get("lifecycle") or "") in {
        "unavailable", "closed",
    }:
        return
    stored = resource.get("control_state") or "idle"
    fenced = writes_fenced(resource_id)
    known = dict(executions_by_id or {})
    live_owners = []
    terminal = []
    missing_owner = False
    for execution_id in _lease_execution_ids(resource_id):
        execution = known.get(execution_id)
        if execution is None:
            try:
                from openprogram.execution import default_store
                db = default_store()
                execution = db.get_execution(execution_id) if db is not None else None
            except Exception:
                execution = None
        if execution is None:
            missing_owner = True
            continue
        if _active_execution(execution):
            live_owners.append(execution)
        else:
            terminal.append(execution)
    isolation_failed = False
    live_lease = inflight = False
    try:
        live_lease, inflight = _page_admission_state(resource_id)
    except Exception:
        isolation_failed = True
        live_lease = inflight = True
    unresolved = False
    effects_unknown = False
    for execution in (*live_owners, *terminal):
        has_unresolved, failed = _unresolved_effects(
            getattr(execution, "execution_id", "") or "",
        )
        unresolved = unresolved or has_unresolved
        effects_unknown = effects_unknown or failed
    runtime_paused = any(_status_value(item) == "paused" for item in live_owners)
    runtime_wait_open = any(_wait_open_paused(item) for item in live_owners)
    runtime_pausing = any(_status_value(item) == "pausing" for item in live_owners)
    runtime_running = any(_status_value(item) == "running" for item in live_owners)
    target = stored
    clear_fence = False
    clear_pause = False
    if runtime_wait_open:
        target = "waiting"
        clear_pause = True
    elif runtime_pausing or (fenced and runtime_running):
        target = "yielding"
    elif runtime_paused and not unresolved and not effects_unknown:
        target = "paused"
    elif runtime_paused and effects_unknown:
        target = "stop_unconfirmed"
    elif runtime_paused and unresolved:
        target = "yielding"
    elif (unresolved or effects_unknown) and (
        fenced or stored in {"yielding", "paused", "stop_unconfirmed"}
    ):
        target = "stop_unconfirmed"
    elif missing_owner and (
        fenced or stored in {"yielding", "paused", "stop_unconfirmed"}
    ):
        target = "stop_unconfirmed" if stored == "paused" else (
            stored if stored in {"yielding", "stop_unconfirmed"} else "yielding"
        )
    elif (
        not live_owners
        and not live_lease
        and not inflight
        and not unresolved
        and not effects_unknown
        and not isolation_failed
        and not missing_owner
        and (fenced or stored in {"yielding", "paused", "stop_unconfirmed", "active"})
    ):
        target = "idle"
        clear_fence = True
        clear_pause = True
    if (
        target == stored
        and not (clear_fence and fenced)
        and not (clear_pause and resource.get("pause_command_id"))
    ):
        return
    store.set_control_state(
        resource_id, target, clear_pause=clear_pause, bump=(target != stored),
    )
    if clear_fence:
        clear_page_write_fence(resource_id)


def _lease_execution_ids(resource_id: str) -> list[str]:
    ids = []
    try:
        from openprogram.programs.workflow.browser.web_use_runtime import get_registry
        registry = get_registry()
        with registry._lock:
            for session in registry._sessions.values():
                if session.page_key == resource_id and not session.closed and not session.closing:
                    execution_id = (session.state or {}).get("execution_id")
                    if execution_id:
                        ids.append(str(execution_id))
    except Exception:
        _log.debug("browser lease registry lookup failed resource_id=%s", resource_id, exc_info=True)
    try:
        resource = BrowserResourceStore().get_resource(resource_id)
        controller = (resource or {}).get("pause_execution_id")
        if controller:
            ids.append(str(controller))
    except Exception:
        _log.debug("browser resource controller lookup failed resource_id=%s", resource_id, exc_info=True)
    if ids:
        return list(dict.fromkeys(ids))
    try:
        for assoc in BrowserResourceStore().associations_for_page(resource_id):
            if assoc.get("execution_id"):
                ids.append(str(assoc["execution_id"]))
    except Exception:
        _log.debug("browser resource associations lookup failed resource_id=%s", resource_id, exc_info=True)
    return list(dict.fromkeys(ids))


async def _submit_owner_command(
    *, operation: str, command_id: str, execution, actor, conversation_session_id: str,
):
    from openprogram.webui.ws_actions.runtime import submit_execution_control

    command, result = await submit_execution_control(
        {
            "type": "execution.command",
            "action": f"execution.{operation}",
            "command_id": command_id,
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
            "payload": {},
        },
        operation,
        actor=dict(actor) if isinstance(actor, Mapping) else actor,
        bound_session=None,
        surface="browser-resource",
        conversation_session_id=conversation_session_id,
    )
    if isinstance(command, dict) and command.get("status") == "rejected":
        raise PermissionError(str(command.get("rejection_code") or "command_rejected"))
    return result


async def request_page_pause(
    *, resource_id: str, command_id: str, actor: Mapping[str, Any] | None,
    conversation_session_id: str,
):
    from openprogram.execution import default_store
    from openprogram.execution.conversation_scope import authorize_conversation_execution
    from openprogram.execution.model import ExecutionStatus

    store = BrowserResourceStore()
    reconcile_resource_control(resource_id, store=store)
    resource = store.get_resource(resource_id) or {}
    pending = _inflight_pause_command(resource)
    if pending:
        command_id = pending
    executions = default_store()
    last = None
    owner_ids = _lease_execution_ids(resource_id)
    if not owner_ids:
        owner_ids = [
            assoc.get("execution_id") for assoc in store.associations_for_page(resource_id)
            if assoc.get("execution_id")
        ]
    for execution_id in owner_ids:
        if not execution_id or executions is None:
            continue
        execution = executions.get_execution(execution_id)
        if execution is None or not _active_execution(execution):
            continue
        authorize_conversation_execution(
            actor or {}, "execution.pause", execution, store=executions,
            session_id=conversation_session_id, bound_session=None,
        )
        status = execution.status
        if status in {ExecutionStatus.PAUSING, ExecutionStatus.PAUSED}:
            last = execution
            continue
        if not getattr(execution.capabilities, "pause", False):
            continue
        last = await _submit_owner_command(
            operation="pause", command_id=command_id, execution=execution,
            actor=actor, conversation_session_id=conversation_session_id,
        )
        if hasattr(last, "status"):
            execution = last
        elif isinstance(last, dict):
            last = executions.get_execution(execution_id)
    return last


async def request_page_resume(
    *, resource_id: str, command_id: str, actor: Mapping[str, Any] | None,
    conversation_session_id: str,
):
    from openprogram.execution import default_control_service, default_store
    from openprogram.execution.conversation_scope import authorize_conversation_execution
    from openprogram.execution.model import ExecutionStatus

    store = BrowserResourceStore()
    reconcile_resource_control(resource_id, store=store)
    resource = store.get_resource(resource_id)
    if resource is None or not resource.get("live"):
        raise PermissionError("disconnected")
    executions = default_store()
    from openprogram.execution import default_control_service
    owners = []
    wait_open = (resource.get("control_state") or "") == "waiting"
    owner_ids = _lease_execution_ids(resource_id) or [
        assoc.get("execution_id") for assoc in store.associations_for_page(resource_id)
        if assoc.get("execution_id")
    ]
    for execution_id in owner_ids:
        if not execution_id or executions is None:
            continue
        execution = executions.get_execution(execution_id)
        if execution is None or not _active_execution(execution):
            continue
        if _wait_open_paused(execution):
            wait_open = True
            continue
        authorize_conversation_execution(
            actor or {}, "execution.continue", execution, store=executions,
            session_id=conversation_session_id, bound_session=None,
        )
        if execution.status is not ExecutionStatus.PAUSED:
            raise PermissionError("not_paused")
        try:
            unresolved = default_control_service().effects.list_unresolved(execution_id)
        except Exception as exc:
            raise PermissionError("unresolved_effect") from exc
        if unresolved:
            raise PermissionError("unresolved_effect")
        owners.append(execution)
    if wait_open:
        raise PermissionError("wait_open")
    if (resource.get("control_state") or "") != "paused":
        raise PermissionError("not_paused")
    from openprogram.programs.workflow.browser.web_use_runtime import get_registry
    get_registry().invalidate_page_frames(resource_id)
    last = None
    try:
        for execution in owners:
            last = await _submit_owner_command(
                operation="continue", command_id=command_id, execution=execution,
                actor=actor, conversation_session_id=conversation_session_id,
            )
    except Exception:
        raise
    clear_page_write_fence(resource_id)
    store.set_control_state(resource_id, "idle", clear_pause=True)
    return last


async def apply_resource_control(
    *, conversation_session_id: str, resource_id: str, action: str,
    command_id: str, generation: int, actor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raise ValueError("Use the conversation pause or continue control.")


def page_keys_for_socket_tab(ws, window_id: str, tab_id: str) -> list[str]:
    """Live binding keys plus live retained descriptors for this socket tab.

    Ownership is the registered desktop window on this socket. Unregistered
    or foreign sockets never consult persisted rows. Closed/unavailable
    records do not grant a write lease.
    """
    from openprogram.webui.ws_actions import webtab
    with webtab._lock:
        if webtab._desktop_windows.get(ws) != window_id:
            return []
        connection_revision = webtab._connection_revisions.get(ws)
        if connection_revision is None:
            return []
        keys = [
            webtab.page_key_for_revision(entry[5])
            for entry in webtab._bindings.values()
            if entry[0] is ws and entry[1] == window_id and entry[2] == tab_id
        ]
    try:
        store = BrowserResourceStore()
        for resource_id in store.page_keys_for_tab(window_id, tab_id):
            row = store.get_resource(resource_id)
            if row is None:
                continue
            if not int(row.get("live") or 0):
                continue
            if (row.get("lifecycle") or "") in {
                "closed", "unavailable", "restoring", "restore_failed", "superseded",
            }:
                continue
            if (row.get("control_state") or "") == "closed":
                continue
            if int(row.get("connection_generation") or 0) != int(connection_revision):
                continue
            keys.append(resource_id)
    except Exception:
        _log.debug("browser page key projection unavailable window_id=%s tab_id=%s",
                   window_id, tab_id, exc_info=True)
    return list(dict.fromkeys(key for key in keys if key))


async def handle_human_page_input(*, ws, window_id: str, tab_id: str, input_seq: int, kind: str = "pointer") -> None:
    """Ignore legacy desktop page-input messages; only task controls pause execution."""
    return None
