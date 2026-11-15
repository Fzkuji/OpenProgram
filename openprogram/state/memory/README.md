# `openprogram/state/memory/`

Persistent, machine-wide memory for OpenProgram agents.

**Design doc:** [`docs/reference/design/memory/overview.html`](../../docs/reference/design/memory/overview.html).
Read that first — this README is a navigation aid for working in the
directory, not a substitute.

## TL;DR

Memory is a workspace of Markdown files under `<state>/memory/`:

1. `topics/**/*.md` — the editable semantic memory, one file per subject.
   Every paragraph ends in a stable `^block-id` and cites a footnote
   linking the chat message it rests on, `/s/<session>#<message>`. The
   chat history is the evidence; memory keeps no copy of it.
2. `MEMORY.md` — the entry point injected into every session: a map of the
   workspace kept by the memory agents, then a core section rendered from
   `topics/core.md` (only trusted blocks, within `core_max_tokens`).

`timeline/`, `recent_events.jsonl` and `relations.json` are derived and
rebuilt after every write. Memory does not own scheduled work. Scheduler tasks
may keep stable `workspace_id` + `memory_id` references to Topic blocks and
resolve their current content at execution time. A legacy `commitments.jsonl`
file is read only by the one-time Scheduler migration.

Writing happens in the background, not in the conversation: turns
accumulate and are written once there is roughly 16k tokens' worth, and
a nightly pass rewrites what has landed.

Three features sit behind their own settings, all under `memory.*`:
hybrid recall tuning (`retrieval.hybrid.*`), a project's own memory read
beside the personal one (`layers.*`), and a self-check after each write
(`writer.verify`). The design doc explains each.

## File map

| Path | Role |
|---|---|
| `store.py` | Where the workspace is; migration off the previous layout |
| `backend.py` | `MemoryBackend` ABC and the `<memory-context>` fence |
| `local_backend.py` | `LocalMemoryBackend` — the lifecycle hooks the agent runtime calls |
| `policy.py` | Per-execution memory access: modes, spaces, the commit guard (a turn enters its policy through `openprogram/runtime/conversation/memory_execution.py`) |
| `project_layer.py` | A project's own memory: which one, when it is read, routing writes to it |
| `writing.py` | Accumulate, write, verify, reorganize |
| `scheduler.py` | Daemon thread; the 03:00 reorganize of every space and project |
| `session_watcher.py` | Writes a session's remainder once it goes idle |
| `checkpoints.py` | Five-minute Git checkpoints for manual edits |
| `references.py` | Stable references to Topic blocks, for the Scheduler |
| `chat_refs.py` | Citing chat messages: refs, links, which messages may be cited, excerpts |
| `workspace_layout.py` | Names the runtime owns inside a workspace |
| `management/` | The write transaction (staging, validation, install) and write verification |
| `retrieval/` | BM25, embedding and hybrid search; layered reads across workspaces |
| `markdown/` | The topic format — blocks, footnotes, links |
| `prompts/` | What the writer, the organizer and the verifier are told |
| `runtime/` | Node marks, thresholds, derived views, writer status, legacy commitment migration parser |
| `agent_runtime/` | The Claude Code writer and the writer result types |
| `writer_agent.py` | The hook the runtime registers its writer through (`OpenProgramAgent`, in `openprogram/runtime/conversation/memory_agent.py`) |

## Below the runtime

Memory is part of `openprogram.state` and never imports
`openprogram.runtime`, not even inside a function. What it needs from the
runtime comes through ports the runtime fills in when it loads
(`openprogram/runtime/lower_layers.py`):

- `state/store/current.py` — `current_store()`, the session store of the
  active Context;
- `state/execution_facts.py` — the bound session, the saved-Agent registry
  (`agent_registry()`), used by `policy.py`, `project_layer.py` and
  `scheduler.py`;
- `state/event_sink.py` — `memory.*` events and the writer-failure frame;
- `state/authority.py` — the authority record a turn carries, which decides
  whose words may become memory;
- `writer_agent.py` — the writer itself.

## Working here

The writer runs on the user's own login and on the default Agent's model,
or the last used model when that Agent has none, so nothing needs a
separate API key. A write is transactional: it stages, validates
and installs, or it changes nothing.

Two invariants worth knowing before editing:

- **Block IDs are the runtime's.** A `^id` that disappears breaks every
  view and link reaching it, so the writer copies them through and the
  transaction rejects an edit that drops one.
- **A new citation must name a real chat message.** Topics can be
  rewritten freely, but a footnote added by an edit is checked against
  the session store: the message must exist and be a person's or the
  assistant's words. Committed citations are left alone, so deleting a
  chat leaves its memory standing.
