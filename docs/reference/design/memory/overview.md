# Memory subsystem

How OpenProgram makes the agent "remember" things across conversations.

> This document covers the memory subsystem end to end. For the entity
> tier's git substrate see [`git-as-entity-memory.md`](git-as-entity-memory.md)
> and [`entity-memory.md`](entity-memory.md).
>
> Path conventions: all state lives under `~/.openprogram/` (= `get_state_dir()`);
> named profiles use `~/.openprogram-<profile>/`.

## Why this exists

A vanilla LLM forgets everything when a conversation ends. Each new chat
starts from zero, so the user retells the same facts ("I'm a product
manager, please avoid jargon", "the project lives at `~/Projects/foo`")
session after session. Memory fixes that by writing finished
conversations into durable files and feeding the relevant parts back.

Two properties we care about:

1. **The model gets the right facts unprompted.** Stable preferences and
   project facts are in the prompt before the user has to repeat them.
2. **Storage stays reviewable.** Memory is plain Markdown, readable in an
   editor and diffable in Git. Every claim carries a footnote pointing at
   the message it came from, so anything surprising can be traced back to
   what was actually said.

## Three layers on disk

```
<state>/memory/
    core.md                  always-on block, injected every session
    topics/                  the editable semantic memory
        people/dave.md
        projects/budget-tracker.md
    sources/                 append-only evidence, written by the runtime
        openprogram/<session-id>.md
    timeline/                derived time axis, rebuilt after every write
        2026/08/09.md
    recent_events.jsonl      derived
    relations.json           derived
    .scriptorium/            runtime state: cursors, write lock, history
```

**Sources** are what was said, archived verbatim and never edited.
**Topics** are what it means — one file per person, project or recurring
theme. Every topic paragraph ends in a stable `^block-id` and cites a
footnote:

```markdown
Craig is building a budget tracker in Flask, due 2024-04-15.[^e-1175dea39c] ^f888f60e

[^e-1175dea39c]: Time: `2024-03-15`; Sources: [openprogram/sess-7f2a/msg_2f9b](../sources/openprogram/sess-7f2a.md#source-8339b8d3)
```

The block ID is how other views and links reach that paragraph; it
survives edits and moves. The footnote is how a claim is traced back.

`timeline/`, `recent_events.jsonl` and `relations.json` are derived —
rebuilt from topics after every successful write. Editing them by hand
accomplishes nothing.

## When writing happens

Not during the conversation. Turns accumulate, and the model is asked to
write them up once there is a batch worth a call — about 16k tokens.
Writing per turn would cost a model call per turn and produce memory
shaped like a transcript instead of like knowledge.

Three things trigger a write:

| Trigger | Where | What it does |
|---|---|---|
| A turn finishes | `provider.sync_turn` | Writes if the session has crossed the threshold |
| A session goes idle | `session_watcher` | Flushes the remainder, however small |
| 03:00 daily | `scheduler` | Reorganises topic files |

The conversation is read back from the session store rather than
buffered in the process. That store is durable and ordered, so a turn's
identity survives a worker restart and the cursor in
`runtime/online.py` can tell what has already been written. A
module-level buffer would lose its contents on restart and hand out
positions that change between runs, which is exactly what a cursor
cannot tolerate.

Each write takes the leading turns that reach the threshold, not the
whole backlog: a session running all day arrives with far more than one
model call can hold. The idle flush repeats that until nothing is left,
because for it there is no later pass — the watcher marks a session
processed on the way out, so a flush that stopped after one batch would
strand the rest for good. It reports whether it finished, and an
unfinished one is left for the next poll.

A turn is what a person said and what the assistant replied. Tool calls
and their results are the machinery of a turn rather than its content,
and so are the turns the runtime schedules for itself: a finished
sub-agent's notification and a merge prompt are written as user rows so
the model has something to answer, but nobody said them.

## Why the nightly sweep exists

Writing only ever makes files longer; nothing shortens them. Left alone,
a workspace becomes one enormous file per subject with its timeline cut
into pieces by topic — the shape that makes ordering and counting
questions unanswerable. The 03:00 sweep splits files that have grown to
cover several subjects, merges paragraphs that say the same thing, and
repairs links.

It also runs on demand: `openprogram memory sleep`.

## What the model sees

- **Every session**: `core.md`, injected as a fenced `<memory-context>`
  block so recalled facts are never mistaken for the user talking now.
- **Every turn**: whatever `prefetch` finds for that message — a BM25
  search over blocks and sources, top five, also fenced.
- **On demand**: the `memory_*` tools.

## Tools

| Tool | For |
|---|---|
| `memory_search` | Find paragraphs by meaning |
| `memory_grep` | Find an exact name, ID or phrase |
| `memory_get` | Read a file, a section, or one block with its footnotes |
| `memory_browse` | See what exists |
| `memory_update` | Correct or add one thing, as a unified diff |
| `memory_status` | Size and the current revision |

There is no tool for "save this". Recording the conversation is the
background writer's job. `memory_update` is for what the user asked to
be remembered right now, and for fixing something the model can see is
wrong.

## Writes are transactional

One `memory_update` carries the evidence and the edit citing it, checked
against the revision the caller read. A patch that cites a source it did
not supply, links a block that does not exist, or breaks the topic
format is refused whole and the workspace is left byte-identical.
Derived views are rebuilt only after a successful install.

A cross-process lock (`.scriptorium/write.lock`) serialises writers, so
a background write and a live chat write cannot interleave. Background
writing takes the lock with a one-second timeout and gives up rather
than making a user wait; the next turn brings it back around.

## Code map

The package splits into the contract and one implementation of it.

```
openprogram/memory/           the framework side
    provider.py               MemoryProvider — the contract
    __init__.py               get_provider() / set_provider()
    store.py                  where memory lives; migration off the old layout
    scheduler.py              daemon thread, 03:00 maintenance
    session_watcher.py        idle-session flush
    scriptorium/              the shipped implementation
        provider.py           satisfies the contract
        writing.py            accumulate, write, reorganise
        management/           the write transaction, staging, validation
        retrieval/            BM25 and embedding search
        markdown/             the topic format
        prompts/              what the writer is told
        runtime/              cursors, thresholds, derived views
        agent_runtime/        the process that does the writing
```

Nothing in the agent loop, the tools, the web UI or the CLI names an
implementation: the runtime calls `get_provider()`. Swapping memory
systems means writing a class that satisfies `MemoryProvider` and
pointing `get_provider()` at it. `set_provider()` is the supported way
in, and what tests use.

The writer runs on the user's own login and default model, so background
memory needs no separate credential. `openprogram memory sleep --model`
and `scheduler.start_in_worker(model=...)` override it.

## Migrating from the previous layer

The workspace kept its location, so an existing installation finds
memory in the same place. What is inside changed: `journal/` and `wiki/`
are gone, replaced by `sources/` and `topics/`; `core.md` is unchanged.

On first use, `store.ensure()` moves `journal/`, `wiki/`, `.state/` and
`index.sqlite` to `<state>/memory-superseded/`. Moved, not deleted, and
to a sibling directory rather than a subdirectory: inside the workspace
they would still be listed, and deleting someone's notes to make room
for a new format is not a migration.

## Failure modes

| Failure | Effect |
|---|---|
| No writer process available | Writing is deferred and retried; the conversation is safe in the session store |
| Model unreachable mid-write | The turn is rolled back whole; the cursor does not advance, so the same turns are retried |
| Another writer holds the lock | This pass is skipped; the next turn retries |
| The writer's edits are rejected twice | The batch fails whole — one repair attempt, then nothing is installed and the cursor does not advance |
| A hand edit breaks the format | `openprogram memory edit` validates and reports before the views are rebuilt |

Memory never takes a conversation down with it: every provider hook
swallows its own failures and logs them. Swallowed is not forgotten —
`on_session_end` returns whether the session is finished, and the
watcher leaves an unfinished one for the next poll instead of marking
it processed.

## Plugin point

`MemoryProvider` (`provider.py`) is the interface between memory and the
agent runtime:

| Hook | When |
|---|---|
| `system_prompt_block()` | Session start |
| `prefetch(query)` | Before each turn |
| `sync_turn(user, assistant, session_id=)` | After each turn |
| `on_session_end(messages, session_id=)` | Session boundary |
| `on_pre_compress(messages)` | Before context compression |
| `maintain(**kwargs)` | Nightly |
| `get_tool_schemas()` / `handle_tool_call()` | Optional extra tools |

Every one has a default, so an implementation only writes the hooks it
has something to do for.
