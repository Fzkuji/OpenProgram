# Knowing which turns memory has written

A session is a DAG, not a line. Re-asking an earlier message, retrying a
reply and spawning a sub-agent all fork the chain, so one session holds
several branches that share a prefix and diverge after it
([`../runtime/dag/overview.md`](../runtime/dag/overview.md) §4).
Everything said on a branch belongs in memory, and nothing said on the
shared prefix belongs in memory twice. This note is about the one fact
that decides both: which turns memory has already written.

Four layers, answering four different questions. Layer 1 records the
pre-migration implementation and why it was wrong. Layer 2 is what the reference frameworks
under `references/` do, including the ones that do nothing. Layer 3 is
the change to make next, down to the files and the size of the diff.
Layer 4 is the shape this would have if the current implementation were
not in the way, and why none of it is next.

The visual companion is [`written-marker.html`](written-marker.html);
the memory subsystem around it is [`overview.md`](overview.md).

---

## Layer 1 — What the migration replaced

This layer is retained as the problem statement. The appendix records the
current implementation.

### One number per session

The record of what has been written is a position cursor, kept outside
the session store, in the memory workspace's runtime file:

```
<state>/memory/.scriptorium/runtime.json
    {"cursors": {"<session-id>": {"message_id": "a3f1c2", "ordinal": 9}}}
```

`RuntimeState.cursors` in
`openprogram/memory/scriptorium/runtime/state.py` holds it. The key is
`thread_id`, which `scriptorium/writing.py` fills with the session id, so
a session with six branches has one cursor between them.

Three functions are the whole mechanism:

| Function | File | What it does |
|---|---|---|
| `_records` | `scriptorium/writing.py` | Turns the branch into `SourceRecord`s. `ordinal=index`, the row's position in the list it was handed, skipped rows included |
| `OnlineMemoryRuntime.pending` | `scriptorium/runtime/online.py` | Keeps a record only when `record.ordinal > stored ordinal` |
| `RuntimeState.advance_cursor` | `scriptorium/runtime/state.py` | Stores the batch's last ordinal, after the write transaction installs |

The per-turn call reaches them through
`dispatcher/__init__.py:_memory_write` → `ScriptoriumMemoryProvider.write`
→ `writing.write` → `write_session`. The session-boundary call reaches
them through `memory/session_watcher.py:_process_session`. Both ask
`SessionStore.get_branch(session_id)` for one branch: the one ending at
the session's head.

### Where the number comes from

An ordinal is not a property of a message. `get_branch` walks
`predecessor` edges back from a head and hands back a list; the ordinal
is what counting the rows of that list produces
(`_records` does `for index, message in enumerate(messages)`). It comes
into existence at the moment the chain is flattened, it describes the
flattening, and it is gone again when the list is. Before the walk there
is no number. After it, the number and the message's own id have nothing
to do with each other.

Two things follow. The same message reached along two lines gets two
numbers, and two different messages at the same depth on two lines get
the same one: the trunk's fourth message and a branch's fourth message
are not the same message and are both "four". And one line's numbering
is not stable between two reads either, because `get_branch` filters out
the turns `rewind` marked (`session_store.py` drops any node whose
metadata carries `rewound`) and compaction splices a summary node in at
the position of the first turn it covers, and either shifts every row
after it.

### What that costs, measured

A cursor holding "written through nine" reads a branch forked at the
third message, whose turns number from zero, as entirely written and
offers none of it. A branch that runs past nine gives up only its tail.
Measured on a session whose stored ordinal was nine:

| Branch forked at m3 | Turns the branch holds | Turns offered to the writer |
|---|---|---|
| short | 5 | **0** |
| long | 11 | **2** (the last two) |

The second row is the worse one. It writes a fragment starting in the
middle of a conversation, records the whole branch as written, and
returns success.

The two counts are measurements, not arithmetic. The ordinal axis counts
every row in the branch, tool rows and runtime-scheduled turns included,
because `_records` enumerates the list before it filters it. So how many
rows cross the stored ordinal, and how many recorded turns that leaves,
are two different numbers.

