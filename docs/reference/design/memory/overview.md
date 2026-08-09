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
    core.md                  always-on block, rendered from topics/core.md
    topics/                  the editable semantic memory
        core.md              what must be visible in every conversation
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

`core.md`, `timeline/`, `recent_events.jsonl` and `relations.json` are
derived — rebuilt from topics after every successful write. Editing
them by hand accomplishes nothing.

## When writing happens

Not during the conversation. Turns accumulate, and the model is asked to
write them up once there is a batch worth a call — about 16k tokens.
Writing per turn would cost a model call per turn and produce memory
shaped like a transcript instead of like knowledge.

Three things trigger a write:

| Trigger | Where | What it does |
|---|---|---|
| A turn finishes | `provider.write()` | Writes if the session has crossed the threshold |
| A session goes idle | `provider.write(force=True)` | Writes the remainder, however small |
| 03:00 daily | `provider.reorganize()` | Rewrites topic files |

The conversation is read back from the session store rather than
buffered in the process. That store is durable and it gives every turn
a stable id, which is what the cursor in `runtime/online.py` records. A
module-level buffer would lose its contents on restart and hand out
positions that change between runs, and in a session that branches a
position is not an identity at all. The next section says what the
cursor holds instead.

The first two rows are one method and one flag, not two hooks. What
separates them is how hard to try, and every other word about them is
the same, so naming them separately means naming the same action twice
and getting neither name right. A per-turn call is not "writing this
turn" either: it fires every turn but writes only on the turns that
bring the session over the line, and what it writes is the batch that
has gathered since the last one, which usually spans several turns.

Each write takes the leading turns that reach the threshold, not the
whole backlog: a session running all day arrives with far more than one
model call can hold. Forced, `write` repeats that until nothing is
left, because there is no later pass — the watcher marks a session
processed on the way out, so stopping after one batch would strand the
rest for good. What it reports back decides whether the watcher comes
back, and the section on failure modes below says how.

A turn is what a person said and what the assistant replied. Tool calls
and their results are the machinery of a turn rather than its content,
and so are the turns the runtime schedules for itself: a finished
sub-agent's notification and a merge prompt are written as user rows so
the model has something to answer, but nobody said them.

## Who said it

Several people share one agent, so a session holds turns from more than
one person. A Telegram group talks to a single conversation by default,
and an agent set to `session_scope: main` collects every direct peer
into one. Recording all of them as "the user" turns three people
settling a budget into one person changing their mind, so every turn
carries who said it.

The identity comes from the turn, not from the session. A session row
holds one peer, and in a group that peer is the group.

Identity travels inside the message text, not in a field beside it. The
channel adapter puts a label in front of what the sender wrote, and
every stage downstream carries it without knowing it is there: the
session store, a fork of the branch, the writer prompt, the source
archive, and whatever a topic file ends up saying. `openclaw` and
`hermes-agent`, the two reference frameworks that handle group chats,
both do exactly this, and it is why neither of their memory layers
holds a sender field at all.

The label is `display (id)`, because either half alone loses somebody.
A display name reads naturally in a topic file and is what a search for
a person finds, and people rename themselves and share names with each
other. A platform id survives a rename and separates two people called
Ada, and a file full of numbers says nothing to whoever reads it. The
display name is trimmed to one line and capped at 64 characters on the
way in, and its square brackets become round ones. One record per line
is what both renderers assume, and a display name is whatever its owner
typed into the platform: a newline in it splits one archived record into
two and leaves the evidence footnote pointing at a line that is not the
content, and a bracket in it forges a second speaker prefix.

The prefix is added in `channels/base.py`, which holds the sender's id
and display name side by side and is the only caller of
`dispatch_inbound`, so one place covers every channel. It goes on every
channel turn rather than only on group turns: an agent set to
`session_scope: main` puts direct peers in one session too, and the
scope is resolved further down, so a direct message is not reliably one
speaker and `base.py` is not where that is known. `peer_id` stays what
it is, the routing target and the address a reply is sent back to,
which in a group is the group. Web, CLI and TUI turns never pass
through that path and are untouched. The write prompt says that the
name in brackets at the head of a user message is the person who said
it, so a fact lands under that person rather than under "the user".
What the reference frameworks do here, and what following them saved,
is drawn out in [`speaker-identity.html`](speaker-identity.html).

