# Session Management Comparison

A comprehensive comparison of the session management mechanisms across four projects: Claude Code, OpenCode, OpenClaw, and OpenProgram (our design).

## 1. Storage Format

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| Storage medium | Filesystem (one JSONL per session) | SQLite database (single file `opencode.db`) | JSON registry (`sessions.json`) + one JSONL per session | Git repository (one directory per session) + JSON registry (`index.json`) |
| Metadata location | Mixed into the JSONL (`ai-title`, `custom-title`, `mode`, and other entries) | `sessions` table (10 fields) | The SessionEntry in `sessions.json` (around 70 fields) | `meta.json` (inside each session directory) + `index.json` (registry cache) |
| Message location | Same JSONL file (`user`, `assistant` entries) | Dedicated `messages` table + `parts` column (JSON array) | Dedicated `<id>.jsonl` transcript file | Git history (one file per message, DAG structure) |
| File snapshots | `file-history-snapshot` entries in the JSONL | Dedicated `files` table (path, content, version) | None | Git worktree (each session can have its own working directory) |
| Storage path | `~/.claude/projects/<slug>/<uuid>.jsonl` | `<data_dir>/opencode.db` | `<state>/agents/<id>/sessions/sessions.json` + `<id>.jsonl` | `<state>/sessions/<id>/` (meta.json + history/) + `<state>/sessions/index.json` |
| Metadata/message separation | Not separated, all in one file | Separated (different tables) | Separated (different files) | Separated (meta.json vs history/) |

## 2. Session Metadata Fields

| Field category | Claude Code | OpenCode | OpenClaw | OpenProgram |
|----------|------------|----------|---------|-------------|
| **id** | JSONL filename (UUID) | `sessions.id` (UUID) | `sessionId` | `id` (UUID) |
| **Title** | Two separate entries, `aiTitle` + `customTitle` | Single `sessions.title` field | `displayName` + `label` | Single `title` field |
| **Title priority** | `customTitle > aiTitle > summaryHint > firstPrompt > id` | Last value written | `displayName > label` | Last value written (truncation / LLM / manual — all three sources override equally) |
| **Created time** | timestamp of the first message in the JSONL | `sessions.created_at` | `startedAt` | `created_at` |
| **Updated time** | File mtime or sidecar | `sessions.updated_at` (trigger-driven) | `updatedAt` (written by the application layer) | `updated_at` (written by the application layer) |
| **Message count** | None | `sessions.message_count` (trigger-driven +1/-1) | None | None |
| **token stats** | None (present in `usage` but not aggregated) | `prompt_tokens` / `completion_tokens` / `cost` | `inputTokens` / `outputTokens` / `totalTokens` / `estimatedCostUsd` / `cacheRead` / `cacheWrite` / `contextTokens` | None (managed by the separate UsageLedger subsystem) |
| **Run status** | `status` in `~/.claude/sessions/<pid>.json` (idle/busy) | None | `status` (running/done/failed/killed/timeout) | `status` enum (idle/running/needs_input/done/failed, written by the dispatcher, reset on startup) |
| **Parent/child relationships** | None | `parent_session_id` (child sessions used for title generation and tasks) | `spawnedBy` / `parentSessionKey` / `spawnDepth` (0=main, 1=subagent, 2=sub-subagent) | None |
| **Channel/source** | None | None | `channel` / `groupId` / `origin` (includes label/provider/surface/chatType/from/to/nativeChannelId/accountId/threadId) + `lastChannel` / `lastTo` / `lastAccountId` / `lastThreadId` | `source` / `channel` / `account_id` / `peer_display` / `peer_id` |
| **Model/config** | `mode`, `permission-mode` entries in the JSONL | None (runtime state) | `providerOverride` / `modelOverride` / `modelOverrideSource` / `authProfileOverride` / `thinkingLevel` / `fastMode` / `verboseLevel`, etc. | Not persisted (held by the runtime object, not stored in meta.json) |
| **Compaction** | `system:compact_boundary` entry (preTokens/postTokens/preservedSegment) | `summary_message_id` points to the summary message | `compactionCount` / `compactionCheckpoints` array (each entry has tokensBefore/tokensAfter/summary) | compactionSummary message node (a special node in the DAG, source="compaction") |
| **Project binding** | Implicitly bound via the directory path (`projects/<slug>/`) | None | None (implicit via the agent directory) | `project_id` field + an in-project `.openprogram/sessions/` directory |
| **Git branch** | `gitBranch` field in the JSONL | None | None | DAG branches (`head_id` + branches map) |
| **Preview** | No dedicated field (firstPrompt extracted from the tail when listing) | None | None | `preview` (registry field, updated on `append_message`) |
| **Archive/pin/group** | None | None | None | `pinned` / `archived` / `group` |
| **Unread marker** | None | None | None | `unread` (set when a background run completes, cleared on open) |
| **Queue policy** | None | None | `queueMode` (7 modes: steer/followup/collect/interrupt, etc.) / `queueDebounceMs` / `queueCap` / `queueDrop` | None |
| **Subagent role** | None | None | `subagentRole` (orchestrator/leaf) / `subagentControlScope` | None |
| **Heartbeat** | None | None | `lastHeartbeatText` / `lastHeartbeatSentAt` / `heartbeatTaskState` | None |
| **Memory flush** | None | None | `memoryFlushAt` / `memoryFlushCompactionCount` / `memoryFlushContextHash` | None |
| **CLI binding** | None | None | `cliSessionIds` / `cliSessionBindings` / `claudeCliSessionId` | None |
| **Auto-naming marker** | None (inferred from whether a `customTitle` entry exists) | None (inferred from an `isDefaultTitle` regex) | None | `_auto_titled` (bool, idempotency marker for first-turn auto-naming) |