### The shape refuses the correction

`advance_cursor` raises `cursor cannot move backwards` when a new
ordinal is below the stored one, on the reasoning that a cursor only
ever moves forward. Moving backwards is exactly what a branch forked
from an earlier message needs, so a caller that worked out the right
answer could not record it. That is not a missing check. A monotonic
counter and a branching conversation are different shapes, and one
cannot be repaired into the other.

### The quieter failure is the worse one

A branch read as entirely written produces nothing, and nothing at least
looks like nothing. A branch that gives up only its tail produces a
write that succeeds: memory gains a fragment beginning mid-conversation,
the whole branch is recorded as done, and no code path reports anything.
Where the bookkeeping moves last, a failure defaults to doing the work
again. Here it defaults to skipping the work quietly and keeping a
decapitated record of it.

---

## Layer 2 — What the reference frameworks do

Nine directories sit under `references/`. `claude-code/` holds five files
of one tool implementation and no session code, so eight frameworks carry
an answer. Four of the eight have long-term memory at all; the other four
are recorded here because not having it is a design choice too, and
because their conversation shape is what makes the question easy or hard.

| Framework | Conversation shape | Long-term memory | What decides which turns | Where that record lives | What a fork does to it |
|---|---|---|---|---|---|
| **claude-code-leaked** | DAG. `parentUuid` per entry, one append-only JSONL per session | Yes. `extractMemories` per query loop, `autoDream` across sessions, `memdir` for retrieval | A message-UUID cursor, `lastMemoryMessageUuid` | In process only. A closure variable, never written to disk | `/branch` copies to a new file keeping the original UUIDs, so the cursor still resolves. `/rewind` can slice the cursor's message away, and the counter then counts every message rather than returning zero |
| **codex-cli** | Straight line per rollout `.jsonl`. A fork is always a new thread and a new file, linked by a `history_base` pointer | Yes. Two phases in `codex-rs/memories`: per-rollout extraction, then global consolidation | A thread-keyed row: `jobs.input_watermark` and `last_success_watermark`, with `ownership_token` and `lease_until` | A separate SQLite file, `memories_1.sqlite`, keyed by `thread_id` | A fork is a new `thread_id`, so there is no row, so the thread is extracted from scratch. Nothing joins against the lineage, so the shared prefix is extracted a second time |
| **openclaw** | DAG. `id`/`parentId` per entry, one JSONL per session, with a `readBranch` walk available | Yes. `dreaming-phases` ingests, `short-term-promotion` promotes into `MEMORY.md` | A line-offset cursor, `lastContentLine`, per session file, plus a `seenMessages` hash set | `memory/.dreams/session-ingestion.json`. `promotedAt` exists, on the derived candidate in `short-term-recall.json` | A fork writes a new file, which starts at line 0, so the copied prefix is ingested again. An in-place retry changes the file's hash and forces a rescan from line 0 |
| **hermes-agent** | Straight line per session in SQLite. Branching is at session granularity through `parent_session_id`, and `/branch` copies every message into a new session row | Yes. A built-in `MEMORY.md`/`USER.md` tool, plus eight pluggable providers behind `MemoryProvider` | Nothing, for memory: `sync_all` fires once per completed turn, so program order is the cursor. The SQLite flush keeps its own index, `_last_flushed_db_idx` | Outside the transcript, in process. One plugin keeps a `_synced` flag per message in its own cache | Every fork site re-baselines `_last_flushed_db_idx` by hand. `on_session_switch` is offered to the providers, and seven of the eight do not implement it |
| **pi-mono** | DAG. `id`/`parentId` per entry, one JSONL per session. The storage interface has no way to change an entry: annotating appends a `label` entry pointing at the target, moving the active leaf appends a `leaf` entry pointing at it | No | Not applicable. Its branch summaries walk the tree instead, at navigation time, storing nothing | Not applicable | Nothing can go stale, because the walk is recomputed on every navigation |
| **opencode** | Straight line. SQLite, one `seq` per session | No | Not applicable | Not applicable | `revert` marks a point, and the next prompt hard-deletes every message at or after it. No older branch survives |
| **pi-ai** | None. A stateless streaming client; the caller supplies the whole message array on every call | No | Not applicable | Not applicable | No branch concept |
| **weclaw** | None of its own. The CLI and ACP backends cache a downstream session id; the HTTP backend keeps the last twenty turns in process | No | Not applicable | Not applicable | No fork concept |

