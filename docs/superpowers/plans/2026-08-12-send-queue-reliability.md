# Send Queue Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make queued chat turns strictly serial within one renderer session, recover known socket-write failures, preserve attachment drafts, and atomically reject concurrent backend turns.

**Architecture:** Keep the existing Zustand per-session FIFO. Treat the session store's running entry as the single frontend dispatch gate, and make backend run acquisition atomic before any user-message mutation. Do not restore the deleted MessageStore protocol or add persistence.

**Tech Stack:** TypeScript, Zustand, WebSocket, Node assertion checks, Python asyncio/threading, pytest.

## Global Constraints

- Preserve unrelated dirty-worktree changes.
- Queue storage remains renderer-memory only.
- Queued turns remain plain text; attachment drafts are retained until an idle normal send.
- Add no dependencies and no new queue abstraction.
- Every behavior change starts with a failing regression check.

### Task 1: Frontend serialization and reconnect

**Files:**
- Modify: `web/scripts/check-send-queue.mjs`
- Modify: `web/lib/session-store/index.ts`
- Modify: `web/components/chat/composer/legacy-send.ts`
- Modify: `web/lib/net/use-ws.ts`
- Modify: `web/lib/state/send-queue.ts`

**Interfaces:**
- Consumes: `setRunningTaskFor(sessionId, task | null)`, `useSendQueue.getState().drain(sessionId)`.
- Produces: one dispatch per real running→idle transition and a reconnect drain for idle queues.

- [x] Add a check that calls idle twice before ACK and asserts only the first queued text was written.
- [x] Run `npm run check:send-queue` and confirm the new assertion fails with two sent texts.
- [x] Mark every successful `sendChatMessage` with a per-session optimistic running task.
- [x] Change `setRunningTaskFor` so only a real running→idle transition schedules drain.
- [x] Add reconnect reconciliation using `get_run_state` for background sessions, without changing socket focus.
- [x] Run `npm run check:send-queue` and confirm all queue assertions pass.

### Task 2: Attachment boundary

**Files:**
- Modify: `web/scripts/check-send-queue.mjs`
- Modify: `web/components/chat/composer/use-chat-submit.ts`

**Interfaces:**
- Consumes: current composer `pendingImages` and `pendingDocs`.
- Produces: running-session submit leaves text and attachments unchanged when either attachment list is non-empty.

- [x] Add a behavior assertion that the running queue refuses attached drafts.
- [x] Run `npm run check:send-queue` and confirm the assertion fails.
- [x] Add the minimum guard to retain the complete draft while a turn runs.
- [x] Run `npm run check:send-queue` and confirm it passes.

### Task 3: Atomic backend turn acquisition

**Files:**
- Modify: `tests/unit/test_webui_head_mirror_and_run_guard.py`
- Modify: `openprogram/webui/server.py`
- Modify: `openprogram/webui/ws_actions/chat.py`
- Verify callers that release `_running_tasks` in existing execute/finalize paths.

**Interfaces:**
- Produces: `_try_reserve_run(session_id, msg_id) -> bool` and `_activate_run_reservation(...)`, which reserve one session and hand it to a registered runtime without an idle window.
- Consumes: existing `_running_tasks_lock`, `_running_tasks`, and normal finalization cleanup.

- [x] Add concurrent reservation, handler rejection, ACK disconnect, runtime handoff and focus-neutral state-query regressions.
- [x] Confirm the old separate guard and reservation handoff behavior fail their regression checks.
- [x] Implement atomic reservation before persistent user-message mutation.
- [x] Keep reservation active until runtime registration; release setup/start failures and expire abandoned reservations.
- [x] Run the targeted backend tests and confirm they pass.

### Task 4: Documentation status and verification

**Files:**
- Modify: `docs/reference/design/ui/send-queue-reliability.html`

- [x] Run queue check, focused web checks, targeted backend tests, Web TypeScript/build checks, and diff whitespace checks.
- [x] Update the HTML implementation record with exact files and fresh command results.
- [x] Review `git diff` only for task files and confirm no unrelated dirty changes were modified.
- [x] Request an independent code review and resolve every Critical or Important finding.
- [x] Re-run the complete verification set after review fixes.
