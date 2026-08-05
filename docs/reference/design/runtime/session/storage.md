# Session Data Model

## On-Disk Layout

```
<state>/sessions/
├── index.json                    # registry (summary cache for all sessions)
├── <session_id_1>/
│   ├── meta.json                 # metadata
│   └── history/                  # message DAG (Git repository)
├── <session_id_2>/
│   └── ...
```

## Persistent Fields (meta.json)

| Field | Type | Registry | Description |
|------|------|--------|------|
| `id` | str | Yes | unique session identifier |
| `agent_id` | str | Yes | the bound agent |
| `title` | str | Yes | display name |
| `created_at` | float | Yes | creation timestamp |
| `updated_at` | float | Yes | last-activity timestamp |
| `project_id` | str? | No | the bound project (supplemented with the `project` name by project_map when listing) |
| `source` | str? | Yes | origin: "tui" / "web" / "wechat" / ... |
| `channel` | str? | Yes | channel type |
| `account_id` | str? | Yes | channel account |
| `peer_display` | str? | Yes | peer display name |
| `peer_id` | str? | Yes | peer ID |
| `pinned` | bool | Yes | pinned |
| `archived` | bool | Yes | archived |
| `group` | str? | Yes | group label |
| `status` | str | Yes | lifecycle status (see below) |
| `unread` | bool | Yes | unread marker |
| `_auto_titled` | bool | No | auto-naming idempotency marker (internal control; not stored in the registry, not returned to the frontend) |

The "Registry" column indicates whether the field is cached in `index.json`. `_auto_titled` and `project_id` are not stored in the registry: the former is an internal marker, and the latter is supplemented from the project directory mapping when listing.

## Registry-Only Fields

The following fields exist only in the registry, not in meta.json:

| Field | Description |
|------|------|
| `preview` | the first 80 characters of the last user message, maintained by truncation when a message is written |

## status Enum

| Value | Meaning | Frontend Display |
|----|------|----------|
| `idle` | idle, no turn executing | no indicator |
| `running` | a turn is executing | running animation |
| `needs_input` | the agent is waiting for user input | amber dot |
| `done` | background task finished | blue dot shown together with `unread` |
| `failed` | turn execution failed | red dot |
| `interrupted` | the worker died mid-turn | no indicator (not run-active) |

`running` is stamped by the dispatcher when a turn starts and cleared
when it ends. A worker killed mid-turn (SIGKILL, crash) never runs that
clear, so the row would stay `running` forever and pin the chat container
at `data-run-active="true"` with no way out short of editing state on
disk. `reconcile_interrupted_runs()` therefore resets any row still at
`running` to `interrupted` on worker startup — a fresh worker has nothing
running by definition. It resets the row independently of the DAG-node
sweep in the same function, because a worker killed between the status
write and the placeholder insert leaves a running *row* with no running
*node*.

## Moving HEAD: `_set_active_head`

`webui/server.py` keeps a per-session mirror in `_sessions[sid]` holding
`head_id` and `messages`, and `_save_session` flushes both straight back
into SessionStore. A path that moves HEAD in the store but leaves the
mirror behind is therefore not merely stale — **the next save actively
reverts the move.**

`_set_active_head(session_id, head_id)` is the single correct way to move
HEAD. It writes SessionStore, re-reads the new branch into the mirror's
`head_id` and `messages`, and drops the message cache, in that order.
Every mutating path routes through it: retry, edit, sibling checkout,
deepest-leaf jump, branch checkout, branch delete, attach, and rewind.

Mutations that move HEAD are refused while a run is in flight
(`_is_run_active`), returning `RUN_ACTIVE_ERROR` with `code:
"run_active"`. Without that guard, an in-flight reply lands with its
predecessor pointing at a branch the user already left; branch delete is
worse still, since the tail being deleted may be the one the turn is
writing into.

## Non-Persistent Objects (`_sessions` dict)

Non-serializable objects such as the agent runtime and WebSocket connection are stored in the in-process `_sessions` dict, keyed by session id:

| Key | Type | Description |
|----|------|------|
| `runtime` | AgentRuntime? | LLM connection, session state |
| `ws` | WebSocket? | the currently connected WebSocket |
| `agent` | Agent? | the agent instance |

Goal: all persistent fields are read and written through SessionStore, with no redundancy in `_sessions`.

> **Current state**: `_sessions` still redundantly holds persistent fields such as title, agent_id, created_at, and channel, because `_save_session` reads all fields from the dict to write meta.json. `run_active` has been removed (replaced by the status field). Fully slimming this down requires rewriting `_save_session` to read persistent fields from SessionStore — left for later.

## Interface

```python
class SessionStore:
    def create_session(session_id, agent_id, *, title="", source=None, **meta) -> None
    def get_session(session_id) -> dict | None
    def update_session(session_id, **fields) -> None
    def delete_session(session_id) -> None
    def list_sessions(*, limit=100, offset=0, **filters) -> list[dict]
    def get_branch(session_id, head_id=None) -> list[dict]
    def append_message(session_id, msg) -> None
    def latest_user_text(session_id) -> str | None
```

See [operations.md](operations.md) for the full behavior of each method.