### Nobody puts the record on the turn

Of the four with memory, none writes "memory has taken this" onto a
conversation entry. openclaw comes closest and stops short: `promotedAt`
is a real processed-flag, but it lives on a derived candidate record in
`short-term-recall.json`, not on the transcript line the candidate came
from. The other three keep the record in a closure variable, in a
separate SQLite file, and in program order respectively.

Three of the eight store a conversation that branches per message
(claude-code-leaked, openclaw, pi-mono). Three keep it in a straight
line, branching only by starting a new thread, session or file
(codex-cli, hermes-agent, opencode). Two hold no conversation of their
own (pi-ai, weclaw). A straight line makes a position an identity,
because there is only ever one list, so most of them never meet this
question — and of the three that do store a DAG, the one with the most
developed memory pipeline reads its transcript file flat, top to bottom,
with no parent walk at all.

### The two shapes that are actually in use

**Coarsen the unit until branching stops mattering.** codex-cli keys its
extraction jobs by thread, so a fork is simply a new key with no row.
openclaw keys ingestion by session file, so a fork is a new file starting
at line 0. hermes-agent fires once per completed turn and keeps no cursor
for memory at all, which is the same move taken to its limit: the unit is
one turn and the bookkeeping is program order.

**Derive the delta from the tree.** pi-mono computes what a branch
summary covers at navigation time: walk from the leaf being abandoned up
to its common ancestor with the new leaf, collect what lies between,
store nothing. Its storage supports this because it is strictly
append-only — there is no call that changes a stored entry, so a cursor
move is itself an appended `leaf` entry and an annotation is an appended
`label` entry.

### Everyone else's fork failure is duplication. Ours is omission

The two file-keyed designs both pay at a fork, and both pay the same way.
codex-cli extracts a forked thread from scratch, with no join against
`history_base`, so the prefix it inherited is extracted twice. openclaw's
fork copies the active branch into a new file whose cursor starts at
line 0, so the copied prefix is ingested twice; its in-place retry path
changes the file hash and rescans the whole file from line 0 as well.
Neither loses a turn. Both re-read turns they have already read.

Our position cursor makes the opposite error, and it is the worse one.
Duplication is recoverable: the nightly reorganize merges paragraphs that
say the same thing, so a turn written twice costs a model call and
corrects itself. Omission is not recoverable, because nothing ever comes
back for those turns.

### What is worth taking

**Reprocess rather than go silent.** claude-code-leaked's cursor counter
carries the comment that returning zero *"would permanently disable
extraction for the rest of the session"*, and falls back to counting
every message when the cursor's UUID is gone. That it is a decision
rather than a default is visible in the same codebase: the sibling
counter beside it has no such fallback and silently stays at zero. Layer
3 applies this in two places, to a lost mark and to an unreadable
`runtime.json`.

**Appending a marker that points at the node is a real alternative to
changing the node.** pi-mono demonstrates that a whole session store can
work this way. Layer 3 turns it down for a stated reason, not for lack of
a precedent.

Nothing else transfers. Both working shapes above depend on the unit
being a whole file or a whole thread, and adopting either means giving up
writing memory from a live session as it runs.

---

## Layer 3 — Current implementation: a mark on the node

The boundary is keyed on what identifies a message: its node. The record
of "memory has written this turn" is stored on that turn.

### Where the mark goes

On the node, in `metadata`, under a key naming the memory provider that
wrote it, with the memory workspace's identity as the value:

```json
{"id": "a3f1c2", "role": "user", "predecessor": "9d0e77",
 "metadata": {"memory_written_scriptorium": "w-4f21c8e0"}}
```

