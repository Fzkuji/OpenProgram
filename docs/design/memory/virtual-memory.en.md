# Virtual Memory

## 1. Concept

Virtual memory is a compact index distilled from the concrete-memory layer. It does not replace the concrete layer; it gives that layer a **navigation map with coordinates**.

Core properties:
- **Provenance-linked**: every memory carries a pointer back to its source in the concrete layer
- **Bi-temporal**: every memory records two timestamps — `event_time` (when it happened) and `ingestion_time` (when it was written down)
- **Immutable append**: old records are never deleted; on conflict they are tagged `superseded_by`

Virtual memory currently has three types (plus the v1 transition-period Journal/Wiki):

| Type | Question it answers | Storage | Status |
|------|-----------|------|------|
| Timeline | What happened, and when | `memory/timeline/YYYY-MM.jsonl` | To be implemented in Phase 2 |
| Knowledge Graph | How things relate to one another | `memory/graph/{entities,edges}.jsonl` | To be implemented in Phase 2 |
| Core.md | The minimal snapshot the LLM reads on every call | `memory/core.md` | Exists in v1, to be rewritten in Phase 2 |
| Journal/Wiki (v1) | Same as above (coarse-grained) | `memory/short-term/`, `memory/wiki/` | Running, to be superseded |

## 2. Timeline

A stream of events organized by time. Answers "what happened, and when".

### 2.1 Creation

**Triggers**:
- Session goes idle (idle ≥30 min) → watcher triggers distillation
- Daily sleep (03:00) → batch consolidation

**Process**:
1. Read the newly added DAG nodes from the concrete layer (`iter_nodes_since()`)
2. LLM extracts events (Stage 2: extract)
3. Attach a `Provenance` pointer to each event
4. Append to the JSONL file for the corresponding month

### 2.2 Storage

```
~/.openprogram/memory/timeline/
├── 2026-05.jsonl
├── 2026-06.jsonl
└── ...
```

A single record:
```json
{
  "id": "ev_abc",
  "summary": "Fixed the Windows cp1252 encoding bug in OpenProgram, touching 38 files",
  "kind": "work",
  "provenance": {
    "project_id": "proj_openprogram",
    "session_id": "local_13d5",
    "commit": "73bfc05",
    "event_time": 1779900000,
    "ingestion_time": 1779986400
  },
  "entities": ["project.openprogram", "issue.cp1252"]
}
```

`kind` enumeration: `work` | `decision` | `learning` | `event`

### 2.3 Reading

- By time range: `memory_timeline(since, until)`
- By associated entity: `memory_timeline(entity="project.openprogram")`
- On recall, inject the most recent high-signal events into Core.md

### 2.4 Updating

Append-only. When the same fact is re-distilled, the old record is tagged `superseded_by` pointing at the new record's ID; it is not deleted.

### 2.5 Deletion

No deletion. Rationale: an event is a record of a historical fact, and deleting it would break the integrity of the timeline. Marking it superseded is enough.

## 3. Knowledge Graph

A graph structure of entities and relations. Answers "how things relate to one another".

### 3.1 Creation

**Triggers**: same as Timeline (session-end / daily sleep).

**Process**:
1. Stage 2 of the distillation pipeline extracts entities and relations from the DAG
2. Stage 3 performs alias resolution ("worker"/"backend"/"daemon" → the same node)
3. Stage 4 performs contradiction detection (a new edge conflicting with an old one → tag superseded)
4. Append to JSONL

### 3.2 Storage

```
~/.openprogram/memory/graph/
├── entities.jsonl           nodes
├── edges.jsonl              edges (with bi-temporal + provenance)
└── views/
    └── entity/<slug>.md     one readable page per entity
```

Entity:
```json
{
  "id": "project.openprogram",
  "type": "project",
  "name": "OpenProgram",
  "attrs": {"path": "/Users/fzkuji/OpenProgram", "lang": "python"},
  "scope": "global"
}
```

Edge:
```json
{
  "from": "issue.cp1252",
  "to": "commit.73bfc05",
  "relation": "fixed-by",
  "event_time": 1779900000,
  "ingestion_time": 1779986400,
  "provenance": {"project_id": "proj_openprogram", "session_id": "local_13d5"},
  "confidence": 0.95,
  "superseded_by": null
}
```

### 3.3 Reading

- Neighbor traversal: `memory_graph_neighbors(entity, hops=2)`
- Hybrid search: `memory_search(query)` — FTS + optional vectors
- Scope filtering: filter by the current context at query time (`global` + `project:<current>`)

### 3.4 Updating

- **Alias resolution**: different names for the same entity are merged into a single node
- **Contradiction handling**: when a new edge conflicts with an old one, the old edge is tagged `superseded_by` pointing at the new edge; the old edge is not deleted
- **View rebuild**: Stage 5 re-projects `views/entity/*.md`

### 3.5 Deletion

No deletion. The old edge is tagged `superseded_by`, preserving the full history.

### 3.6 Scope tags

Every entity/edge carries a scope, filtered by context at query time:

