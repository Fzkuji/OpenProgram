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
| [`overview.md`](overview.md) | The two-tier architecture (entity/virtual + provenance recall) and the linear summary chain running today in `openprogram/memory/` |
| [`memory-architecture.html`](memory-architecture.html) | Visualization: the two write entry points, the five write steps, the staged transaction, the id-set cursor, which of the nine provider hooks are wired, and the failure contract |
| [`git-as-entity-memory.md`](git-as-entity-memory.md) | The entity layer's git substrate (Session-Git + Project-Git) |
| [`entity-memory.md`](entity-memory.md) | Entity memory: Session-Git + Project-Git, organized by lifecycle |
| [`virtual-memory.md`](virtual-memory.md) | Abstract memory: Timeline + Graph + Core, organized by type × lifecycle |

## Implementation Status

The entity layer is in place: the Project schema, `session.project_id`, and
project-git are all implemented. The abstract layer is still the linear summary
chain described in [`overview.md`](overview.md) — the distillation pipeline does not
yet read the session-git DAG, recall does not yet inject the abstract layer
alone, and the navigation tools are not yet registered. In the UI, the topbar
project selector exists; the Projects panel, timeline, and `/memory` command do
not.