The key names the provider because the memory interface is pluggable and
two providers would each want their own answer; one boolean shared
between them would be wrong the day a second one exists. The value names
the workspace because a mark travels with the node. There is no session
export format, a session directory is self-describing JSON, so copying
the directory is how a session moves and the copy carries every mark
with it. Arriving on a machine whose memory workspace is empty, it would
arrive with every turn already claiming to have been written; naming the
provider does not separate the two, because the other machine runs the
same provider. The workspace does. A walk stops only on a mark naming
the workspace it is walking for, so a restored backup keeps its marks
and a session copied in from elsewhere is written from the start.

Nothing has to be taught to carry the field. `metadata` is a free-form
dict on both paths: `_msg_adapter._msg_to_node` puts every field it does
not recognise into it, and `_node_to_msg` spreads every metadata key back
onto the top level of the dict `get_branch` hands out. That last detail
is why the key has to be a name no message field uses — `id`,
`session_id`, `role`, `content`, `predecessor`, `caller`, `timestamp`,
`token_model`, `function`, `extra`, `status` are taken;
`memory_written_scriptorium` collides with none of them.

Nor is writing to a stored node new. `_rewind.py` stamps `rewound` on
the turns it retires and rewrites their history files;
`internals/_revert.py` does the same with `reverted`;
`dispatcher/finalize.py` rewrites a node file in place to stamp its
shadow-git checkpoint. And `GraphStoreShim.update` already does exactly
the operation the mark needs — merge a metadata patch into a node,
rewrite that node's history file, touch nothing else.

The append invariants are not in the way. `_check_append_invariant` runs
when a node is appended; a mutation never reaches it. What it reads is
`predecessor`, `caller`, `role`, `input` and three metadata keys
(`display`, `spawn_branch_root` with `source`, `covers_ids`). The
read-side walk in `get_branch` reads the same set plus `rewound`.

### How a read uses it

`_records` already reduces a branch to the rows memory records: user and
assistant roles, non-empty text, runtime-scheduled turns dropped. The
walk runs on that filtered list, from the end:

```python
def unwritten_turns(records, marked_ids):
    """The trailing run of records nothing has marked, oldest first."""
    out = []
    for record in reversed(records):
        if record.message_id in marked_ids:
            break
        out.append(record)
    out.reverse()
    return out
```

Turns memory never records — a sub-agent's completion notice, a merge
prompt — are stepped over for free, because they were never in
`records`. That is the whole read. It replaces
`OnlineMemoryRuntime.pending` and the ordinal comparison inside it.

The batch handed to the writer stays what it is today: `_first_batch`
takes the leading turns that together reach the threshold, so a day-long
backlog is still written in several passes.

### When the mark goes on

Two conditions, not one. The write transaction has to install, **and**
it has to have changed a file.

A writer that spends every one of its turns on the same rejected edit
finishes without raising anything and without touching a file, and a run
judged by its exit alone reads that as a batch written. Measured on this
same writer: twenty turns spent on one rejected edit, a successful
return, and a topic file one byte long. The audit already answers the
question — `writing._changed_files(audit)` collects the topic paths of
every `commit` entry with `status == "ok"` — so the writer closure
returns that list and an empty list marks nothing.

That rule is not special to the mark. Any state that says a thing is
done has to be checkable against something the work produced, which is
also why the nightly pass reports the files it changed rather than the
files it looked at. The absence of an error is not a product.

The order of the three steps follows from which of them can be replayed:

1. **Archive the evidence.** `archive_source_records` is append-only and
   addressed by content — it reads the `<!-- source-id:… -->` comments
   already in the file into a `known` set and skips them — so a batch
   archived twice leaves the archive byte for byte as it was.
2. **Write the topic files.** They carry block IDs and footnotes that a
   half-write would corrupt, so they go in through a transaction that
   installs whole or not at all.
3. **Mark the turns.**

A writer that dies between 2 and 3 leaves evidence nobody cites, no
marks, and the same batch unwritten next time, which is a redo. Marking
first would turn the same crash into losing those turns silently, and
nothing recovers from that.

