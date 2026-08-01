# Memory v2 — Entity/Virtual Two-Tier + Provenance-Navigated Recall

> This document is the authoritative design of the memory subsystem: the two
> tiers, the provenance pointer that links them, the distillation pipeline that
> derives one from the other, and the recall path that puts them in front of the
> model. For the entity tier's git substrate in detail, see
> [`git-as-entity-memory.md`](git-as-entity-memory.md); for the linear
> summarization chain this design replaces, see [`memory.md`](memory.md).
>
> Path conventions: all state lives under `~/.openprogram/` (= `get_state_dir()`);
> named profiles use `~/.openprogram-<profile>/`.

## 1. Overview

Memory is split into an **entity tier** (git-stored, immutable, the complete
real history) and a **virtual tier** (a compact, pointer-bearing index distilled
from the entity tier). When the LLM is called, **only the virtual tier is
injected**; when raw detail is needed, the LLM follows the **provenance
pointers** in the virtual tier and uses tools to navigate back to the entity
tier itself.

```
        ┌──────────────────── Entity tier (raw, git, complete) ────────────────────┐
        │   Session-Git                          Project-Git                          │
        │   one repo per session                 binds user work dir (real code/docs) │
        │   one commit per turn                  agent edits file → auto commit       │
        │   · bound to project → <project>/.openprogram/sessions/<id>/                │
        │   · casual chat      → <state>/sessions/<id>/  (default proj = label only)  │
        └─────────────────────────────────┬──────────────────────────────────────────┘
                                           │  continuous distillation, with provenance
                          ┌────────────────┴────────────────┐
                          ▼                                  ▼
              ┌────────────────────┐          ┌────────────────────┐
              │  Timeline (Journal) │          │  Knowledge Graph    │  ← Virtual tier (derived)
              │  "when did what     │          │  (Wiki)             │     every entry carries
              │   happen"           │          │  "how entities      │     a pointer back to
              │  bi-temporal        │          │   relate"           │     the entity tier
              │                     │          │  bi-temporal edges  │
              └──────────┬──────────┘          └──────────┬──────────┘
                         └──────────────┬─────────────────┘
                                        ▼
                              ┌──────────────────────────────────────────────┐
                              │   Recall                                       │
                              │   inject virtual tier only into LLM context;   │
                              │   LLM sees pointers → navigates back to entity │
                              └──────────────────────────────────────────────┘
```

## 2. Design Rationale

### Why the abstraction tier reads the entity tier directly

A linear summarization chain (`raw chat → extract 0-10 facts → journal → wiki →
core`) drops information at every layer, and the abstraction layer is built from
the lossy summary of the layer above it rather than from the source. The entity
tier and the abstraction layer then become two disconnected things. This design
makes the entity tier the single source the abstraction tier reads, and keeps a
pointer from every derived record back to that source.

### Comparison with mainstream frameworks

| Framework | Entity tier | Abstraction tier | Recall method | Time dimension | Knowledge graph |
|---|---|---|---|---|---|
| Claude Code | CLAUDE.md + sessions | auto-memory MEMORY.md (index + topic) | inject index, read topics on demand | no | no |
| OpenClaw | MEMORY.md + journal | same as above + wiki plugin | inject + semantic search | no | weak |
| mem0 | — | vector DB | RAG chunked injection | write time only | no |
| Letta/MemGPT | conversation history | tiered (core/recall/archival) | LLM tool shuttling | partial | no |
| Zep/Graphiti | — | temporal knowledge graph | graph queries | bi-temporal | yes |
| **This design** | **git (session+project)** | **timeline + knowledge graph** | **inject virtual, LLM navigates back to entity** | **bi-temporal** | **yes** |

### What is distinctive about this design

1. **Git as the substrate for episodic memory.** Entity memory is not a
   home-grown store; it uses git directly: commits are immutable, so the record
   cannot be tampered with; log is the timeline; checkout is the time machine;
   branch holds explored branches; and the agent can read it with standard tools
   (`git log` / `grep` / `diff`). Auditable, reproducible, traceable.

