# `openprogram/memory/`

Persistent, machine-wide memory for OpenProgram agents.

**Design doc:** [`docs/reference/design/memory/overview.md`](../../docs/reference/design/memory/overview.md).
Read that first — this README is a navigation aid for working in the
directory, not a substitute.

## TL;DR

Memory is a workspace of Markdown files under `<state>/memory/`:

1. `sources/` — the append-only evidence record: what was said, archived
   verbatim, written only by the runtime.
2. `topics/**/*.md` — the editable semantic memory, one file per subject.
   Every paragraph ends in a stable `^block-id` and cites a footnote
   pointing at the source it rests on.
3. `core.md` — the small always-on block injected into every session.

`timeline/`, `recent_events.jsonl` and `relations.json` are derived and
rebuilt after every write.

Writing happens in the background, not in the conversation: turns
accumulate and are written once there is roughly 16k tokens' worth, and
a nightly pass rewrites what has landed.

## File map

| Path | Role |
|---|---|
| `store.py` | Where the workspace is; migration off the previous layout |
| `provider.py` | `MemoryProvider` ABC and the `<memory-context>` fence |
| `scriptorium/provider.py` | The lifecycle hooks the agent runtime calls |
| `scriptorium/writing.py` | Accumulate, write, reorganize |
| `scheduler.py` | Daemon thread; the 03:00 reorganize |
| `session_watcher.py` | Writes a session's remainder once it goes idle |
| `scriptorium/management/` | The write transaction: staging, validation, install |
| `scriptorium/retrieval/` | BM25 and embedding search over the workspace |
| `scriptorium/markdown/` | The topic format — blocks, footnotes, links |
| `scriptorium/prompts/` | What the writer is told |
| `scriptorium/runtime/` | Cursors, thresholds, derived views |
| `scriptorium/agent_runtime/` | The process that performs a write |

## Working here

The writer runs on the user's own login and default model, so nothing
needs a separate API key. A write is transactional: it stages, validates
and installs, or it changes nothing.

Two invariants worth knowing before editing:

- **Block IDs are the runtime's.** A `^id` that disappears breaks every
  view and link reaching it, so the writer copies them through and the
  transaction rejects an edit that drops one.
- **`sources/` is append-only.** Topics can be rewritten freely; the
  evidence they cite cannot.