### At a fork

The turn that opens a branch carries the predecessor of the turn it
replaces, and nothing else about it is special. Walking back from the
branch's tip reaches the shared prefix, whose turns are marked already,
and stops there. The trunk and the branch need no ordering between them
and no knowledge of each other. Whichever is written first marks the
shared turns, and the other one stops at them.

The measured cases from Layer 1, under this rule: the five-turn branch
is offered as five turns, the eleven-turn branch as eleven. In the
figures the same case is drawn against a stored ordinal of nine, where
the branch forked at m3 has two and eight turns of its own to give.

### Every branch, at the session boundary

Per turn, memory asks for the branch ending at the session's head,
because that is the branch the turn happened on. At the boundary it asks
for all of them: `SessionStore.list_branches` gives every live tip and
one `get_branch` per tip gives the line behind it. Walking a shared
prefix several times costs nothing, since the walk stops at its first
marked turn.

The branch under the head is written however little it holds, because
nothing comes back for it afterwards. The others are written once what
they hold unwritten reaches the threshold. A branch of one turn is a
retry, and a retry is a reply somebody rejected; a branch long enough to
reach a batch is a line of conversation somebody went down and came back
from, and what was said there belongs in memory like anything else. The
idle allowance in `should_incremental_write` that lets a short head
branch through does not apply to them, because an abandoned branch's
last message is old by definition and the allowance would let every
retry in.

Turns the user rolled back are gone before memory sees them. `rewind`
marks them and `get_branch` filters them out, so "abandoned on purpose"
is the session store's judgment and not one memory makes again.

### When a mark is lost

A node file is written without a lock, so marking a node while something
else rewrites the same node loses one of the two writes whole rather
than merging them. Memory marks turns that are already finished, and the
per-turn write runs on the turn's own thread after the turn has been
persisted, which leaves the idle watcher meeting a session that has just
woken up as the window.

A lost mark strands everything older than it: the walk stops early. The
rule that follows is the one claude-code already applies to its own
cursor — when the bookkeeping cannot be trusted, reprocess rather than
go silent. Two places carry it:

- `RuntimeStateStore.load` reads `runtime.json` with a bare
  `json.loads`, so an unparseable file raises and that workspace never
  writes again. It returns an empty state instead.
- A walk that reaches the start of a branch without meeting a mark
  offers the whole branch. That is already what the code above does, and
  it is the behaviour to keep rather than guard against.

### Migrating off the position cursor

An installation upgrading from the position cursor has
`cursors: {thread: {message_id, ordinal}}` in `runtime.json` and no
marks on any node, so a walk back from a head would collect the whole
session.

The source archive says which turns were handed to the writer. Legacy
`sources/openprogram/<session-id>.md` files carry a validated anchor and
`<!-- source-id:openprogram/<session>/<message> -->` comment per archived
message. Canonical `sources/openprogram/_v2/<session-id>.md` files carry
the same source id inside strict `record-lines` frames. Migration consumes
only the v2 parser's valid prefix: record content cannot create an id, and
parsing never resumes after an invalid or truncated frame. On the write path a
batch is archived before the cursor advances
(`OnlineMemoryRuntime.process` calls `workspace.archive_source_records`
before `state.advance_cursor`), so the archive covers everything the old
cursor pointed past.

Marking those nodes re-writes nothing that was written. It can cover a
little more, because a batch archived by a write that then failed reads
as written and is not offered again; that costs at most one interrupted
batch per thread, once, against re-writing every session's whole
history, which is what placing no marks would cost. A workspace with no
`sources/` tree, from before the source archive existed, is written from
the start.

`cursors` leaves `runtime.json` once the marks are placed. The counters
stay: `creation_order`, the local batch and token counts, and the time
of the last global pass.

### What it costs the session store

**Nothing there hashes a whole tree.** The one full-byte tree hash in
this system is the memory workspace's revision — `workspace_revision`
in `scriptorium/management/transaction.py`, rooted at the directory
`memory/store.py:root()` returns, `<state>/memory`. Sessions live under
`<state>/sessions`. The two are disjoint, so a marked node cannot read
as a concurrent memory write.

