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

### Why a position cannot be an identity

An ordinal is not a property of a message. `get_branch` walks
`predecessor` edges back from a head and hands back a list, and the
ordinal is what counting the rows of that list produces. It comes into
existence at the moment the chain is flattened, it describes the
flattening, and it is gone again when the list is. Before the walk
there is no number. After it, the number and the message's own id have
nothing to do with each other.

Two things follow. The same message reached along two lines gets two
numbers, and two different messages at the same depth on two lines get
the same one: the trunk's fourth message and a branch's fourth message
are not the same message and are both "four". And one line's numbering
is not stable between two reads either, because `get_branch` filters
out the turns `rewind` marked and compaction splices a summary node in
at the position of the first turn it covers, and either shifts every
row after it.

A cursor holding "written through nine" therefore reads a branch forked
at the third message, whose turns number from zero, as entirely written
and offers none of it. A branch that runs past nine gives up only its
tail, which writes a fragment starting in the middle of a conversation
and then marks the whole branch written. Measured on a session whose
stored ordinal was nine: a five-turn branch was offered as zero turns,
and an eleven-turn branch as its last two.

The shape refuses the correction as well. `advance_cursor` raises when
a new ordinal is below the stored one, on the reasoning that a cursor
only ever moves forward. Moving backwards is exactly what a branch
forked from an earlier message needs, so a caller that worked out the
right answer could not record it. That is not a missing check. A
monotonic counter and a branching conversation are different shapes,
and one cannot be repaired into the other.

The two failures are not equally loud, and the quieter one is worse. A
branch read as entirely written produces nothing, and nothing at least
looks like nothing. A branch that gives up only its tail produces a
write that succeeds: memory gains a fragment beginning in the middle of
a conversation, the whole branch is recorded as done, and no code path
reports anything. Where the bookkeeping moves last, a failure defaults
to doing the work again; here it defaults to skipping the work quietly
and keeping a decapitated record of it.

So the boundary has to be keyed on what identifies a message, and what
identifies a message is the node it is.

### Two ways to carry that identity

|  | A mark on the node | A set of ids beside memory |
|---|---|---|
| What a session owes | Walk back from the head collecting unmarked turns, stop at the first marked one | Walk the whole branch, subtract the set |
| At a fork | The walk reaches the shared prefix, finds it marked, stops | The prefix's ids are in the set, so they are skipped |
| What is stored | One field on a node that already exists | One file per thread holding every id ever written |
| How it grows | Nothing new | One entry per message, kept as long as the session exists |
| What a read costs | A few steps back from the head | The thread's whole list |
| Migration | Mark what the source archive covers | Seed the set from the same archive |
| A mark or an entry lost | The walk stops early and everything older is stranded | The turn is offered again |
| Coupling | Memory writes one field into the session store | Memory keeps its own books |

The mark is what this design takes. Keeping memory's books outside the
session store looks like the cheaper choice because it keeps a
pluggable subsystem from writing into the runtime's storage, and it is
not. Once the books have to stay right across a fork, "outside" means a
set, and a set brings a file layout, a migration, a rule that an id is
never removed, and a read whose cost grows with the session. The mark
brings none of those. What it costs is one field written by memory into
a node, and a node already carries where the turn came from, whether it
is the runtime talking to itself, which branch it roots, and whether
`rewind` retired it. One more field saying memory has taken this turn is
the same kind of thing.

A set is exact and a walk is not, and that is the real price. The walk
assumes the marks form a prefix of the branch, which is what the writer
produces: a batch is the leading turns that reach the threshold, so
marking always fills in from the oldest end. Two things bend the
assumption. A turn memory never records, a sub-agent's completion notice
or a merge prompt, carries no mark and would stop the walk at itself, so
the walk steps over what memory does not record rather than stopping on
it. And a mark lost after its transaction installed strands everything
older than it, where a set would offer those turns again. One field per
node written without a lock is how a mark gets lost, and the section on
where the mark goes says what that window actually is.

### What others do

Nine reference frameworks were read for this. Three of them store a
conversation that branches: claude-code, openclaw and pi-mono all keep
a parent pointer per entry in one append-only file. Of the rest, three
keep the conversation in a straight line, one destroys a branch by
rewriting the whole session when a turn is retried, and two hold no
conversation at all. A straight line makes a position an identity,
because there is only ever one list, so most of them never meet this
question.

