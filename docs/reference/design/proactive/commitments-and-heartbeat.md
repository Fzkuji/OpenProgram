# Commitments and Heartbeat

This document records the implemented boundary and the remaining work. It does
not define a general task system.

## 1. OpenProgram current state

### Implemented data and write path

The existing memory writer now identifies obligations while processing its
already-selected conversation batch. It calls `record_commitments`; there is no
second extraction service. The LLM supplies the obligation sentence, semantic
due value, Source ref, and an exact one-line quote. The Runtime verifies that
the quote is a substring of that trusted, in-batch Source, validates the
absolute `YYYY-MM-DD` date or `null`, and derives the speaker and commitment ID.

The tool schema and Runtime both enforce the same writer limits: at most 64
commitment creates plus transitions in one batch, at most 2,048 characters for
normalized commitment text, 8,192 for an exact quote, and 512 for a Source ref.
The count follows the existing 64-source transaction precedent. Runtime checks
remain authoritative if a caller bypasses schema validation. The owner Web
route performs one explicit transition and does not accept writer semantic text
or quotes, so these writer-batch limits do not create a separate owner limit.

`commitments.jsonl` is installed in the same staged memory transaction as Topic
changes. Each record contains `id`, `text`, `due`, `speaker_id`, `source`,
`source_quote`, `status`, transition provenance, transition time, and
`notification_steps`. IDs are deterministic over Source ref plus exact quote,
not LLM wording, due-date interpretation, or item order. Distinct quotes allow
multiple commitments from one Source; reworded extraction of the same evidence
does not create or reopen another record. Only `open`, `done`, and `dismissed`
are supported.

The file is an additive workspace capability: an existing workspace without it
loads as an empty commitment set, so this release needs no one-time migration.
Writes use the existing atomic file replacement, staged install, workspace
lock, and memory Git candidate list. Status and heartbeat tolerate malformed
rows, count them as invalid, and preserve their raw lines while updating valid
notification state. Upsert and transition are strict: any malformed row rejects
the mutation without changing the file. No invalid data is silently deleted or
reinterpreted as another schema.

A later trusted writer batch may semantically close a record only by citing a
trusted Source and exact closure quote from that current batch. The Runtime
persists that Source, quote, and a Runtime timestamp as transition provenance.
An explicit transition uses the existing `memory_update` transaction with the
current memory revision; only persisted owner authority may request `done` or
`dismissed`, and the Runtime records `owner/manual` rather than accepting a
client-supplied provenance label.

### Implemented deterministic heartbeat

The heartbeat is a built-in check in the existing `cron-worker`, not a new
scheduler or an agent turn. On each normal cron tick it reads live config:

- `proactive.heartbeat`: `daily` at 09:00 local time (default), `hourly` at
  minute 00, or `off`.
- `proactive.quiet_hours`: `23:00-08:00` local time by default.

Invalid manual configuration is skipped without terminating the worker. The
heartbeat reads only open, dated commitments. It sends once when a
record becomes due and once when it reaches the current seven-day overdue
step. Undated records remain visible and are not pushed. Quiet-hour suppression
does not consume a notification step.

The deterministic ID deduplicates repeated extraction. The persisted `due` and
`overdue:7` steps bound successful delivery to two notifications per record in
the current policy. A failed send consumes neither step and is retried by a
later eligible heartbeat. Delivery is intentionally at-least-once: a process
crash after the channel accepts a message but before the atomic state write can
produce one duplicate. The current outbound API has no cross-channel
idempotency key, so claiming exactly-once delivery or adding a separate delivery
journal would not remove that external ambiguity.

Delivery is derived from evidence rather than model output. For an
`openprogram/<session>/<message>` Source, the Runtime loads the originating
session from `SessionDB` and uses that session's existing channel, account, and
peer binding. Records without a target remain visible. Due records sharing a
target are sent as one message through `channels.outbound`; notification steps
are written only after that send returns success. Tests replace outbound send,
so no test uses a real channel or credential.