**The session index cache does rebuild once.** Staleness is
`GitSession.stat_fingerprint`, which is the `history/` directory's
mtime plus `meta.json`'s mtime and size, and a rewrite bumps the
directory mtime exactly as an append does. Another process holding that
session rebuilds its index once on next `_open`: measured at **14 to 50
milliseconds for a 289-node session of 4.2 MB**, and self-limiting,
because `mark_synced` records the new fingerprint rather than repeating
per read.

**The cost that grows is git.** A session directory is committed with
`git add -A` once per turn (`GitSession.commit_all`), so every node
whose bytes changed becomes a new blob. Marking the batch a write
ingested is a few dozen nodes per write, and the repository grows by
what the batch weighs. Marking every node in the session on every write
would add the session's whole weight per turn, which is quadratic over
its life. **So the mark goes on the batch and never on a full pass over
the session.**

**Two things must not happen.**

- The mark must not move the session's `updated_at`. The idle watcher
  decides a session is already handled by comparing that exact value
  (`session_watcher._scan`: `if processed.get(sid) == updated_at`), so
  moving it hands the session straight back to a forced write and the
  model call inside it. `SessionStore.append_message` and
  `GraphStoreShim.append` both bump it; the marking path must not go
  through either.
- The mark must not go through the path that records the session as
  synced. `GitSession.write_history` calls `mark_synced()` as a side
  effect, and that fingerprint would cover writes the marking process
  never read, so an index that skipped them would never rebuild. The
  marking path opens through `SessionStore._open` — which rebuilds a
  stale index before handing back `(git, idx)` — and then rewrites the
  node file with `atomic_write_text` directly, which is what
  `GraphStoreShim.update` and `dispatcher/finalize.py` already do.

### The case against the mark, and why it still wins

The honest argument for keeping the books outside the session store:

> The position cursor is an O(1) read of one small file, and it sits
> outside the session tree entirely. A mark on the node writes the same
> fact into a second place — a place whose directory mtime is the
> change detector everything else relies on. And an external set is
> exact where a walk is not: with a set, a lost entry offers that turn
> again; with a mark, a lost mark strands every turn older than it.

Both halves of that are true, and the second half is the real price. The
walk assumes the marks form a prefix of the branch. That holds because a
batch is always the leading unwritten turns, so marking fills in from the
oldest end — but it is an assumption, not a checked invariant. An
external set needs no such assumption.

What decides it is the first half. "Outside" is only cheap while the
books are a single number. Once they have to stay right across a fork,
outside means a **set** of message ids, and a set brings a file layout,
a migration, a rule that an id is never removed (a fork can start from
any message, so every message must stay recognisable for as long as the
session exists), and a read whose cost grows with the session rather
than with the backlog:

| | A mark on the node | A set of ids beside memory |
|---|---|---|
| Working out what is unwritten | Walk back from the tip, stop at the first marked turn | Walk the whole branch, subtract the set |
| At a fork | The walk reaches the shared prefix, finds it marked, stops | The prefix's ids are in the set, so they are skipped |
| What is stored | One field on a node that already exists | One file per thread holding every id ever written |
| How it grows | Not at all | One entry per message, kept as long as the session |
| What one read costs | A few steps back from the tip | The thread's whole list |
| Migration | Mark what the source archive covers | Seed the set from the same archive |
| A mark or an entry lost | The walk stops early, everything older is stranded | The turn is offered again |
| Coupling | Memory writes one field into the session store | Memory keeps its own books |

The session store already keeps, per node, where the turn came from,
whether it is the runtime talking to itself, which branch it roots, and
whether `rewind` retired it. One more field saying memory has written
this turn is the same kind of thing. And the index rebuild the mark
triggers is bounded and measured, above.

Appending a marker node that points at the turn — pi-mono's shape — is
the third option, and it is worse here: it would put a node in the
conversation graph for every batch written, and every reader of the
graph would then have to know to skip them.

### Files to change, and how much

Ordered by dependency. Nothing here needs a new file except the
migration.