Nobody writes "memory has taken this turn" onto a conversation turn.
The two shapes that are actually in use are to make the unit coarse
enough that branching stops mattering, and to derive the delta from the
tree instead of storing it. codex-cli takes the first: a fork starts a
new rollout file, and its extraction jobs live in a SQLite table keyed
by thread with a success watermark and a lease, so the unit is a whole
thread and a fork is simply a new key. pi-mono takes the second: its
session storage exposes no way to change an entry at all, annotating one
means appending a `label` entry that points at it, moving the cursor
means appending a `leaf` entry that points at it, and the set of turns a
branch summary covers is computed at navigation time by walking from the
abandoned leaf up to its common ancestor with the new one. claude-code
keeps a per-message cursor in process memory and treats a cursor whose
message it can no longer find as a reason to reprocess the session from
the start, with a comment saying that returning nothing would disable
extraction for the rest of the session. openclaw is the one that writes
a processed flag back, `promotedAt`, but it writes it onto a derived
candidate record rather than onto the transcript, and its own memory
hook then reads the transcript flat, taking the last fifteen lines with
no parent walk, which mixes abandoned branches into what it writes.

Two of those are worth taking. Reprocessing beats going silent when the
bookkeeping is lost, which is the rule below for a mark that goes
missing. And appending a marker that points at a node, rather than
changing the node, is a real alternative to the mark: here it would put
a node in the conversation graph for every batch written, which is a
worse trade than one field, because every reader of the graph would then
have to know to skip them.

### What a session owes

Memory asks for the branch ending at the session's head and walks it
back from the tip. Turns memory does not record are stepped over. Every
recorded turn without the mark is owed, and the walk stops at the first
recorded turn that carries it. What comes back is the owed turns in
branch order, oldest first; the batch is the leading part of them that
reaches the threshold, and the marks go on once the write transaction
installs, never before.

### When the mark goes on

Two conditions, not one. The write transaction has to install, and it
has to have changed a file. A writer that spends every one of its turns
on the same rejected edit finishes without raising anything and without
touching a file, and a run judged by its exit alone reads that as a
batch written. The transaction already reports the topic files a turn
changed; an empty list is a batch that reached no file, and marking it
would lose those turns for good. Measured elsewhere on this same writer:
twenty turns spent on one rejected edit, a successful return, and a
topic file one byte long.

That rule is not special to the mark. Any state that says a thing is
done has to be checkable against something the work produced, which is
also why the nightly pass reports the files it changed rather than the
files it looked at. The absence of an error is not a product.

The order of the three steps follows from which of them can be replayed.
Evidence is archived first, then the topic files are written, then the
marks go on. Archiving is append-only and addressed by content, so a
batch archived twice leaves the archive byte for byte as it was. Topic
files carry block IDs and footnotes that a half-write would corrupt, so
they go in through a transaction that installs whole or not at all. A
writer that dies between the two leaves evidence nobody cites, no marks,
and the same batch owed next time, which is a redo. Marking first would
turn the same crash into losing the turns silently, and nothing recovers
from that.

### At a fork

The turn that opens a branch carries the predecessor of the turn it
replaces, and nothing else about it is special. Walking back from the
branch's tip reaches the shared prefix, whose turns are marked already,
and stops there. The trunk and the branch need no ordering between them
and no knowledge of each other. Whichever is written first marks the
shared turns, and the other one stops at them.

### Every branch, at the session boundary

Per turn, memory asks for the branch ending at the session's head,
because that is the branch the turn happened on. At the boundary it asks
for all of them: `list_branches` gives every live tip and one
`get_branch` per tip gives the line behind it. Walking a shared prefix
several times costs nothing, since the walk stops at its first marked
turn.

The branch under the head is written however little it owes, because
nothing comes back for it afterwards. The others are written once what
they owe reaches the threshold. A branch of one turn is a retry, and a
retry is a reply somebody rejected; a branch long enough to reach a
batch is a line of conversation somebody went down and came back from,
and what was said there belongs in memory like anything else. The idle
allowance that lets a short head branch through does not apply to them,
because an abandoned branch's last message is old by definition and the
allowance would let every retry in.

