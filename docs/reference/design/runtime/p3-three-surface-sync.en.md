# P3 — Three-Surface Sync (filling gaps) + P1/P2 onto all three surfaces

After investigation, a correction: the foundation for three-surface sync is far more complete than expected, and the gap is narrow.

## Already in place (no work needed)

| Capability | Current state | file:line |
|---|---|---|
| Single worker + shared git SessionDB | All three surfaces connect to the same worker and read the same source of truth | worker/lock.py, agent/session_db.py |
| running status flag | `_running_tasks` registry + `running_task`/`running_task_clear` broadcasts (drive the sidebar pulse indicator + composer status) | webui/server.py:187-224 |
| Backfill history on reconnect | `handle_sync(session_id, known_seqs)` re-sends the missing message frames | ws_actions/runtime.py:436-450 |
| Event broadcast | EventBus + _broadcast; a single emit reaches all WS clients | event_bus.py, server.py `_broadcast` |

→ A turn started on one surface is **already** received as the same stream events on the others; reconnect **already** backfills history + the running indicator. So "one surface did something the others can't see" essentially does not hold on the webui/TUI paths (both go through the worker WS).

## The gap (what P3 must do)

### G1 — Reconnect does not backfill running_task state
`handle_sync` only backfills message frames; it does not backfill the current `running_task` indication. A new or reconnecting client has to wait for the next `_emit_running_task_event` before it sees the "running" state.
**Fix**: at the end of `handle_sync`, call `_emit_running_task_event` once for that session (or send the current running_task snapshot directly to this ws). A one-line-level change.

### G2 — Bring the P1 attended switch to all three surfaces (WS action + session state)
attended is currently a process-level flag (used by the CLI). To let the TUI/web toggle it too, we need:
- A WS action `{action:"set_attended", session_id, attended: bool}` → call `attended.set_attended` + broadcast an `attended_changed` status frame, so all three surfaces show the current mode in sync.
- Note: attended is currently **process-global**, not per-session. In the webui single-worker, multi-session case, a global switch makes sessions interfere with each other. **Decision**: in P3, change attended to **session-level** (dict[session_id]→bool); the CLI passes its own session.
- Frontend: a toggle key + status display in the TUI; a toggle button in web.

### G3 — Bring the P2 steer onto all three surfaces (WS action)
steer currently only has a CLI subcommand (writes to a file inbox). Add:
- A WS action `{action:"steer", session_id, message}` → `steering.push(session_id, message)` (already a file inbox: the worker writes, the running process reads) + broadcast an acknowledgement frame.
- Subprocess case: research_agent runs in a subprocess, and the file inbox uses the same session_dir, so the subprocess can read it — **no extra IPC needed** (files cross processes naturally). This is simpler than expected.
- Frontend: a "send steering instruction" input box in the TUI/web (available while a task is running).

## Order
G1 (one line) → G2 (make attended session-level + WS action + frontend toggle) → G3 (steer WS action + frontend input box).

## To be decided
- Make attended per-session: confirm we want this (with a single worker and multiple sessions, a global switch leaks across them). Default still unattended.
- Frontend changes: both frontends — TUI (Ink, cli/src) and web (web/) — need the toggle/input box added. This is where most of the work is. Do the backend WS action first (shared across all three surfaces, testable), then the frontend UI.