| # | File | Change | Size |
|---|---|---|---|
| 1 | `openprogram/store/session/session_store.py` | New `merge_node_metadata(session_id, node_id, patch)`: `_open`, merge into `node.metadata`, rewrite the history file with `atomic_write_text`. No `_persist_meta`, no `updated_at`, no `write_history` | ~20 new |
| 2 | `openprogram/store/session/graphstore_shim.py` | `update` delegates its metadata rewrite to #1 instead of repeating it | ~12 removed |
| 3 | `openprogram/memory/store.py` | New `workspace_id()`: read or generate a hex id in `state_dir()` | ~12 new |
| 4 | `openprogram/memory/scriptorium/runtime/state.py` | Drop `RuntimeState.cursors` and `advance_cursor`; keep the counters; `RuntimeStateStore.load` returns an empty state on an unreadable file | ~10 removed, ~4 changed |
| 5 | `openprogram/memory/scriptorium/runtime/online.py` | `pending` becomes `unwritten_turns(records, marked_ids)`; `process` takes a `mark` callback and calls it only when the writer reported changed files | ~25 changed |
| 6 | `openprogram/memory/scriptorium/writing.py` | `_records` drops the positional-id fallback; `write_session`'s writer closure returns `_changed_files(audit)` and supplies the `mark` callback; `_pending` reads marks | ~40 changed |
| 7 | `openprogram/memory/scriptorium/writing.py` | `write(force=True)` asks `list_branches` and runs one pass per tip, head's branch first | ~30 new |
| 8 | `openprogram/memory/scriptorium/runtime/mark_archived_turns.py` | One-time migration: parse `source-id` comments out of `sources/`, mark those nodes, delete `cursors` | ~50 new |

Tests, all in `tests/unit/`:

| File | What it has to prove |
|---|---|
| `test_memory_writing.py` (existing, 515 lines) | `_pending` assertions currently read the cursor; they move to marks. Existing behaviour otherwise unchanged |
| `test_memory_write_timing.py` (existing) | Threshold and idle behaviour, unchanged |
| new: `test_memory_written_marker.py` | The two measured cases: a branch forked at m3 with 2 and with 8 unwritten turns, both offered whole. A shared prefix written once. A rejected batch marking nothing. A mark from another workspace ignored. The migration seeding from `sources/` |

Roughly **200 lines changed and 130 new across seven files**, plus one
new test file. Steps 1–6 are one coherent change and can land together;
7 is separable and can follow; 8 has to be right the first time, because
a migration that over-marks loses conversation and a migration that
under-marks rewrites history. The measured index rebuild (14–50 ms) is
the only runtime cost worth re-measuring after the change.

### What this still does not settle

- **Nothing checks that the marks form a prefix.** The walk stops at the
  first marked turn it meets and trusts that everything behind it is
  marked too.
- **A branch is only revisited at the session boundary.** Between
  boundaries the per-turn path offers the head's branch alone, so a
  branch left an hour ago waits for the session to go idle. Nothing is
  lost by waiting, and nothing could be gained by trying earlier: the
  head is written in five places, the one function all of them pass
  through discards the previous value, and no event announces the move.
- **Two branches, one set of topic files.** Records from different
  branches are folded into the same files, and the topic format has no
  way to say that two claims are alternatives from mutually exclusive
  branches. Retrieval returns both.
- **Cross-session spawn.** A spawn branch inside the same session is an
  ordinary part of that session's graph. A cross-session spawn's branch
  root points into another session's graph and terminates the walk
  there, so the two halves are written under two sessions and neither
  walk crosses into the other. Nothing is written twice and nothing is
  skipped; what is missing is the link saying that the sub-agent's
  branch continues the caller's conversation.
- **Compaction summaries.** A summary node takes the predecessor of the
  first turn it covers, which makes it a sibling of the line the head is
  on rather than a member of it, so the ordinary walk sees the raw turns
  and writes those. A head that moves onto the summary's own line makes
  the summary an unwritten assistant turn whose text restates turns
  already in memory.