Identity is what memory records, and it partitions nothing. One
workspace and one set of topic files, shared by everyone the account
approves, because that sharing is what makes a team bot worth having. A
person is a topic file like any other subject, which is where a rename
or a second channel is reconciled. Someone who wants memory of their
own runs their own instance
([Chat Channels](../../../integrations/channels.md#who-can-talk-to-your-bot)).

## The write cursor

A session is a DAG, not a line. Re-asking an earlier message, retrying
a reply and spawning a sub-agent all fork the chain, so one session
holds several branches that share a prefix and diverge after it
([`../runtime/dag/overview.md`](../runtime/dag/overview.md) §4).
Everything said on a branch belongs in memory, and nothing said on the
shared prefix belongs in memory twice.

The cursor therefore records identity, not position. One file per
thread holds the ids of the messages memory has taken:

```
<state>/memory/.scriptorium/cursors/openprogram/<session-id>.json
    {"written": ["a3f1c2", "9d0e77", "4b21ae"]}
```

The path carries the provider and the thread, the file carries the ids
in the order they were written. A number cannot carry that identity:
the fourth message of a branch and the fourth message of the trunk are
two different messages under the same number, so a cursor holding
"written through nine" reads an entire branch numbered four to nine as
already written and offers none of it.

### Working out what a session owes

`write` reads the branch that ends at the session's head. `get_branch`
walks `predecessor` edges from that head back to the start of the
branch and returns the line in order, so by the time memory sees it the
walk is done. The turns on that line whose ids are not in `written` are
what the session owes, in branch order. The batch is the leading part
of them that reaches the threshold, and their ids join `written` when
the write transaction installs, never before.

The ids are the whole boundary, so a branch needs no identifier of its
own. The tip cannot serve as one anyway: a branch's tip is a different
node after every turn, so keying by it would open a new cursor per
turn. A record still carries an ordinal, and it still orders the batch
the source archive appends, but it no longer decides what is owed.

### At a fork

The turn that opens a branch carries the predecessor of the turn it
replaces, and nothing else about it is special. Its branch runs back
through the shared prefix, whose ids are in `written` already, so the
new turns are owed and the prefix is not. The trunk and the branch need
no ordering between them and no knowledge of each other. Whichever is
written first puts the shared ids in the set, and the other one skips
exactly what it shares.

### Every branch, at the session boundary

Per turn, memory asks for the branch ending at the session's head,
because that is the branch the turn happened on. At the boundary it
asks for all of them: `list_branches` gives every live tip and one
`get_branch` per tip gives the line behind it. Offering a shared prefix
several times costs nothing, since its ids are in `written` already.

The branch under the head is written however little it owes, because
nothing comes back for it afterwards. The others are written once what
they owe reaches the threshold. A branch of one turn is a retry, and a
retry is a reply somebody rejected; a branch long enough to reach a
batch is a line of conversation somebody went down and came back from,
and what was said there belongs in memory like anything else. The idle
allowance that lets a short head branch through does not apply to
them — an abandoned branch's last message is old by definition, so the
allowance would let every retry in.

Turns the user rolled back are gone before memory sees them. `rewind`
marks them and `get_branch` filters them out, so "abandoned on purpose"
is the session store's judgment and not one memory makes again.

### Ids stay

An id is never removed. Dropping the ids of a branch nobody visits any
more looks like tidying, and it is the one operation that breaks what
the set is for: a fork can start at any message, so a message has to
stay recognisable for as long as any future branch could run back
through it, which is as long as the session exists. The set grows with
the session at the rate the source archive already grows, one entry per
message, and one file per thread keeps a turn's cursor read
proportional to that session rather than to every session ever written.

Deleting a session deletes its cursor file. That is the only removal,
and it is safe because the branches that could have run through those
messages go with it.

### An unreadable cursor is an empty one

A cursor file that will not parse says memory does not know what it has
written, and under a set the only safe reading of that is that it has
written nothing. So it reads as an empty set and the thread is offered
whole. One thread rewritten once is the cost, and the write transaction
already folds paragraphs that say the same thing. Raising instead costs
the thread: the failure travels up as a retryable `WriteIncomplete`,
the watcher leaves the session unmarked, and every later pass reads the
same file and reports the same thing, so nothing is ever written and
nothing says why. claude-code makes the same choice when the message
its cursor names has gone missing, for the same reason — rewriting a
batch has a bound, silence does not.

The cursor lives inside the workspace, under `.scriptorium/cursors/`,
so a backup, a git checkout or a restore takes it along with the files
it describes. Kept beside the workspace instead, memory restored from
last week would meet a cursor from today and read the whole week as
written.

### Migrating off the position cursor

An installation upgrading from the position cursor has
`cursors: {thread: {message_id, ordinal}}` in `runtime.json`. It names
the last message written per thread and nothing before it, so the set
cannot be reconstructed from it.

The set is seeded from the source archive instead.
`sources/openprogram/<session-id>.md` carries a `source-id` comment per
archived message, and on the write path a batch is archived before the
cursor advances, so the archive covers everything the old cursor
pointed past. Seeding from it re-writes nothing that was written. It
can cover a little more than the old cursor did, because the archive
records what was handed to the writer: a batch archived by a write that
then failed reads as written and is not offered again. That costs at
most one interrupted batch per thread, once, against re-writing every
session's whole history, which is what discarding the old state would
cost. A workspace with no `sources/` tree, from before the source
archive existed, seeds empty and is written from the start.

`cursors` leaves `runtime.json` once seeded. The counters stay there:
`creation_order`, the local batch and token counts, and the time of the
last global pass.

### What this does not settle

- **A branch is only revisited at the session boundary.** Between
  boundaries the per-turn path offers the head's branch alone, so a
  branch left an hour ago waits for the session to go idle. Nothing is
  lost by waiting, and nothing could be gained by trying earlier: the
  head is written in five places, the one function all of them pass
  through discards the previous value, and no event announces the move.
- **Two branches, one set of topic files.** Records from different
  branches are folded into the same files, and the topic format has no
  way to say that two claims are alternatives from mutually exclusive
  branches. Retrieval returns both, and the nightly reorganize merges
  paragraphs that say the same thing whether or not they came from the
  same line of the conversation.
- **Cross-session spawn.** A spawn branch inside the same session
  shares that session's cursor and archive and needs nothing extra. A
  cross-session spawn's branch root points into another session's
  graph, and the thread here is the session id, so the two halves are
  written under two threads and neither walk crosses into the other.
  Nothing is written twice and nothing is skipped; what is missing is
  the link saying that the sub-agent's branch continues the caller's
  conversation.
- **Compaction summaries.** A summary node takes the predecessor of the
  first turn it covers, which makes it a sibling of the line the head
  is on rather than a member of it, so the ordinary walk sees the raw
  turns and writes those. A head that moves onto the summary's own line
  makes the summary an unwritten assistant turn whose text restates
  turns already in memory, under an id the cursor has never seen.
- **Rows the store gave no id.** `_records` falls back to a positional
  id for a row without one, and that string names different messages on
  different branches. The session store always supplies an id, so the
  fallback is unreachable; under an id-keyed cursor it is wrong rather
  than merely unused, and it goes.
- **`written` means handed to the writer, not cited.** A batch the
  writer folds into one paragraph citing one of its five turns marks
  all five written. That is intended, and it is why the archive is a
  fair seed above, but the cursor is not evidence that a particular
  turn's content reached a file.

## Why the nightly reorganize exists

Writing only ever makes files longer; nothing shortens them. Left alone,
a workspace becomes one enormous file per subject with its timeline cut
into pieces by topic — the shape that makes ordering and counting
questions unanswerable. The 03:00 pass splits files that have grown to
cover several subjects, merges paragraphs that say the same thing, and
repairs links.

It also runs on demand: `openprogram memory sleep`.

A pass reports the files it changed. What to rearrange is the model's
judgment, and a model that judges there is nothing to do does nothing,
silently and correctly under its own criterion: measured on the same
prompt, a single-subject conversation of 520,000 characters that had
been folded into one 34,400-character file survived pass after pass
untouched, because the criterion for splitting is that a file covers
two subjects and that file covers one. Whether that is the right
criterion is a separate question, and an empty list of changed files is
what makes it a question anybody can ask.

## The always-on block

`core.md` is what every session starts with, and it is derived. Its
content is `topics/core.md`, a subject file like any other, rendered
under a 2,000-token budget after every successful write. Nothing writes
to `core.md`: an edit there is replaced by the next render, the same
way an edit to `timeline/` is.

A subject file, because a fact that must be visible in every
conversation is still a fact about something, and it carries the same
block ID and the same evidence footnote as every other fact. The writer
learns one kind of file and one set of rules. Keeping the always-on
block as separate content is what left it with nobody maintaining it:
writing only ever appended to it, the nightly pass only ever looked at
`topics/`, and once it reached the budget the transaction refused
whatever came next, so it froze at whichever facts happened to arrive
first.

The budget is a rendering limit rather than a gate. The render takes
paragraphs in file order until the next one does not fit, and reports
how many tokens it laid down and the block IDs it left out. What it leaves out is still in
`topics/core.md`, still indexed, still reachable by `search` and
`memory_get`, so leaving a paragraph out of the rendered block costs
visibility and nothing else. That is what makes trimming safe without
knowing who wrote what: openclaw separates its automatic lines from its
hand-written ones because its always-on file is the only copy and
dropping a line destroys it. Here the preference lives in the order
instead — a paragraph earlier in the file is rendered first, and moving
one is an ordinary edit that a person or the nightly pass can make.

A workspace that has a hand-written `core.md` and no `topics/core.md`
has that file moved into place the first time the block is rendered. It
already carries block IDs and evidence footnotes, so it is a valid
topic file exactly as it stands. A workspace that has both keeps
`topics/core.md` and lets the render overwrite the loose file, because
that is what being derived means and the content is not at risk either
way.

### What this does not settle

- **Nothing reorders the source file.** The budget decides visibility,
  and preference lives in the order, so a paragraph that arrives after
  the file has grown past 2,000 tokens is written, indexed and
  searchable but never rendered. The nightly pass organizes by subject
  and knows nothing about the budget, so nothing moves it up on its
  own. Losing visibility is not losing content, but the block is what
  the model reads without being asked.
- **The report has no reader.** The render says how many tokens it laid
  down and which block IDs it left out. Nothing consumes that yet, so
  the first sign that the block is over its budget is still somebody
  reading the file.
- **The budget is approximate.** It is counted with `tiktoken`'s
  `o200k_base`, which is not the tokenizer of every model the block is
  injected into.

## What the model sees

- **Every session**: `core.md`, injected as a fenced `<memory-context>`
  block so recalled facts are never mistaken for the user talking now.
- **Every turn**: whatever `search` finds for that message — a BM25
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
    scheduler.py              daemon thread, the 03:00 reorganize
    session_watcher.py        writes an idle session's remainder
    scriptorium/              the shipped implementation
        provider.py           satisfies the contract
        writing.py            accumulate, write, reorganize
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
and `scheduler.start_nightly_reorganizer(model=...)` override it.

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
| Another writer holds the lock | This pass writes nothing and says so; the next turn retries |
| The writer's edits are rejected twice | The batch fails whole — one repair attempt, then nothing is installed and the cursor does not advance |
| A hand edit breaks the format | The edit is validated in a staging copy and never installed; the committed file is untouched and the rejected text is kept for a retry |

Memory never takes a conversation down with it: every provider hook
swallows its own failures and logs them. Swallowed is not forgotten.
`write` returns nothing once a session owes nothing, which is how a
hook that says nothing is read as a hook that had no problem, the same
way a Claude Code hook only speaks up to intervene. Being below the
threshold is silence too: nothing was owed yet. Anything left unwritten
comes back as a `WriteIncomplete` carrying the reason and one more bit,
whether a later pass could finish it. A held lock or an unreachable
model can, so the watcher leaves the session unmarked and tries again.
Content the write transaction refused cannot, so the watcher marks the
session handled anyway and puts the reason on the event bus as
`memory.ingest_ended` with `ok: false`. Retrying refused content
forever only burns model quota, and a failure nobody can see is a
failure that stays.

Both calls report the same way. A per-turn write can hit a held lock
just as an idle one can, and it used to swallow that as "nothing to do
yet" — indistinguishable from the ordinary under-threshold case, so a
turn that never got written said nothing at all.

## Plugin point

`MemoryProvider` (`provider.py`) is the interface between memory and the
agent runtime:

| Hook | When |
|---|---|
| `name` / `is_available()` | Selection |
| `initialize(session_id=)` / `shutdown()` | Session start and end |
| `system_prompt()` | Session start |
| `search(query)` | Before each turn |
| `write(messages, session_id=, force=)` | After each turn, and at a session boundary |
| `extract_before_discard(messages)` | Before context compression |
| `reorganize(**kwargs)` | Nightly |

All of it but `name` has a default, so an implementation only writes
the hooks it has something to do for. One verb per action, and the same
verb on both sides: the name of a hook here is the name of the function
that carries it out in `scriptorium/`, so reading across the two layers
takes no translation.

`extract_before_discard` runs the other direction from the rest and is
easy to read backwards. It stores nothing. The compactor is holding
messages it means to drop and asks memory what in them belongs in the
summary; the text that comes back is folded into that summary, so an
insight outlives the raw turns.

There is no hook for exposing tools. A memory system that ships extra
tools arrives as a plugin, and a plugin already registers commands,
skills, MCP servers, providers, hooks and agents through the
contribution registry. A second private route through this interface
would only be a way to bypass it.

Recalled memory reaches the model inside a `<memory-context>` block
with a system note, so old facts read as background rather than as
something the user just asked for. `system_prompt` and `search` return
text that is already fenced — `fence_memory` does the wrapping, and the
provider applies it. Nothing fences again on the way out: fencing twice
strips the inner block and leaves an empty one.

## Appendix: Implementation status

Everything above runs today except "The always-on block", "The write
cursor" and the two subsections after it, "Every branch, at the session
boundary", and the sentence saying a reorganize pass reports the files
it changed. Where those four come from, and what was decided against,
is [`memory-adoption.html`](memory-adoption.html).

`core.md` is written rather than rendered, and once it is full it stops
accepting stable facts permanently. The writer edits it directly on its
own judgment, on instructions in `prompts/write.py` and
`prompts/system.py`; `_synchronize` in `management/block_views.py`
raises once the file passes 2,000 tokens, which refuses the whole
transaction including that turn's topic edits; and the repair guidance
in `management/agent.py` then tells the writer to leave it alone and
put the fact in a topic file. Nothing shortens it either:
`organize_topics` builds its file list from `topics/**.md` only.

The refusal is wider than the writer's own edit. `_synchronize` runs on
every `commit_edits`, and it measures the staged `core.md` rather than
the change, so a `core.md` already over the budget on disk refuses
every transaction after it, including one that edits only topic files
and one that edits nothing at all. Measured on a workspace whose
`core.md` a person had enlarged in an editor: a transaction with no
edit in it raised `Core Memory exceeds 2000 tokens: 4004`, and the
guidance handed to the writer was "core.md is full. Leave it alone and
put this in a Topic file", which is what it had already done. Two
rejected turns raise `COMMIT_REJECTED`, which is not in
`RETRYABLE_CODES`, so the watcher marks the session handled and that
conversation never reaches memory. The same happens to the next
session, and to every one after it, until a person shortens the file by
hand. `core.md` is writable by hand on purpose
(`WRITABLE_FILES` in `management/transaction.py`), and it is plain
Markdown in the workspace, so nothing stands between an editor and
this state.

The design lands in `runtime/derived_views.py` (render the block beside
the other derived views), `management/block_views.py` (a budget where
the gate is, reporting the block IDs it left out),
`management/agent.py` (the repair line goes), and the two prompts,
`prompts/write.py` and `prompts/system.py`, which each tell the writer
to edit `core.md` and are pointed at `topics/core.md` instead. Being
derived rather than authored also takes `core.md` out of
`WRITABLE_FILES` and out of the hand-edit path `management/patching.py`
documents, and `store.ensure()` stops seeding a bare `# Core` file.
Five special cases go with it, all of them there because `core.md` is
handled as a topic file that happens to sit outside `topics/`:
`workspace.baseline`, `MemoryWorkspace.shell`,
`transaction.committed_baseline` and `_validate_topic_contract` each
fold its block IDs into the set the contract is checked against, and
`topic_normalization` appends it to the files it assigns IDs in. A
rendered block's IDs are always a subset of the topics' own, so all
five go.

Migration needs no step of its own. `topics/core.md` is created from
the existing `core.md` the first time the block is rendered, the render
then overwrites `core.md` in place, and a workspace that never had a
`core.md` renders an empty block. Nothing is read from the old file
after that first pass.

`reorganize` returns `{"status": ..., "topics": N}`, where `N` counts
the files it looked at rather than the files it changed.

Both write paths ask `get_branch` for the head's branch alone
(`memory/session_watcher.py` and `scriptorium/writing.py`), so no other
branch is ever offered. `list_branches` and the per-tip `get_branch`
are already there to build on.

`RuntimeStateStore.load` calls `json.loads` on `runtime.json` with
nothing around it, so a file that will not parse raises rather than
reading as empty.

What runs in place of "The write cursor" is a position cursor:
`runtime.json` holds
`cursors: {thread: {message_id, ordinal}}`, `runtime/online.py` claims
a record only when its ordinal is higher than the stored one, and
`scriptorium/writing.py` builds that ordinal from the row's index in
the branch it read. The index is a position in one branch, so a branch
forked from an earlier message numbers its turns below the stored
ordinal and reads as already written.

How much of it reads that way depends on how far the branch has run.
While it is no longer than what the cursor has already counted, all of
it is skipped: measured on a session whose stored ordinal was 9, a
branch forking at the fourth message and running six turns was offered
as zero records. Once it runs past that high-water mark the tail is
claimed and the turns before it never are: measured on the same
session, a branch of fourteen new turns was offered as its last seven,
so what reached the writer began in the middle of a conversation whose
first seven turns memory will not be offered again. The first case
loses a branch. The second case writes a fragment and calls the whole
branch done.

The design lands in `runtime/state.py` (the state shape and the
per-thread cursor files), `runtime/online.py` (what a session owes, and
adding ids after the transaction installs), and `_records` in
`scriptorium/writing.py` (identity by message id, and dropping the
positional fallback). `advance_cursor` and its "cursor cannot move
backwards" check go with the ordinal; a set has no direction to move
in. Migration is the seeding pass described above, reading
`sources/openprogram/<session-id>.md` once per thread and then dropping
`cursors` from `runtime.json`.

Writing every branch rather than the head's alone is a separate change
on top, and it lands in `_branch` in `scriptorium/writing.py` and in
`memory/session_watcher.py`, which today both ask `get_branch` for one
line. It also needs `should_incremental_write` in `runtime/state.py` to
take its idle allowance as an argument, because that allowance is what
would otherwise let every one-turn retry through.
