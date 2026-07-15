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

## Implementation Status

| Phase | Content | Status |
|-------|------|------|
| 0 | Baseline fixes (LLM bridge / watcher / ingest) | ✅ |
| 1 | Entity layer: Project schema + session.project_id + project-git | ✅ |
| 2 | Distillation pipeline rewrite: read the session-git DAG → timeline + graph | ❌ Not started |
| 3 | Recall rewrite: inject only the abstract layer + navigation tools | ❌ Not started |
| 4 | Materialized views + hybrid search (vector) | ❌ Not started |
| 5 | UI: Projects panel / timeline / `/memory` | ⚠️ Partial |

## Sub-documents

> Versioning: `memory.md` describes the **currently shipped** linear summary
> chain (`journal → wiki → core`, see `openprogram/memory/`); `memory-v2.md`
> is the **target design** (entity/virtual two layers + provenance recall) that
> supersedes v1 and is still being implemented. This README describes the v2
> architecture.

| Document | Content | Status |
|------|------|------|
| [`memory.md`](memory.md) | v1: linear summary chain (journal/wiki/core) | ✅ current |
| [`memory-v2.md`](memory-v2.md) | v2: entity/virtual two layers + provenance recall (supersedes v1) | 🚧 design + WIP |
| [`git-as-entity-memory.md`](git-as-entity-memory.md) | Origin of the entity layer (Session-Git + Project-Git) | reference |
| [`entity-memory.md`](entity-memory.md) | Entity memory: Session-Git + Project-Git, organized by lifecycle | sub-design |
| [`virtual-memory.md`](virtual-memory.md) | Abstract memory: Timeline + Graph + Core, organized by type × lifecycle | sub-design |
