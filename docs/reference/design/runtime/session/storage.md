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