```
scope: "global"                  across all projects (e.g. the user's language preference)
scope: "project:openprogram"     this project only
scope: "agent:research"          this agent only
```

## 4. Core.md (injected snapshot)

The minimal memory snapshot injected into the system prompt on every LLM call. ≤2KB.

### 4.1 Creation/update

**Trigger**: regenerated on sleep::deep (daily 03:00).

**Sources**:
- The most recent high-signal events from the Timeline (top-N by recency × importance)
- High-frequency / high-confidence entities and relations from the Graph

**Every line carries a provenance pointer** (`↪ session:<id>`), so once the LLM sees it, it can use the navigation tools to dig deeper.

### 4.2 Reading

On every LLM call, the contents of Core.md are injected into the system prompt:

```
═══════════════════════════════════════════════
OpenProgram Memory — project: OpenProgram, last consolidated 2026-06-18
═══════════════════════════════════════════════
[Timeline · recent]
· 2026-06-15 Refactored the Functions page into a three-tab layout   ↪ session:local_fc03
· 2026-06-17 Fixed the CLI attended-mode issue                       ↪ session:local_d125

[Graph · current project]
· OpenProgram at /Users/fzkuji/OpenProgram (python, next.js)
· worker ──listens-on──► :18109

For details: memory_open_session(<id>) / memory_git_log(<project>)
═══════════════════════════════════════════════
```

### 4.3 Deletion

Overwrite-style update. Each sleep::deep regenerates the entire file, and the old contents are fully replaced.

## 5. Journal/Wiki (v1 transition)

### 5.1 Current status

The v1 three-layer architecture is still running:

```
short-term/YYYY-MM-DD.md  → (sleep::light) →  wiki/<kind>/<slug>.md  → (sleep::deep) →  core.md
```

- **short-term**: at session-end, append 0–10 facts to the current day's file
- **wiki**: sleep::deep promotes short-term facts into knowledge pages
- **core.md**: sleep::deep projects a minimal snapshot from the wiki

### 5.2 Problems

The v1 pipeline reads from the conversation text rendered by `get_branch()`, not from DAG nodes. It loses:
- Tool-call chains (what the agent ran, with what arguments and results)
- `reads` edges (what influenced a decision)
- project-git commit history

### 5.3 Future

Once Phase 2 is complete, the v1 pipeline is replaced by the new Timeline + Graph:
- `short-term/` → replaced by Timeline
- `wiki/` → replaced by Graph
- `core.md` → re-projected from Timeline + Graph

During the transition both run in parallel; the old pipeline is removed once the new one is validated.

## 6. Distillation pipeline (concrete → virtual)

### 6.1 Trigger timing

| Trigger | Frequency | Purpose |
|------|------|------|
| Session-end | Session idle ≥30 min | Incrementally distill new turns |
| Sleep (03:00) | Once a day | Batch consolidation, disambiguation, contradiction detection, core rebuild |
| Pre-compress | When context approaches its limit | Flush the key information from the conversation to the concrete layer |

### 6.2 Five Stages

```
Stage 1: Collect    Pull commits added since the last distillation from session-git + project-git
                    Read the full set of DAG nodes (user/llm/code + reads edges)

Stage 2: Extract    A single LLM pass: extract timeline events + graph entities/relations, each tagged with provenance

Stage 3: Link       Run alias resolution between new entities and the existing graph

Stage 4: Reconcile  Contradiction detection; old edges tagged superseded, not deleted

Stage 5: Project    Re-project core.md / entity views / timeline shards
```

Stage 2 is the most expensive (it needs an LLM); you can start with a rules-based version (pattern match) and gradually replace it with a prompt-based version.

### 6.3 Key point: read the DAG directly

v1 reads rendered text. v2 reads the `Call` DAG in session-git directly, including `code` nodes (tool calls) and `reads` edges (context references). These are the key data sources for projecting the graph.

The read layer is already implemented: `store/session/provenance.py` provides `iter_nodes_since()` / `node_provenance()` / `session_commits()` / `project_commits()`.

## 7. Recall mechanism

### 7.1 Injection (virtual only)

Injected on every LLM call:
- Core.md (≤2KB, always injected)
- Optional: timeline/graph results recalled for the current query

No raw chat is injected.

### 7.2 Navigation (LLM fetches on its own)

When the LLM needs detail from the concrete layer, it calls a navigation tool:

| Tool | Purpose | Where it lands in the concrete layer |
|------|------|-------------|
| `memory_open_session(session_id, turn)` | Read the raw messages of a session | Session-Git history/ |
| `memory_git_log(project_id, since)` | View a project's commit history | Project-Git |
| `memory_git_show(project_id, commit)` | See what a given commit changed | git show |
| `memory_timeline(entity\|since\|until)` | A timeline slice | Virtual timeline |
| `memory_graph_neighbors(entity, hops)` | A node's neighbors in the graph | Virtual graph |
| `memory_search(query)` | Hybrid search across the virtual layer | Virtual (FTS + vectors) |