## 3. Session Listing

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Mechanism** | Scan directory + read file tails | SQL query | Read sessions.json | Read the index.json registry |
| **Index** | None | Database index | The registry itself | Registry (`index.json`) |
| **Detailed flow** | `readdir` → `stat` to get mtime → sort by mtime descending → batch-read tail content (32 per batch) → string-search to extract title and other fields | `SELECT * FROM sessions WHERE parent_session_id IS NULL ORDER BY created_at DESC` | `fs.readFileSync` → `JSON.parse` → return the entire `Record<string, SessionEntry>` | On startup, read `index.json` into memory → `list_sessions()` is a pure in-memory traversal |
| **Read volume** | Read tail content of each file (string search, no JSON.parse) | One SQL statement | One file | One file (on startup), pure in-memory thereafter |
| **Filtering** | Scans only the current project directory (implicit per-project filtering) | `WHERE parent_session_id IS NULL` (filters out child sessions) | None | No filtering (empty shells are prevented at the creation entry point) |
| **Sorting** | By file mtime descending | `ORDER BY created_at DESC` | The caller sorts on its own | In memory, by `updated_at` descending |
| **Pagination** | None | None | None | `limit` + `offset` |
| **Search** | None | None | None | None |
| **Cache** | Optional sidecar `.ccr-tip.json` (stores last event ID + update time, avoids reading file content) | SQLite page cache (8MB) | mtime + fileSize used to decide whether to re-read (TTL 45 seconds) | Resident in memory (write operations update memory + disk in sync) |
| **Timeout protection** | Yes (listing timeout fallback) | Not needed | Not needed | Not needed |
| **Complexity** | O(n) file I/O | O(log n) database query | O(1) file read + O(n) in-memory traversal | O(n) in-memory traversal (no disk access) |

## 4. Session Creation

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Entry points** | 1: create the JSONL file | 3: `Create` (normal) / `CreateTitleSession` (title child session) / `CreateTaskSession` (task child session) | 1: add a key to the store inside `updateSessionStore` | 2: `dispatcher.process_user_turn` (user sends a message) / `channel handler` (channel message) |
| **ID generation** | UUID filename | UUID / `"title-" + parentId` / toolCallID | sessionKey | `"local_" + uuid` |
| **Registration** | No registration needed (discovered by scanning the directory) | Queryable automatically after a database INSERT | Written to sessions.json | Written to the `index.json` registry |
| **Event notification** | None | pubsub `CreatedEvent` | None | None (creation is not broadcast; the frontend discovers it via `list_sessions`) |
| **Atomicity** | File creation is itself atomic | `INSERT ... RETURNING` database transaction | read-modify-write inside a file lock | Creation + writing the first message are made atomic (no empty shell produced) |
| **Empty-shell protection** | None (session = file, content exists on creation) | None (a title is required on INSERT) | None | Yes (lazy creation: entering `session_context` only records the id; the session is actually created when the first message is written) |

## 5. Session Deletion

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Method** | Delete the JSONL file | `DELETE FROM sessions WHERE id = ?` | Delete the key from sessions.json | `delete_session` wipes disk + removes the registry entry |
| **Cascade cleanup** | Deletes a single file only (metadata and messages live in the same file) | `ON DELETE CASCADE` automatically clears messages and files | Manually archive the transcript file (`archiveSessionTranscripts`, not a direct delete) | Delete the entire session directory (meta.json + history/) |
| **Event notification** | None | pubsub `DeletedEvent` | None | WebSocket `session_deleted` broadcast |

