# Memory — Memory System Design

## Definition

Memory = **entity memory** (the complete, immutable, real history) + **abstract memory** (a compact index distilled from the entities).

Entity memory is the ground truth: backed by git, one commit per turn, tamper-proof. Abstract memory is a navigation map derived from the entity layer; every entry carries a provenance pointer back to its source in the entity layer. The LLM is injected with abstract memory only; when it needs details, the LLM follows the pointers to navigate back to the entity layer and fetch them itself.

## Architecture

```
entity memory (raw, git, immutable, complete)
  ├─ Session-Git    one repo per session, one commit per turn
  └─ Project-Git    bound to the user's working directory; agent edits a file → auto commit
         │
         │  distillation: 5-stage pipeline, with provenance
         ▼
abstract memory (derived, compact, provenance-linked)
  ├─ Timeline       timeline event stream (what happened when)
  ├─ Graph          knowledge graph (what relationships hold between entities)
  └─ Core.md        ≤2KB injected snapshot (the LLM sees it every time)
         │
         │  recall: inject only the abstract layer; the LLM uses tools to navigate back to entities
         ▼
LLM Context
```

## Design Principles

1. **Git-native** — entity memory uses git directly; no reinventing the wheel. Commits are immutable, the log is the timeline, and checkout is the time machine.
2. **Provenance-linked** — the abstract layer does not replace the entity layer; it indexes it. Every abstract memory entry carries the coordinates `(project, session, commit, timestamp)` pointing back to its source.
3. **Bi-temporal** — every memory records two times: `event_time` (when the thing happened) and `ingestion_time` (when it was written down). This supports time-travel queries and contradiction detection.
4. **LLM-navigated recall** — never dump raw chat into the context. Inject only the compact map; the LLM walks back to the entity layer with tools on demand to fetch details.

## Sub-documents

| Document | Content |
|------|------|
| [`overview.md`](overview.md) | The current Source/Topic/derived-view architecture, automatic writer, authority boundary, transactions, failure behavior, and implementation record |
| [`written-marker.md`](written-marker.md) | How memory knows which turns it has already written, in four layers: the replaced position cursor, the eight reference frameworks, the implemented mark-on-the-node design, and the event-driven design that remains deferred |
| [`written-marker.html`](written-marker.html) | Visualization of those four layers: where the ordinal comes from and what it drops at a fork, the reference frameworks side by side, the walk and the three write steps, and the derived alternative |
| [`memory-architecture.html`](memory-architecture.html) | Visualization: the two write entry points, the five write steps, the staged transaction, the write cursor, who maintains the always-on block, which of the nine provider hooks are wired, and the failure contract |
| [`memory-comparison.html`](memory-comparison.html) | Visualization: how the eight reference frameworks write and track long-term memory, across eight dimensions, including what each does to its cursor at a fork and what maintains its always-on block, and where our choices and our two planned changes land against them |
| [`memory-adoption.html`](memory-adoption.html) | Visualization in three layers: the four moves worth borrowing from that comparison, what each would cost here, and the verdict on each — three adopted, one rejected on measured per-turn latency |
| [`speaker-identity.html`](speaker-identity.html) | Visualization in three layers: how several people share one session and where speaker identity used to break, what all eight reference frameworks do about it, the two-file change that follows from them and now runs, and the two things that shape leaves open — a sender can type a second label into the body, and there is no key to filter memory by person — with the field that closes both |
| [`authority-landscape.html`](authority-landscape.html) | Current owner/paired authority method, local reference-framework evidence, adopted/modified/rejected decisions, execution-order visualization, and implementation record |
| [`authority-handoff.md`](authority-handoff.md) | Settled authority and writer decisions, exact deferred boundaries, review disposition, and implementation handoff |
| [`git-as-entity-memory.md`](git-as-entity-memory.md) | The entity layer's git substrate (Session-Git + Project-Git) |
| [`entity-memory.md`](entity-memory.md) | Entity memory: Session-Git + Project-Git, organized by lifecycle |
| [`virtual-memory.md`](virtual-memory.md) | Abstract memory: Timeline + Graph + Core, organized by type × lifecycle |

## Implementation Status

The committed implementation stores append-only Source evidence, model-written
Topic blocks, and Runtime-derived Core, Timeline, Recent, and Relations views.
Every Source carries Runtime-owned authority provenance: the SessionDB writer
and the general `memory_update` transaction both build it from persisted turn
authority rather than from a caller's payload, and creating a Source without it
fails closed.
The automatic writer reads SessionDB branches, records successful handling on
the source nodes, uses the default chat agent provider/model unless
`memory.writer.model` overrides it, and installs changes through a staged
transaction.

The memory tool surface, CLI and Web UI are registered. The committed baseline
includes writer status, one-shot trusted-Source backfill,
`memory.backend=none` guards, and a composed integration test from SessionDB to
watcher state. The live writer acceptance processed two eligible messages, but
the live workspace has not executed historical backfill for its 152 uncited
Source records.

A Topic reference new to a block must resolve to a `trusted` Source frame and
must belong to the current transaction's own evidence, so no tool path can cite
a `pending` Source or attach an unrelated one to new prose. Write failures
classify into one closed `MemoryWriteFailureCode` taxonomy shared by the status
file, CLI, tool, API and web UI; the idle watcher persists each terminal
outcome durably under a cross-process lock; and unpaired group archival has
explicit rate and storage ceilings. Read-time filtering by requester tier is
designed in [`authority-handoff.md`](authority-handoff.md) but not implemented.
Hold-and-approve requests, branch-semantic provenance, cross-session spawn
relations and event-driven writer notification remain separate deferred
designs.
