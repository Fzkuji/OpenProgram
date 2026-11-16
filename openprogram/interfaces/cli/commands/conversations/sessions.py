"""``openprogram sessions`` handlers (list / resume / export / archive / import)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _cmd_session_archive(session_id: str, archived: bool) -> None:
    """Set or clear a chat session's archive flag.

    Archiving hides the session from the default list without deleting
    anything — ``unarchive`` puts it straight back, and the session's
    last-activity time is untouched either way. A running worker does the
    write, so its live session list and every open window update too;
    without one the flag is set in the store.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.host.security.backend_endpoint import OwnerAuthError, resolve_backend_endpoint
    verb = "Archived" if archived else "Unarchived"
    try:
        resolve_backend_endpoint()
    except OwnerAuthError:
        was_archived = bool((default_db().get_session(session_id) or {}).get("archived"))
        found = default_db().set_archived(session_id, archived)
        if found and archived != was_archived:
            from openprogram.runtime.conversation.always_on.runtime import archive_changed
            archive_changed(session_id, archived)
    else:
        from .execution import _worker_request
        status, payload = _worker_request(
            "POST", "/api/sessions/archive" if archived else "/api/sessions/unarchive",
            {"session_id": session_id},
        )
        if status not in (200, 404):
            print(f"[error] worker refused: {payload}")
            sys.exit(1)
        found = status == 200
    if not found:
        print(f"[error] no session {session_id!r} found.")
        sys.exit(1)
    print(f"{verb} session {session_id}")


def _cmd_chat_sessions(scope: str = "active") -> None:
    """List chat sessions. ``scope`` is active | archived | all."""
    from openprogram.runtime.context.session_db import default_db
    kwargs: dict = {"limit": 10_000}
    if scope == "archived":
        kwargs["archived"] = True
    elif scope == "all":
        kwargs["include_archived"] = True
    rows = default_db().list_sessions(**kwargs)
    if not rows:
        print(f"No {scope} chat sessions.")
        return
    print(f"Chat sessions ({scope}, {len(rows)}):\n")
    for r in rows:
        sid = r.get("id", "?")
        title = (r.get("title") or r.get("preview") or "").strip()
        flag = " [archived]" if r.get("archived") else ""
        print(f"  {sid}{flag}  {title[:70]}")


def _cmd_resume(session_id, answer):
    """Resume a waiting follow-up session."""
    from openprogram.runtime.agent.follow_up_session import Session
    session = Session(session_id)
    if not session.exists():
        print(json.dumps({"type": "error", "message": f"Session not found: {session_id}"}))
        sys.exit(1)
    meta = session.read_meta()
    if not meta:
        print(json.dumps({"type": "error", "message": f"Session metadata unreadable: {session_id}"}))
        sys.exit(1)
    session.send_answer(answer)
    print(json.dumps({"type": "ok", "message": f"Answer sent to session {session_id}"}))


def _cmd_sessions():
    """List active follow-up sessions."""
    from openprogram.runtime.agent.follow_up_session import list_sessions
    sessions = list_sessions()
    if not sessions:
        print("No active sessions.")
        return
    print(f"Active sessions ({len(sessions)}):\n")
    for s in sessions:
        sid = s.get("session_id", "?")
        q = s.get("question", "?")
        status = s.get("status", "?")
        print(f"  {sid}  [{status}]  {q[:80]}")
    print("\nResume with: agentic resume <session_id> \"your answer\"")


def _cmd_sessions_export(session_id, export_format="md", output=None):
    """Write a session out as a shareable Markdown or HTML file."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.reading.export import export_session

    store = default_db()
    if store.get_session(session_id) is None:
        print(f"[error] no session {session_id!r} found.", file=sys.stderr)
        sys.exit(1)

    document = export_session(session_id, export_format, store=store)
    target = Path(output) if output else Path(f"{session_id}.{export_format}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    print(f"Exported session {session_id} → {target}")


_IMPORT_BATCH = 8


def _cmd_sessions_import(paths, import_from="all", since_days=None, list_only=False,
                         dry_run=False):
    """Import Claude Code / Codex conversations, or list what is importable.

    ``dry_run`` prints what an import would do — a new session, or new
    turns appended to the one an earlier import made — and writes nothing.

    A running worker does the writes, in small batches so each request
    stays well inside the HTTP timeout and its sidebar refreshes as they
    land; without one the store is written directly.
    """
    import time
    from openprogram.state.store.session import external_import as ext
    from openprogram.host.security.backend_endpoint import OwnerAuthError, resolve_backend_endpoint

    agents = list(ext.AGENTS) if import_from == "all" else [import_from]
    since = time.time() - since_days * 86400 if since_days else None
    rows = ext.list_candidates(agents, since=since)
    if paths:
        wanted = {str(Path(p).expanduser().resolve()): p for p in paths}
        rows = [r for r in rows if str(Path(r["path"]).resolve()) in wanted]
        found = {str(Path(r["path"]).resolve()) for r in rows}
        for resolved, given in wanted.items():
            if resolved not in found:
                print(f"[warn] not a {' or '.join(ext.AGENT_LABELS[a] for a in agents)} "
                      f"conversation: {given}", file=sys.stderr)
    if list_only:
        if not rows:
            print("No Claude Code or Codex conversations found.")
            return
        print(f"{len(rows)} conversation(s):\n")
        for r in rows:
            day = time.strftime("%Y-%m-%d", time.localtime(r["updated_at"]))
            print(f"  [{r['state']:>8}] {r['agent_label']:<11} {day}  {r['title'][:60]}")
        return
    pending = [r for r in rows if r["state"] in ("new", "updated")]
    if not pending:
        print("Nothing new to import.")
        return
    todo = [r["path"] for r in pending]
    if dry_run:
        # Decided the way the import decides (a rewound source is a new
        # conversation, a source with no reply is skipped), read only.
        plans = [p for p in ext.plan_imports(agents, paths=todo)
                 if p["status"] in ("created", "appended", "failed")]
        if not plans:
            print("Nothing new to import.")
            return
        days = {r["path"]: r["updated_at"] for r in rows}
        labels = {r["path"]: r["agent_label"] for r in rows}
        print(f"Would import {len(plans)} conversation(s); nothing was imported:\n")
        for p in plans:
            action = {"created": "create", "appended": "append"}.get(p["status"], "fail")
            day = time.strftime("%Y-%m-%d", time.localtime(days.get(p["path"], 0)))
            extra = f": {p.get('error')}" if p["status"] == "failed" else f"  (+{p['turns_added']} turns)"
            print(f"  {action:>6}  {labels.get(p['path'], p['agent']):<11} {day}  {p['title'][:60]}{extra}")
            print(f"          {p['path']}")
        return

    try:
        resolve_backend_endpoint()
    except OwnerAuthError:
        def run(batch):
            return ext.import_transcripts(agents, paths=batch)
    else:
        from .execution import _worker_request

        def run(batch):
            status, payload = _worker_request(
                "POST", "/api/sessions/import", {"agents": agents, "paths": batch})
            if status != 200:
                print(f"[error] worker refused: {payload}", file=sys.stderr)
                sys.exit(1)
            return payload["results"]

    counts: dict[str, int] = {}
    for i in range(0, len(todo), _IMPORT_BATCH):
        for r in run(todo[i:i + _IMPORT_BATCH]):
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            if r["status"] in ("created", "appended"):
                print(f"  {r['status']:>8}  {r['title'][:60]}  (+{r['turns_added']} turns)")
            elif r["status"] == "failed":
                print(f"  {'failed':>8}  {r['title'][:60]}: {r.get('error')}", file=sys.stderr)
    print("\n" + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