## 6. Session Resume

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Method** | Read the whole JSONL, rebuild the message chain via `uuid`/`parentUuid`, restore state such as mode/permissionMode/title | SQL query for session + messages, ordered by created_at | Read the transcript JSONL, restore the message sequence | `get_branch(session_id, head_id)` walks back along the parent_id chain in the DAG and returns a linear message sequence |
| **Compaction handling** | The retained range is determined from the `preservedSegment` of the `compact_boundary` entry | If `summary_message_id != ""`, skip all messages before that message | Restore from `compactionCheckpoints` | The compactionSummary node becomes the new branch start; old messages are kept in the DAG but are not on the active branch |

## 7. Compaction (Context Compression)

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Trigger** | Manual + automatic | Manual | Automatic (by token threshold) | Manual (`/compact`) + automatic (`compaction_recommended` signal) |
| **Implementation** | Append a `system:compact_boundary` entry to the JSONL | Generate a summary with a dedicated `summarizeProvider`, write it as a new message, and point `summary_message_id` at it | Generate a summary, record a checkpoint (tokensBefore/tokensAfter/summary), update `compactionCount` | LLM generates a summary → write a compactionSummary node (source="compaction") → reattach the retained tail messages under the summary node → move head_id |
| **Recorded information** | trigger/preTokens/postTokens/preservedSegment/durationMs | summary_message_id | compactionCount + the compactionCheckpoints array | Summary content lives in the node content; budget_pct/context_window live in the recommendation signal |
| **Old messages** | Kept in the JSONL, with preservedSegment marking which are active | Kept in the database, skipped before the summary on load | Kept in the transcript | Kept in the DAG (append-only), but not on the active branch |

## 8. Active Process Tracking

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Mechanism** | A dedicated `~/.claude/sessions/<pid>.json` file | None (single-process TUI) | The `status` field of SessionEntry | `status` enum field (meta.json + registry) |
| **Fields** | pid, sessionId, cwd, startedAt, version, kind (interactive), entrypoint (cli), status (idle/busy), updatedAt, name, bridgeSessionId | — | status (running/done/failed/killed/timeout), runtimeMs, abortedLastRun | `status` (idle/running/needs_input/done/failed) |
| **Crash recovery** | The file lingers after the process exits (requires external cleanup) | — | status may get stuck at running (no automatic recovery) | On startup, reset every `status=running` → `idle` |
| **Relationship to session listing** | Not involved in listing | — | Involved in listing (status is part of SessionEntry) | Involved in listing (`status` is in the registry) |

## 9. Concurrency Control

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Mechanism** | File level (single-process writes; multiple processes coordinate via pid files) | SQLite WAL mode + 8MB page cache | File lock (lockfile) + in-process FIFO queue | Python `threading.Lock` (`_sessions_lock`) + Git file-level operations |
| **Lock granularity** | Per JSONL file | Database level | Per sessions.json file | SessionStore level (`self._lock`) |
| **In-process** | Single-threaded | Handled by SQLite | FIFO queue serializes writes to the same storePath | `threading.Lock` protects the `_sessions` dict and registry writes |
| **Cross-process** | pid file marker (`sessions/<pid>.json`) | Built into SQLite | lockfile exclusive lock (stale detection 30 minutes + PID liveness check) | No explicit cross-process lock (single-worker process model) |
| **Lock timeout/watchdog** | None | None (SQLite busy timeout) | Watchdog patrols every 60 seconds, hold timeout 5 minutes | None |

## 10. Data Maintenance

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Expiry cleanup** | None | None | `pruneStaleEntries`: entries not updated in 30 days | On startup, clean up sessions that are `archived=True` and not updated in over 90 days |
| **Capacity limit** | None | None | `capEntryCount`: at most 500 entries, deleting the oldest when exceeded | Cap of 1000; when exceeded, delete the oldest archived sessions |
| **File rotation** | None | None | `rotateSessionFile`: rotate sessions.json when it exceeds 10MB, keeping the 3 most recent backups | None |
| **Disk budget** | None | None | `enforceSessionDiskBudget`: optional, cleans up by total disk usage | None |
| **Archive on delete** | None (deletes the file directly) | None (CASCADE deletes directly) | Yes (transcript file is archived rather than deleted directly) | None (deletes the directory directly) |
| **Maintenance mode** | — | — | "warn" (default, warn only) / "enforce" (actually execute) | — |
| **updated_at maintenance** | Implicit (file mtime) | SQLite trigger-driven | Written by the application layer | Written by the application layer (automatically set to `time.time()` during `_persist_meta`) |
| **message_count maintenance** | Not tracked | SQLite trigger-driven +1/-1 | Not tracked | Not tracked |