2. **A provenance-pointer index, not a replacement.** The virtual tier does not
   replace the entity tier; it builds it a **coordinate-bearing navigation
   map**. Every virtual memory carries a pointer `(project, session, commit,
   timestamp)` back to its origin in the entity tier. This addresses the
   fundamental problem of lossy summaries dropping context: **at any time you
   can follow the pointer back down to the source record**.

3. **LLM-self-navigated recall (map → territory), not RAG chunk injection.**
   Traditional RAG slices out relevant chunks and stuffs them into the context,
   polluting the context and losing structure. This design injects only a
   compact virtual map; the LLM reads "in 2026-05, fixed a Windows bug in
   project X, full history in session local_13d5", and **when it needs the
   detail it walks over and fetches it with tools itself**. Small context, full
   fidelity, agent-driven retrieval.

4. **Dual projection of timeline + knowledge graph, both bi-temporal.** The same
   git substrate is projected into two orthogonal views: the timeline answers
   "when", the knowledge graph answers "what relationship". Both record two
   times — `event_time` (when the thing happened) and `ingestion_time` (when we
   wrote it down) — supporting time-travel queries and contradiction detection.

## 3. Entity Memory

### 3.1 Every session belongs to some project

**There are no ownerless sessions.** Every session belongs to a project; the only
difference is whether that project is the user's real working directory.

```
when a session is created:
  is a work-dir path specified? (the topbar work_dir selector)
    yes → bind to that path's Project-Git (user's real code/doc repo),
          session repo lands at <project>/.openprogram/sessions/<id>/
    no  → default project (logical label project_id="default"),
          session repo lands at home root <state>/sessions/<id>/
```

The entity tier therefore always has clear ownership, and the virtual tier can
always be aggregated by project.

### 3.2 Disk Layout

```
~/.openprogram/                          ← state root (get_state_dir())
├── sessions/<session_id>/              ← Session-Git, casual chat (no bound project)
│   ├── .git/                            one commit per turn
│   ├── meta.json                        title / agent_id / project_id / head
│   ├── history/NNNN-<role>-<id>.json    DAG nodes (user/llm/code)
│   ├── context/                         materialized view for the LLM (messages.json + commits/)
│   └── workdir/                         this session's temp working directory
│
├── sessions/locations.json            ← session location index: in-project sessions → real path
│
├── projects/
│   └── projects.json                   project registry (id → {name, path, sessions, status})
│                                        default project is just a logical label, no standalone repo
│
└── memory/                             ← virtual tier (see §4)

<user work dir>/                         ← Project-Git (the real repo used when bound)
├── .git/                               reuse existing; git init if none. agent edits file → auto commit
└── .openprogram/sessions/<id>/         ← sessions bound to this project, repo lands in-project
```

### 3.3 Session-Git

Session storage lives at `<state>/sessions/<id>/` (`store/git_session.py`). Each
node is a `Call` (role = user / llm / code), and edges are `called_by` (the call
chain) + `reads` (context references). `meta.json` carries a `project_id` field
recording the session's project binding.

For a session bound to a real project, its repo is not at the home root but at
`<project>/.openprogram/sessions/<id>/`, indexed by the `sessions/locations.json`
index (`SessionStore._record_location` / `_session_dir`). This way a project's
code history (project-git) and conversation history (session-git) both stay
inside the project directory and travel with the project.

### 3.4 Project-Git