- **A mark means handed to the writer, not cited.** A batch the writer
  folds into one paragraph citing one of its five turns marks all five.
  That is intended, and it is why the archive is a fair migration seed
  above, but a mark is not evidence that a particular turn's content
  reached a file.

---

## Layer 4 — The shape with nothing in the way

Layer 3 answers "what do we build next". This answers "what would this
be if it were designed from scratch". The three below are not staged
versions of the mark; they are a different arrangement of the same
problem.

### Memory's own content is the record

The mark and the position cursor are both a second copy of a fact memory
already holds. `archive_source_records` reads
`<!-- source-id:… -->` comments out of `sources/<provider>/<thread>.md`
into a `known` set on every single write, precisely so it can skip turns
it already archived. That set **is** the answer to "which turns has
memory taken", computed from memory's own files, on the hot path,
today.

Designed from scratch, nothing would be stored anywhere else. Unwritten
turns would be a query: reachable from any live tip, not cited by any
memory record. That has properties neither the cursor nor the mark has.

- It cannot disagree with memory, because it is memory. Delete a topic
  file's paragraph and its turns become unwritten again, which is the
  correct answer and one that no external record and no node field can
  give.
- It survives a session being copied to another machine with no
  workspace identity, no import path, and no migration.
- It has no lost-mark case, because there is nothing to lose that is not
  also the loss of the memory itself.

Three things stop it being Layer 3. The archive is per-thread markdown
parsed by regex, so a read is O(file) and grows with the session — the
same cost that argues against the external set, arriving from the other
direction. Making it cheap means an index, and an index is a derived
cache that needs an invalidation story, which is a bigger change than
the whole of Layer 3. And "archived" is not "cited": the archive is
written before the writer runs, so a crash between the two reads as
written. Layer 3 uses exactly that looseness deliberately, once, to seed
the migration; relying on it every turn would need the archive to move
inside the transaction that installs the topics, which is a change to
the transaction rather than to the bookkeeping.

### A record knows which branch it came from

Today two branches fold into the same topic files and the format has no
way to say that two claims are alternatives. The nightly pass merges
paragraphs that say the same thing whether or not they came from the
same line of conversation, and retrieval returns both to a reader who is
on one line only.

Designed from scratch, a written record would carry the branch it came
from, retrieval on a branch would prefer that branch's records, and a
claim contradicted on another line would be visible as a fork rather
than as a contradiction. The gap is in three places at once: the topic
format has no field for it, `retrieval/` has no branch filter, and the
nightly reorganize prompt has no notion of two records being
alternatives. It is not next because none of those is a small change and
the current behaviour is wrong only when two branches actually disagree.

### Memory is told, not polled

Two paths write memory, and both poll. `_memory_write` runs after every
turn and asks a threshold question; the idle watcher wakes every five
minutes and compares `updated_at` against a 30-minute cutoff. A branch
somebody left an hour ago is not written until the whole session goes
idle, because nothing announces that a tip stopped moving.

Designed from scratch, the session store would say when a branch tip
stops moving and memory would react. What is in the way is stated in
Layer 3: the head is written in five places, the one function all of
them pass through discards the previous value, and no event carries the
move. Adding the event is small; making all five paths go through it is
not, and nothing is currently lost by waiting.

---

## Appendix: Implementation status

As of 2026-08-10, Layer 1 describes the implementation that was replaced,
Layer 2 remains the open-source framework audit, and Layer 3 is implemented.
Session nodes carry `metadata.memory_written_scriptorium = <workspace-id>`;
pending work is the unmarked suffix of each branch; a successful non-empty
write marks exactly its source batch; and forced session-boundary writing
handles the current head before other live branch tips.

`RuntimeState.cursors` and `advance_cursor` have been removed. An installation
that still has legacy `cursors` performs one migration before pending work is
computed: validated legacy headers and strict v2 valid-prefix frames are
merged per session, their source nodes are marked, and only then is the old
field removed. The v2 migration does not trust record content or resume after
a malformed tail. Layer 4 remains unimplemented and unscheduled.