## 11. Title (Naming)

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Auto-naming** | Asynchronous LLM generation after the first turn | Forked asynchronous LLM generation after the first turn (dedicated title agent) | None (set displayName manually) | After the first turn: synchronously truncate the first 50 characters → asynchronous daemon thread LLM generation |
| **Manual naming** | A `custom-title` entry overrides | None (no rename feature) | Set `displayName` / `label` | UI rename / `/rename` / agent rename tool |
| **LLM regeneration** | None | None | None | `/rename` with no argument → call the LLM again to regenerate |
| **Injection protection** | Wrapped in `<session>` tags + a "treat as data" instruction | None (the title agent's prompt is concatenated directly) | — | Wrapped in `<session>` tags + a "treat as data" instruction |
| **Language following** | The prompt requires using the conversation's language | The prompt requires using the conversation's language | — | The prompt requires using the conversation's language |
| **Post-processing** | JSON schema structured output | Strip `<think>` tags, take the first non-empty line, truncate to 100 characters | — | Strip `<think>` tags, strip quotes, strip prefixes, truncate to 80 characters |
| **Idempotency marker** | None (inferred from whether an `ai-title` entry already exists) | Inferred from an `isDefaultTitle` regex | — | `_auto_titled` bool marker |
| **Race protection** | None | None | — | The background thread checks that the title is still the truncated value before writing |
| **Title broadcast** | None (the frontend re-reads the JSONL) | None (the TUI reads the database directly) | — | WebSocket `session_updated {id, title}` |

## 12. Message Storage Model

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **Structure** | Linear JSONL (a tree can be built via uuid/parentUuid) | Flat table (ordered by session_id + created_at) | Linear JSONL | DAG (each message has a parent_id, supports branching) |
| **Branching** | Yes (parentUuid supports a tree structure, but the UI does not expose it) | None | None | Yes (head_id + branches map, the UI can switch branches) |
| **Message ID** | uuid (per message) | id (per message) | No explicit ID (by line order) | id (per message) |
| **Message format** | `{type, message: {role, content}, uuid, parentUuid, timestamp, ...}` | `{id, session_id, role, parts, model, created_at, ...}` | `{role, content, ...}` | `{id, role, content, parent_id, timestamp, ...}` |

## 13. JSONL Entry Types (Claude Code only)

Claude Code's JSONL mixes messages and metadata, with a rich set of entry types:

| Type | Purpose |
|------|------|
| `user` | User message (includes uuid, parentUuid, timestamp, cwd, gitBranch) |
| `assistant` | Assistant reply (includes usage, model, requestId) |
| `attachment` | Attachment (file, image) |
| `system` | System event (subtypes: turn_duration / away_summary / compact_boundary / api_error / local_command / informational / bridge_status / scheduled_task_fire) |
| `ai-title` | LLM auto-generated title |
| `custom-title` | User-set title |
| `agent-name` | Agent name |
| `last-prompt` | Last prompt position (leafUuid) |
| `mode` | Conversation mode (normal/plan/...) |
| `permission-mode` | Permission mode |
| `file-history-snapshot` | File snapshot (used for revert) |
| `bridge-session` | Bridge session ID |
| `queue-operation` | Queue operation |

## 14. Summary

| Dimension | Claude Code | OpenCode | OpenClaw | OpenProgram |
|------|------------|----------|---------|-------------|
| **Design philosophy** | File as data, append-only | Relational database, structured queries | Registry + transcript | Git DAG + registry |
| **Listing performance** | Slowest (scan directory + read files) | Fastest (database index) | Fast (read one JSON file) | Fast (in-memory resident registry) |
| **Metadata richness** | Low (title + a few status markers) | Medium (10 fields) | Highest (around 70 fields) | Medium (around 18 fields) |
| **Maintenance capability** | None | None | Most complete (expiry/capacity/rotation/budget) | Yes (90-day expiry cleanup + 1000 capacity cap) |
| **Concurrency capability** | Weak (single-process writes) | Strong (SQLite WAL) | Medium (file lock + queue) | Medium (threading.Lock, single worker) |
| **Branching capability** | Yes (DAG, UI does not expose it) | None | None | Yes (DAG, UI can switch) |
| **Channel support** | None | None | Yes (full routing fields) | Yes (channel/account_id/peer) |
| **Archive/pin** | None | None | None | Yes (pinned/archived/group) |
| **Crash recovery** | No automatic recovery | Not needed (single process) | No automatic recovery | On startup, reset status=running → idle + automatic rebuild of a corrupted registry |
| **Empty-shell protection** | Not needed (file is content) | Not needed (data exists on INSERT) | Not needed | Lazy creation + atomic create-and-write-message |