A Project = a long-running unit of work, associated with:
- a **filesystem directory** (the user's real code repo / document repo)
- **multiple sessions** (the multiple conversations on this project)
- a name / description / status

```python
@dataclass
class Project:
    id: str                       # proj_<8hex of path>, or "default"
    name: str
    path: str                     # absolute path; default project = "" (no repo)
    is_default: bool              # True means the default project (logical label)
    session_ids: list[str]        # reverse index
    status: str                   # "active" | "paused" | "done"
    created_at: float
```

The registry is at `<state>/projects/projects.json`, keyed by the path-derived
`proj_<8hex>` (the same directory always maps to the same project).
`resolve_project(path)` reuses the directory's existing `.git`, or `git init`s if
there isn't one (`ProjectGit.ensure_init`).

**Auto-commit**: at turn end, if the session is bound to a real project and the
agent changed files:

```
if not is_dirty_before_agent_touched():     # work tree was originally clean
    git add -A && git -c user.name=<agent> commit -m "[agent <session>] turn <N>: <user msg>"
else:
    # user has uncommitted changes → don't pollute, skip + UI warning
    skip + warn
```

The commit uses the agent's identity (`-c user.name/email` override), so even
inside the user's own repo it stays distinguishable from the user's commits.

### 3.5 Semantics of the Default-Project

The default project is a **pure logical label**; it does not create a standalone
git repo. The rationale: a casual chat is not bound to any directory, and the
files it produces (if any) land in that session's own `workdir/`, so a "default
project repo" would always be empty and purely redundant. Such sessions only
carry `project_id="default"` for grouping / scope filtering, and their **entity
memory is the session repo itself** (landing at the home root
`<state>/sessions/<id>/`).

Only when a session is bound to a real working directory does an actual git repo
appear (see §3.4). That is: **real path → real repo; no path → label only**. This
avoids the meaningless bloat of a pile of empty default repos in the entity tier.

## 4. Virtual Memory

Two projections, both distilled from the entity tier, both carrying provenance
pointers, both bi-temporal.

### 4.1 Provenance Pointer (the Core Data Structure)

Every virtual memory carries a pointer back to its origin in the entity tier:

```python
@dataclass
class Provenance:
    project_id: str               # which project
    session_id: str               # which session
    commit: str | None            # which session-git commit (optional)
    node_ids: list[str]           # which DAG nodes (optional, message-precise)
    event_time: float             # when the thing happened (wall clock)
    ingestion_time: float         # when we distilled and recorded it
```

`event_time` vs `ingestion_time` = the two axes of bi-temporal. They can answer:
- "after that refactor last Wednesday, what did the code look like" (by event_time)
- "when did we even find out library X was unstable" (by ingestion_time)

### 4.2 Timeline (Temporal / Journal)

An event stream organized by time. Answers "when did what happen".

```
~/.openprogram/memory/timeline/
├── 2026-05.jsonl               # sharded by month, append-only
└── ...

# one record
{
  "id": "ev_abc",
  "summary": "fixed a Windows cp1252 encoding bug in OpenProgram, touched 38 files",
  "kind": "work",               # work | decision | learning | event
  "provenance": {
    "project_id": "proj_openprogram",
    "session_id": "local_13d5",
    "commit": "73bfc05",
    "event_time": 1779900000,
    "ingestion_time": 1779986400
  },
  "entities": ["project.openprogram", "issue.cp1252"]   # linked to graph nodes
}
```

### 4.3 Knowledge Graph (Graph / Wiki)

Entities + relationships. Answers "what is related to what, and how". Where a
plain wiki holds isolated entity pages, the graph adds edges and time.

```
~/.openprogram/memory/graph/
├── entities.jsonl              # nodes
├── edges.jsonl                 # edges (with bi-temporal + provenance)
└── views/                      # materialized readable views
    ├── entity/<slug>.md        # one page per entity (compatible with existing wiki reading habits)
    └── ...

# entity
{"id": "project.openprogram", "type": "project", "name": "OpenProgram",
 "attrs": {"path": "C:\\Users\\fzkuji\\OpenProgram", "lang": "python"}}

# edge (with bi-temporal + provenance)
{"from": "issue.cp1252", "to": "commit.73bfc05", "relation": "fixed-by",
 "event_time": 1779900000, "ingestion_time": 1779986400,
 "provenance": {"project_id": "proj_openprogram", "session_id": "local_13d5"},
 "confidence": 0.95, "superseded_by": null}
```

**Contradiction handling**: when a new edge conflicts with an old one, the old
edge is not deleted; it is marked `superseded_by` pointing to the new edge. This
preserves history and supports time-aware queries like "we once thought X, then
found out Y".

### 4.4 Scope Labels (Cross-Project Isolation)

Every entity / edge carries a scope, and queries filter by the current context:

```
scope: "global"                  # across all projects (e.g. user language preference)
scope: "project:openprogram"     # this project only
scope: "agent:research"          # this agent only
```

More flexible than both Claude Code (pure directory hierarchy) and OpenClaw
(pure per-agent) — a graph naturally supports multi-dimensional label filtering,
which a filesystem hierarchy cannot. When chatting in the OpenProgram project,
only the `global` + `project:openprogram` subgraph is projected.

### 4.5 Core (the Minimal Snapshot Always Injected)

Not a separate tier, but the **minimal projection** of the virtual tier: take the
most recent high-signal events from the timeline + the high-frequency /
high-confidence entities from the graph, and assemble them into a ≤2KB snippet.
Injected into every system prompt. **Every line in core also carries a pointer**,
so when the LLM sees core it knows where to drill in.

## 5. Recall Mechanism

### 5.1 Injection: Only the Virtual Tier

On every LLM call, what gets injected into the system prompt is:

```
═══════════════════════════════════════════════
OpenProgram memory — project: OpenProgram, last organized 2026-05-29
═══════════════════════════════════════════════
[Timeline · recent]
· 2026-05-28 fixed a batch of Windows-compat bugs (38 files)   ↪ session:local_13d5
· 2026-05-29 refactored the CLI into a verb scheme, added rescue/logs  ↪ session:local_7cd1

[Graph · current-project related]
· OpenProgram at C:\Users\fzkuji\OpenProgram (python)
· cp1252-bug ──fixed-by──► commit 73bfc05               ↪ session:local_13d5
· worker ──listens-on──► :18109

need detail: memory_open_session(<id>) / memory_git_log(<project>) / memory_timeline(<entity>)
═══════════════════════════════════════════════
```

**No raw chat is stuffed in.** It is all compact, pointer-bearing summaries.

### 5.2 Navigation: the LLM Follows Pointers to Fetch for Itself

When the LLM needs raw detail, it calls navigation tools to walk back to the
entity tier:

| Tool | What it does | Where in the entity tier it lands |
|---|---|---|
| `memory_open_session(session_id, [turn])` | read the raw messages of a session | `<sessions>/<id>/history/` |
| `memory_git_log(project_id, [since])` | view a project's commit history | Project-Git |
| `memory_git_show(project_id, commit)` | see what a given commit changed | git show |
| `memory_timeline(entity\|since\|until)` | a slice of the timeline | virtual timeline |
| `memory_graph_neighbors(entity, hops)` | a node's neighbors in the graph | virtual graph |
| `memory_search(query)` | hybrid search across the virtual tier | virtual (FTS + vectors) |

Example: the LLM reads "cp1252-bug fixed-by 73bfc05 ↪ session:local_13d5" in core
and wants to know exactly how it was fixed → it calls
`memory_git_show("proj_openprogram", "73bfc05")` to get the diff, or
`memory_open_session("local_13d5")` to read the conversation at the time. **The
virtual tier gives the coordinates, the entity tier gives the truth, and the LLM
walks that path itself.**

## 6. Distillation Pipeline (Entity → Virtual)

### 6.1 Triggers

- **Incremental (session-end)**: session goes idle → distill the new commits of this session
- **Batch (sleep, daily at 03:00)**: re-organize, disambiguate, detect contradictions, rebuild core
- **Pre-compaction flush**: insert a round before context compaction so the agent flushes the key information still in the conversation down to the entity tier first

### 6.2 Five Stages

```
Stage 1: collect   — pull new commits from session-git + project-git since last distillation
                     (read full DAG nodes: user/llm/code + reads edges, not just chat text)
Stage 2: extract   — one LLM pass, extract timeline events + graph entities/relations, each tagged with provenance
Stage 3: link      — alias-resolve new entities against the existing graph ("worker"/"backend"/"daemon" → same node)
Stage 4: reconcile — contradiction detection, mark old edges superseded, don't delete
Stage 5: project   — re-project core.md / entity views / timeline shards
```

Stage 2 is the most expensive and the most prompt-tuning-hungry (Graphiti
iterated on this part for several months). It can start with a rule-based version
(pattern match "I prefer X" → edge), with the prompt-based version gradually
replacing it.

### 6.3 The pipeline reads the DAG, not rendered text

The pipeline **reads the `Call` DAG in session-git directly** — including `code`
nodes (what tools the agent ran, with what arguments, with what results) and
`reads` edges (what influenced a decision). These are exactly the material graph
projection needs (`agent ──ran──► pytest ──produced──► 3 failures`), and they are
lost if the pipeline reads conversation text that has already been flattened.

## 7. Schema Overview

```
~/.openprogram/
├── sessions/<id>/               entity: session (meta carries project_id)
│   └── (project-bound sessions land at <project>/.openprogram/sessions/<id>/, indexed by sessions/locations.json)
├── projects/
│   └── projects.json            project registry (default project = logical label, no default/.git)
├── memory/                       virtual tier
│   ├── timeline/YYYY-MM.jsonl   virtual: timeline
│   ├── graph/
│   │   ├── entities.jsonl       virtual: graph nodes
│   │   ├── edges.jsonl          virtual: graph edges (bi-temporal + provenance)
│   │   └── views/entity/*.md    virtual: readable views
│   ├── core.md                  virtual: minimal injected snapshot
│   ├── index/
│   │   ├── graph.sqlite         graph queries + FTS + time index
│   │   └── embeddings.sqlite    vectors (hybrid search, optional)
│   └── .state/                  distillation progress / locks
<user work dir>/.git/             entity: real project (agent edits files, auto commit)
```

## 8. Relationship to Existing Code

**Reused**:
- Session-Git (`<state>/sessions/`, `store/git_session.py`) — the first piece of the entity tier
- Project-Git (`store/project_store.py`) — the second piece of the entity tier
- the `Call` DAG (`context/nodes.py`) — the node model of the entity tier
- the `MemoryProvider` abstract interface (`memory/provider.py`) — the recall hook shape
- the sleep scheduler skeleton (`memory/scheduler.py`, `sleep/runner.py`) — reads git
- the FTS index (`memory/index.py`) — expanded into graph.sqlite

**Superseded**:
- the fact extraction in `memory/builtin/summarizer.py` and the rendered-text reading in `memory/wiki/ingest.py` → Stage 2's entity/relationship extraction that reads the DAG
- the isolated topic pages of `memory/wiki/` → `graph/` (nodes + edges + time)
- the linear `journal → wiki → core` chain → fan-out from git
- the `memory/journal/` journal layer → timeline (the raw record already lives in session-git)

**Added**:
- `memory/graph/` (entities/edges/views)
- `memory/timeline/`
- navigation tools (expansion of `functions/tools/memory/`)
- bi-temporal + provenance fields

## 9. Research Angle

1. **Git-native episodic memory for LLM agents** — using a version control system
   as the immutable substrate for an agent's long-term memory, supporting
   backtracking / branching / standard-tool retrieval.
2. **Provenance-linked virtual memory** — the summary layer does not replace the
   source but indexes it with coordinates; resolves the fundamental tension of
   lossy summarization (compression vs fidelity).
3. **LLM-navigated recall** — the agent reads a compact map and navigates back to
   the source on demand, in contrast to RAG's blind chunked injection; smaller
   context, higher fidelity, agent-driven retrieval.
4. **Dual bi-temporal projection** — the same substrate projected into a timeline
   + knowledge graph, both bi-temporal, supporting time travel and contradiction
   tracking.

Evaluation directions: the trade-off of context footprint vs recall accuracy;
long-range consistency across multiple sessions; contradiction-detection recall;
comparison against RAG / Zep / mem0 baselines.

## Appendix: Implementation Status

The entity tier is in place: the Project schema, session binding, project-git,
and sessions landing inside the project are all implemented
(`store/project_store.py`, `store/session_store.py`). The read layer for
distillation is available in `store/session/provenance.py`.

The virtual tier described in §4 is not yet built. What runs today is the linear
journal/wiki/core chain documented in [`memory.md`](memory.md), and it still
reads the conversation text rendered by `get_branch()` rather than the
session-git `Call` DAG; the project-git commit history is not read at all. Wiring
that hop is the first step of the distillation pipeline described in §6.

Recall (§5) still injects the v1 core; the navigation tools in §5.2 are not yet
registered. The UI carries a topbar project selector; the Projects panel,
backtrack timeline, and `/memory` command are not built.