Turns the user rolled back are gone before memory sees them. `rewind`
marks them and `get_branch` filters them out, so "abandoned on purpose"
is the session store's judgment and not one memory makes again.

### Where the mark goes

On the node, in `metadata`, under a key naming the provider that wrote
it. The interface is pluggable and two providers would each want their
own answer, so one boolean shared between them would be wrong the day a
second one exists.

Nothing has to be taught to carry it. `metadata` is a free-form dict on
both paths: the message adapter puts every field it does not recognise
into it, and the reader keeps the top-level fields of a node and passes
`metadata` through whole. Nor is writing to a stored node new. `rewind`
stamps `rewound` on the turns it retires and rewrites their files,
`revert` does the same with `reverted`, and the store already offers a
call that merges a metadata patch into a node and rewrites it.

The append invariants are not in the way. They are checked when a node
is appended, a mutation never reaches them, and what they read is
`predecessor`, `caller`, `role`, `input` and three metadata keys
(`display`, `spawn_branch_root` with `source`, and `covers_ids`). A key
of memory's own collides with none of them, and the read-side walk in
`get_branch` reads the same set.

A mark travels with the node. There is no session export format today,
and a session directory is self-describing JSON, so copying one is how a
session moves and the copy carries every mark with it. Arriving on a
machine whose memory workspace is empty, it would arrive with every turn
already claiming to have been written. Naming the provider in the key
does not separate the two, because the other machine runs the same
provider. What separates them is the workspace, so the mark's value is
the identity of the memory workspace that wrote it, an id generated once
and kept in the runtime directory, and a walk stops only on a mark that
names the workspace it is walking for. A restored backup keeps its id
and keeps its marks; a session copied in from elsewhere is written from
the start. Clearing marks on import would do the same job and needs an
import path that does not exist. An external file gets this for free, by
not travelling at all.

### What it costs the session store

Nothing there hashes a whole tree. The one full-byte tree hash in this
system is the memory workspace's revision, and it is rooted at
`<state>/memory` while sessions live under `<state>/sessions`, so a
marked node cannot read as a concurrent memory write. The mark is
bookkeeping rather than content, and the checksum that could confuse the
two does not reach it.

What a rewrite does trip is the session index cache. Staleness is the
history directory's mtime, and a rewrite bumps it exactly as an append
does, so another process holding that session rebuilds its index once:
measured at 14 to 50 milliseconds for a 289-node session of 4.2 MB, and
self-limiting, because the rebuild records the new fingerprint rather
than repeating per read.

The cost that grows is git. A session directory is committed with
`git add -A` once per turn, so every node whose bytes changed becomes a
new blob. Marking the batch a write ingested is a few dozen nodes per
write, and the repository grows by what the batch weighs. Marking every
node in the session on every write would add the session's whole weight
per turn, which is quadratic over its life. So the mark goes on the
batch and never on a sweep.

Two things must not happen. The mark must not move the session's
`updated_at`: the idle watcher decides a session is already handled by
comparing that exact value, so moving it hands the session straight back
to a forced write and the model call inside it. And the mark must not go
through the path that records the session as synced, because that
fingerprint would cover writes the marking process never read, and an
index that skipped them would then never rebuild. Writing the node file
directly is what `rewind` does, and the turn finalizer already rewrites
a node file in place to stamp its checkpoint, so the shape is in use
twice already.

Two more things are worth knowing before writing the code. A node file is
written without a lock, so marking a node while something else rewrites
the same node loses one of the two writes whole rather than merging
them; memory marks turns that are already finished, and the per-turn
write runs on the turn's own thread after the turn has been persisted,
which leaves the idle watcher meeting a session that has just woken up
as the window. And every metadata key is spread onto the top level of
the dicts `get_branch` hands back, so the mark's name has to be one no
message field uses.

### Migrating off the position cursor

An installation upgrading from the position cursor has
`cursors: {thread: {message_id, ordinal}}` in `runtime.json` and no
marks on any node, so a walk back from a head would collect the whole
session.