### Implemented owner and model surfaces

`memory_status`, `openprogram memory status`, and `GET /api/memory/status`
expose commitment counts and a bounded-field record projection. The
model-facing response still omits the host workspace path and does not include
channel bindings, account data, peer data, exact Source quotes, or arbitrary
fields found in the JSONL file. It exposes Source refs and transition Source
refs, which are sufficient to audit provenance without repeating message text.
The owner CLI and owner-only web route retain their existing `workspace_path`
behavior.

The existing Memory web page has a Commitments tab showing counts and records.
Open records have confirmed `done` and `dismissed` controls. The owner-authenticated
route passes the displayed revision to the same `MemoryWorkspace.update`
transition used underneath `memory_update`; a stale revision returns conflict,
malformed/non-object JSON returns structured `INVALID_ARGUMENT`, and no second
state writer or authority model was added.

### Current boundary

This feature is a cited flat list and a deterministic reminder check. It has no
priorities, subtasks, projects, autonomous execution, independent task manager,
or independent scheduling/storage subsystem. It requires `cron-worker` to be
running; the resident web worker does not add a fallback timer.

## 2. How comparable projects design this area

- **Adopt and modify —** [OpenClaw Heartbeat](https://docs.openclaw.ai/gateway/heartbeat) currently
  models heartbeat as a scheduled main-session turn owned by its Automations
  scheduler, with explicit delivery targets and active hours. OpenProgram adopts
  configurable inactive time as quiet hours, but derives the delivery target
  from trusted Source/session state and performs deterministic filtering. It
  rejects a separate scheduler and a heartbeat LLM turn.
- **Adopt caution, modify provenance, reject the retired mechanism —** [OpenClaw Inferred commitments](https://docs.openclaw.ai/concepts/commitments)
  states that its inferred-commitments experiment is retired: it no longer
  extracts or delivers new follow-ups, while legacy rows remain inspectable and
  dismissible. OpenProgram adopts inspect/dismiss lifecycle visibility and the
  need for evaluation, but requires a trusted cited Source and deterministic
  Runtime fields. It rejects confidence-only autonomous follow-up expansion.
- **Adopt the existing memory lifecycle, reject a second manager —** [LangMem](https://github.com/langchain-ai/langmem) provides hot-path memory
  tools and a background memory manager that extracts, consolidates, and updates
  knowledge. OpenProgram keeps extraction in its existing writer, then modifies
  the write with Source-batch validation and atomic commitment staging. It does
  not add another memory service.
- **Reuse the scheduling principle, reject another scheduling API —** [LangSmith Deployment cron jobs](https://docs.langchain.com/langsmith/cron-jobs)
  schedule an assistant and input on a thread or on newly created threads.
  OpenProgram reuses its existing cron-worker and originating session binding;
  it does not add scheduled assistant turns or a second thread scheduler.

These sources support comparison of individual mechanisms only. They do not
show that the systems have equivalent trust, storage, transition, or delivery
semantics.

## 3. OpenProgram follow-up plan

1. Measure extraction precision, missed obligations, incorrect dates, premature
   closure, duplicate reminders, and reminder usefulness on replayed sessions.
   The retired OpenClaw experiment makes this an acceptance gate, not optional
   polish.
2. Extend the implemented fake-transport matrix into long-running soak tests,
   especially concurrent writer/heartbeat activity and process termination in
   the post-send/pre-state-write window.
3. Add a transport idempotency key only if every supported outbound adapter can
   honor the same contract; until then document and observe at-least-once
   delivery rather than adding a journal that cannot prove external deduplication.
4. Consider configurable overdue intervals or timezone selection only after
   observed usage requires them. Keep daily/hourly/off, the seven-day step, and
   host-local time until then.
5. Do not add priorities, subtasks, projects, autonomous action, or a new
   scheduler/storage/task-manager subsystem as part of this follow-up.