The source archive says which turns were handed to the writer.
`sources/openprogram/<session-id>.md` carries a `source-id` comment per
archived message, and on the write path a batch is archived before the
cursor advances, so the archive covers everything the old cursor pointed
past. Marking those nodes re-writes nothing that was written. It can
cover a little more, because a batch archived by a write that then
failed reads as written and is not offered again; that costs at most one
interrupted batch per thread, once, against re-writing every session's
whole history, which is what placing no marks would cost. A workspace
with no `sources/` tree, from before the source archive existed, is
written from the start.

`cursors` leaves `runtime.json` once the marks are placed. The counters
stay there: `creation_order`, the local batch and token counts, and the
time of the last global pass.

### What this does not settle

- **Nothing checks that the marks form a prefix.** The walk stops at the
  first marked turn it meets and trusts that everything behind it is
  marked too. That holds because a batch is always the oldest turns
  owed, and it is an assumption rather than a checked invariant.
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
- **Cross-session spawn.** A spawn branch inside the same session is an
  ordinary part of that session's graph and needs nothing extra. A
  cross-session spawn's branch root points into another session's graph
  and terminates the walk there, so the two halves are written under two
  sessions and neither walk crosses into the other. Nothing is written
  twice and nothing is skipped; what is missing is the link saying that
  the sub-agent's branch continues the caller's conversation.
- **Compaction summaries.** A summary node takes the predecessor of the
  first turn it covers, which makes it a sibling of the line the head is
  on rather than a member of it, so the ordinary walk sees the raw turns
  and writes those. A head that moves onto the summary's own line makes
  the summary an unwritten assistant turn whose text restates turns
  already in memory.
- **Rows the store gave no id.** `_records` falls back to a positional
  id for a row without one, and that string names different messages on
  different branches. It is the handle the source archive files the turn
  under, so it is wrong for the same reason a positional cursor is. The
  session store always supplies an id, so the fallback is unreachable,
  and it goes.
- **A mark means handed to the writer, not cited.** A batch the writer
  folds into one paragraph citing one of its five turns marks all five.
  That is intended, and it is why the archive is a fair seed above, but
  a mark is not evidence that a particular turn's content reached a file.

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

There is a second ceiling underneath that one, and a better criterion
does not move it. How much a batch becomes is set by what the writer can
hold, not by how much was said: measured on one prompt, 546,000
characters of evidence produced 41,000 characters of topics, and 165,000
characters produced 43,000. Three times the input, the same memory. What
a pass decides to do and how much a pass can hold are separate limits,
and only the first one answers to a rule.

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
first. The guidance the writer was handed on hitting the budget, to
leave the file alone and put the fact in a topic file, was correct and
was followed, which is why nothing ever said that one more stable fact
had been kept out.

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

Everything above runs today except "The write cursor" and the six
subsections after it, and "Every branch, at the session boundary".
Where those come from, and what was decided against, is
[`memory-adoption.html`](memory-adoption.html).

What runs in place of the write cursor is a position cursor:
`runtime.json` holds `cursors: {thread: {message_id, ordinal}}`,
`runtime/online.py` claims a record only when its ordinal is higher than
the stored one, and `scriptorium/writing.py` builds that ordinal from
the row's index in the branch it read. `RuntimeState.advance_cursor`
raises `cursor cannot move backwards` when a new ordinal is below the
stored one, so the shape refuses even the act of correcting itself.

The design lands in three places. `openprogram/store/session/` gains the
mark: a node's `metadata` takes a key naming the provider whose value
identifies the memory workspace, written once the write transaction has
installed and reported a non-empty list of changed files, through the
same file rewrite `rewind` uses. `runtime/online.py` works out what a
session owes by walking back from the head instead of comparing
ordinals, and `runtime/state.py` loses `cursors` and `advance_cursor`
and keeps the counters. `_records` in `scriptorium/writing.py` drops its
positional id fallback. The migration reads the `source-id` comments out
of `sources/` and marks those nodes, once.

Both write paths ask `get_branch` for the head's branch alone
(`memory/session_watcher.py` and `scriptorium/writing.py`), so no other
branch is ever offered. `list_branches` and the per-tip `get_branch`
are already there to build on.
