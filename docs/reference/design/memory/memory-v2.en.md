# Memory v2 — Entity/Virtual Two-Tier + Provenance-Navigated Recall

> Status: design draft + under implementation. Supersedes the current linear summarization chain (`journal → wiki → core`).
> Prerequisite reading: [`git-as-entity-memory.md`](git-as-entity-memory.md) (initial design of the entity tier),
> [`memory.md`](memory.md) (v1 implementation).
> Path conventions: all state lives under `~/.openprogram/` (= `get_state_dir()`); named profiles use
> `~/.openprogram-<profile>/`. The `~/.agentic/` and `sessions-git/` from early docs are both deprecated
> (see the one-time migration in `openprogram/paths.py`).

## 0. In One Sentence

Memory is split into an **entity tier** (git-stored, immutable, the complete real history) and a **virtual tier** (a compact, pointer-bearing index distilled from the entity tier). When the LLM is called, **only the virtual tier is injected**; when raw detail is needed, the LLM follows the **provenance pointers** in the virtual tier and uses tools to navigate back to the entity tier itself.

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

## 0.5 Implementation Status (as of 2026-05)

This document is the **target design**. Where the current code stands:

| Phase | Content | Status |
|---|---|---|
| **0** | baseline fixes (LLM bridge / watcher deferral / ingest reading the right fields) | ✅ done (see §9) |
| **1** | entity-tier Project: schema + binding + project-git + sessions landing inside the project | ✅ done (`store/project_store.py` + `store/session_store.py`) |
| **2** | distillation pipeline rewrite: read the session-git DAG → timeline + graph (provenance, bi-temporal) | ❌ not started |
| **3** | recall rewrite: inject virtual only + navigation tools | ❌ not started |
| **4** | materialized views + core rebuild + hybrid search (vectors) | ❌ not started |
| **5** | UI: Projects panel / timeline / `/memory` | ⚠ partial (topbar project selector done, the rest not) |

**Key gap (the core of Phase 2)**: the entity tier (git) is built, but the virtual tier is **still v1's journal/wiki/core, and it still does not actually read the entity tier** — what `memory/wiki/ingest.py` feeds the LLM is the conversation text rendered by `get_branch()`, not the session-git `Call` DAG; the project-git commit history is never read at all. So the "entity → virtual" hop is not yet wired up, and the entity tier's contribution to memory quality is currently ≈ 0.

**The "default project" in the §0 overview diagram** has been simplified from the originally designed "fallback git repo" to a **pure logical label** (see §2.5); where the diagram and the prose disagree, §2.5 is authoritative.

**Known to-fix (out of scope for this doc, at the code level)**: after the `sessions-git → sessions` rename, the memory entity tier's `<state>/sessions/` collides with `agentic_programming`'s ask_user IPC directory (`paths.get_sessions_dir()` likewise points at `<state>/sessions/`) on the same directory; one of them needs renaming (e.g. ask_user switching to `<state>/followups/`).

## 1. Design Motivation / Difference from Existing Approaches

### Problems with the Current State

The v1 implementation (`memory.md`) is a **linear lossy chain**: `raw chat → extract 0-10 facts → journal → wiki → core`. Every layer drops information, and the abstraction layer (wiki) is built from the lossy summary of the layer above it, **never reading the entity tier directly**. The result: the entity tier (sessions/) and the abstraction layer (wiki) are two disconnected things, and the pipeline in between had even been broken (`build_default_llm` returned None, see §9, fixed).

### Comparison with Mainstream Frameworks

| Framework | Entity tier | Abstraction tier | Recall method | Time dimension | Knowledge graph |
|---|---|---|---|---|---|
| Claude Code | CLAUDE.md + sessions | auto-memory MEMORY.md (index + topic) | inject index, read topics on demand | ❌ | ❌ |
| OpenClaw | MEMORY.md + journal | same as above + wiki plugin | inject + semantic search | ❌ | ⚠ weak |
| mem0 | — | vector DB | RAG chunked injection | ⚠ write time | ❌ |
| Letta/MemGPT | conversation history | tiered (core/recall/archival) | LLM tool shuttling | ⚠ | ❌ |
| Zep/Graphiti | — | temporal knowledge graph | graph queries | ✅ bi-temporal | ✅ |
| **This design** | **git (session+project)** | **timeline + knowledge graph** | **inject virtual, LLM navigates back to entity** | **✅ bi-temporal** | **✅** |

### Four Novel Points (from a Paper's Perspective)

1. **Git as the substrate for episodic memory.** Entity memory is not a home-grown store; it uses git directly: commits are immutable = truth that cannot be tampered with; log = timeline; checkout = time machine; branch = explored branches; and the agent can read it with standard tools (`git log` / `grep` / `diff`). Auditable, reproducible, traceable.

2. **Provenance-pointer index, not a replacement.** The virtual tier does not replace the entity tier; it builds it a **coordinate-bearing navigation map**. Every virtual memory carries a pointer `(project, session, commit, timestamp)` back to its origin in the entity tier. This solves the fundamental problem of "lossy summaries dropping context" — **at any time you can follow the pointer back down to ground truth**.

3. **LLM-self-navigated recall (map → territory), not RAG chunk injection.** Traditional RAG slices out relevant chunks and stuffs them into the context, polluting the context and losing structure. This design injects only a compact virtual map; the LLM reads "in 2026-05, fixed a Windows bug in project X, full history in session local_13d5", and **when it needs the detail it walks over and fetches it with tools itself**. Small context, full fidelity, agent-driven retrieval.

4. **Dual projection of timeline + knowledge graph, both bi-temporal.** The same git substrate is projected into two orthogonal views: the timeline answers "when", the knowledge graph answers "what relationship". Both record two times — `event_time` (when the thing happened) and `ingestion_time` (when we wrote it down) — supporting time-travel queries and contradiction detection.

## 2. Entity Memory

### 2.1 Mental Model: Every Session Belongs to Some Project

Core simplification: **there are no "ownerless" sessions**. Every session belongs to a project; the only difference is whether that project is the user's real working directory.

```
when a session is created:
  is a work-dir path specified? (the topbar work_dir selector)
    yes → bind to that path's Project-Git (user's real code/doc repo),
          session repo lands at <project>/.openprogram/sessions/<id>/
    no  → default project (logical label project_id="default"),
          session repo lands at home root <state>/sessions/<id>/
```

This way the entity tier always has clear ownership, and the virtual tier can always be aggregated by project.

### 2.2 Disk Layout

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
└── memory/                             ← virtual tier (see §3)

<user work dir>/                         ← Project-Git (the real repo used when bound)
├── .git/                               reuse existing; git init if none. agent edits file → auto commit
└── .openprogram/sessions/<id>/         ← sessions bound to this project, repo lands in-project
```

### 2.3 Session-Git (implemented, retained)

This is already in place; see `<state>/sessions/<id>/` (`store/git_session.py`). Each node is a `Call` (role = user / llm / code), and edges are `called_by` (the call chain) + `reads` (context references). **v2 does not change Session-Git's storage; it only adds a `project_id` field to `meta.json`** (already added).

For a session bound to a real project, its repo is not at the home root but at `<project>/.openprogram/sessions/<id>/`, indexed by the `sessions/locations.json` index (`SessionStore._record_location` / `_session_dir`). This way a project's "code history (project-git) + conversation history (session-git)" both stay inside the project directory and travel with the project.

### 2.4 Project-Git

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

The registry is at `<state>/projects/projects.json`, keyed by the path-derived `proj_<8hex>` (the same directory always maps to the same project). `resolve_project(path)` reuses the directory's existing `.git`, or `git init`s if there isn't one (`ProjectGit.ensure_init`).

**Auto-commit (Strategy A, carried over from the original design)**: at turn end, if the session is bound to a real project and the agent changed files:
```
if not is_dirty_before_agent_touched():     # work tree was originally clean
    git add -A && git -c user.name=<agent> commit -m "[agent <session>] turn <N>: <user msg>"
else:
    # user has uncommitted changes → don't pollute, skip + UI warning
    skip + warn
```
The commit uses the agent's identity (`-c user.name/email` override), so even inside the user's own repo it stays distinguishable from the user's commits.

### 2.5 Semantics of the Default-Project

The default project is a **pure logical label**; it does not create a standalone git repo. The rationale: a casual chat is not bound to any directory, and the files it produces (if any) land in that session's own `workdir/`, so a "default project repo" would always be empty and purely redundant. Such sessions only carry `project_id="default"` for grouping / scope filtering, and their **entity memory is the session repo itself** (landing at the home root `<state>/sessions/<id>/`).

Only when a session is bound to a real working directory does an actual git repo appear (see §2.4). That is: **real path → real repo; no path → label only**. This avoids the meaningless bloat of "a pile of empty default repos" in the entity tier.

## 3. Virtual Memory

Two projections, both distilled from the entity tier, both carrying provenance pointers, both bi-temporal.

> Current state: the timeline/graph described in this section is **not yet implemented** (Phase 2). What is running now is v1's
> journal/wiki/core (see `memory.md`). Below is the target form.

### 3.1 Provenance Pointer (the Core Data Structure)

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

### 3.2 Timeline (Temporal / Journal)

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

### 3.3 Knowledge Graph (Graph / Wiki)

Entities + relationships. Answers "what is related to what, and how". **This upgrades v1's `wiki/<kind>/` into a real graph** — right now there are only isolated entity pages; v2 adds edges and time.

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

**Contradiction handling**: when a new edge conflicts with an old one, the old edge is not deleted; it is marked `superseded_by` pointing to the new edge. This preserves history and supports time-aware queries like "we once thought X, then found out Y".

### 3.4 Scope Labels (Cross-Project Isolation)

Every entity / edge carries a scope, and queries filter by the current context:

```
scope: "global"                  # across all projects (e.g. user language preference)
scope: "project:openprogram"     # this project only
scope: "agent:research"          # this agent only
```

More flexible than both Claude Code (pure directory hierarchy) and OpenClaw (pure per-agent) — a graph naturally supports multi-dimensional label filtering, which a filesystem hierarchy cannot. When chatting in the OpenProgram project, only the `global` + `project:openprogram` subgraph is projected.

### 3.5 Core (the Minimal Snapshot Always Injected)

Not a separate tier, but the **minimal projection** of the virtual tier: take the most recent high-signal events from the timeline + the high-frequency/high-confidence entities from the graph, and assemble them into a ≤2KB snippet. Injected into every system prompt. **Every line in core also carries a pointer**, so when the LLM sees core it knows where to drill in.

## 4. Recall Mechanism (the Two Links Wired Up)

### 4.1 Injection: Only the Virtual Tier

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

### 4.2 Navigation: the LLM Follows Pointers to Fetch for Itself

When the LLM needs raw detail, it calls navigation tools to walk back to the entity tier (added in Phase 3):

| Tool | What it does | Where in the entity tier it lands |
|---|---|---|
| `memory_open_session(session_id, [turn])` | read the raw messages of a session | `<sessions>/<id>/history/` |
| `memory_git_log(project_id, [since])` | view a project's commit history | Project-Git |
| `memory_git_show(project_id, commit)` | see what a given commit changed | git show |
| `memory_timeline(entity\|since\|until)` | a slice of the timeline | virtual timeline |
| `memory_graph_neighbors(entity, hops)` | a node's neighbors in the graph | virtual graph |
| `memory_search(query)` | hybrid search across the virtual tier | virtual (FTS + vectors) |

Example: the LLM reads "cp1252-bug fixed-by 73bfc05 ↪ session:local_13d5" in core and wants to know exactly how it was fixed → it calls `memory_git_show("proj_openprogram", "73bfc05")` to get the diff, or `memory_open_session("local_13d5")` to read the conversation at the time. **The virtual tier gives the coordinates, the entity tier gives the truth, and the LLM walks that path itself.**

## 5. Distillation Pipeline (Entity → Virtual)

### 5.1 Triggers

- **Incremental (session-end)**: session goes idle → distill the new commits of this session
- **Batch (sleep, daily at 03:00)**: re-organize, disambiguate, detect contradictions, rebuild core
- **Pre-compaction flush (borrowed from OpenClaw)**: insert a round before context compaction so the agent flushes the key information still in the conversation down to the entity tier first

### 5.2 Five Stages (read the entity git, not the old summary chain)

```
Stage 1: collect   — pull new commits from session-git + project-git since last distillation
                     (read full DAG nodes: user/llm/code + reads edges, not just chat text)
Stage 2: extract   — one LLM pass, extract timeline events + graph entities/relations, each tagged with provenance
Stage 3: link      — alias-resolve new entities against the existing graph ("worker"/"backend"/"daemon" → same node)
Stage 4: reconcile — contradiction detection, mark old edges superseded, don't delete
Stage 5: project   — re-project core.md / entity views / timeline shards
```

Stage 2 is the most expensive and the most prompt-tuning-hungry (Graphiti iterated on this part for several months). It can start with a rule-based version (pattern match "I prefer X" → edge), with the prompt-based version gradually replacing it.

### 5.3 Key: Read the DAG Directly, Not the Old Summaries

The v1 pipeline read "conversation text that had already been extracted". v2 **reads the `Call` DAG in session-git directly** — including `code` nodes (what tools the agent ran, with what arguments, with what results) and `reads` edges (what influenced a decision). These are exactly the gold mine for graph projection (`agent ──ran──► pytest ──produced──► 3 failures`), which v1 flattened into text and threw away.

> This is the biggest current gap (§0.5): the entity tier is built, but `memory/wiki/ingest.py` to this day still reads the
> rendered text from `db.get_branch()`, never the DAG, and never touches project-git. The first cut of Phase 2 is to connect this pipe.

## 6. Schema Overview

```
~/.openprogram/
├── sessions/<id>/               entity: session (implemented, meta carries project_id)
│   └── (project-bound sessions land at <project>/.openprogram/sessions/<id>/, indexed by sessions/locations.json)
├── projects/
│   └── projects.json            project registry (default project = logical label, no default/.git)
├── memory/                       virtual tier (timeline/graph added from Phase 2; currently v1 journal/wiki/core)
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

## 7. Implementation Phasing

| Phase | Content | Depends on | Effort | Status |
|---|---|---|---|---|
| **0** | fix baseline (LLM bridge / watcher / ingest fields), get v1 running | — | 1d | ✅ done |
| **1** | Project concept: schema + session.project_id + default project (label) + binding + project-git auto-commit | Session-Git | 3-4d | ✅ done |
| **2** | distillation pipeline rewrite: read session-git DAG → timeline + graph (with provenance, bi-temporal) | 1 | 5-7d | ❌ |
| **3** | recall rewrite: inject virtual only + navigation tools (memory_open_session / git_log / timeline / graph_neighbors) | 2 | 3-4d | ❌ |
| **4** | materialized view projection + core.md rebuild + hybrid search (vectors) | 2,3 | 3-5d | ❌ |
| **5** | UI: Projects panel + session-backtrack timeline + `/memory` slash command | 1 | 3-5d | ⚠ partial |

~2-3 weeks remaining. Each phase is independently verifiable.

**Suggested landing order for Phase 2** (prove the pipeline first, then spend on tuning the most expensive Stage 2):
1. First nail down the `Provenance` dataclass + the JSONL schema for timeline/graph + a thin read/write layer without any LLM;
2. Wire the distillation trigger to **read the session-git DAG (+ project-git log)**, not `get_branch()` text — get the "entity → virtual" pipe connected first;
3. Write a **rule-based extractor** (pattern match) first to get end-to-end working with real provenance attached, then swap in the LLM version;
4. Add the navigation tools, so recall can actually shrink down to "inject virtual only".

## 8. Relationship to Existing Code

**Reuse**:
- Session-Git (`<state>/sessions/`, `store/git_session.py`) — the first piece of the entity tier, used directly
- Project-Git (`store/project_store.py`) — the second piece of the entity tier, already implemented
- the `Call` DAG (`context/nodes.py`) — the node model of the entity tier, used directly
- the `MemoryProvider` abstract interface (`memory/provider.py`) — the recall hook shape is retained
- the sleep scheduler skeleton (`memory/scheduler.py`, `sleep/runner.py`) — changed to read git
- the FTS index (`memory/index.py`) — expanded into graph.sqlite

**Replace**:
- the fact extraction in `memory/builtin/summarizer.py` / the rendered-text reading in `memory/wiki/ingest.py` → Stage 2's entity/relationship extraction that reads the DAG
- the isolated topic pages of `memory/wiki/` → `graph/` (nodes + edges + time)
- the linear `journal → wiki → core` chain → fan-out from git

**Add**:
- `memory/graph/` (entities/edges/views)
- `memory/timeline/`
- navigation tools (expansion of `functions/tools/memory/`)
- bi-temporal + provenance fields

**Remove**:
- the `memory/journal/` journal layer (superseded by timeline; the raw truth already lives in session-git)

## 9. Phase 0 baseline bugs (fixed, 2026-05)

The baseline originally produced no memory at all (wiki/journal/core all empty). The root cause was a string of **cascading bugs**, now all fixed:

1. **`build_default_llm()` returned None** (`memory/llm_bridge.py`) — `_read_default_model()` used to hand-read the `agents.json` index file, and on a freshly installed machine where the index does not exist it returned None → the entire memory subsystem was silently disabled. Changed to delegate to `agents.manager.get_default()` (the fallback chain of index → DEFAULT_AGENT_ID → first agent).
2. **watcher silently dropped data** (`memory/session_watcher.py`) — when it couldn't get an LLM it used to `return True` (mark as processed), which permanently discarded that session's memory. Changed to `return False` (defer and retry); the raw conversation lives permanently in session-git, so deferring loses nothing.
3. **passed the wrong argument** — `ingest_session` takes `runtime=` (a Runtime with `.exec`), not an `llm=` callable; the watcher now pre-checks with `_build_runtime()` before passing it in.
4. **the generation step was given no tools** (`memory/wiki/ingest.py`) — `runtime.exec` used to carry no tools and wrote 0 files. Added `tools_allow=["read","write","edit","list","glob","grep","apply_patch"]` (avoiding bash's schema bug).
5. **`X | None` schema bug** (`functions/_runtime.py`) — PEP 604 union-type parameters were missing the `type` key, causing codex HTTP 400 (global impact, not just memory). `_python_type_to_json_schema` now recognizes `types.UnionType`.
6. **read the wrong field** (`memory/wiki/ingest.py`) — `_render_conversation` used to read only `m["content"]`, but DAG nodes store the body in `output`/`input`, so every real session rendered as empty and was judged an "empty/test session". Changed to `content or output or input`.

After the fix, wiki pages were verified to actually generate (`People/Fzkuji.md`, `Projects/OpenProgram/Architecture.md`), and the test data was cleaned up afterward. **Note: this link fixes v1's journal/wiki/core pipeline; v2's Phase 2 will replace it with the new DAG-reading pipeline (see §5.3).**

## 10. Paper-Angle Contributions (memo)

1. **Git-native episodic memory for LLM agents** — using a version control system as the immutable substrate for an agent's long-term memory, supporting backtracking / branching / standard-tool retrieval.
2. **Provenance-linked virtual memory** — the summary layer does not replace the source but indexes it with coordinates; resolves the fundamental tension of lossy summarization (compression vs fidelity).
3. **LLM-navigated recall** — the agent reads a compact map and navigates back to the source on demand, in contrast to RAG's blind chunked injection; smaller context, higher fidelity, agent-driven retrieval.
4. **Dual bi-temporal projection** — the same substrate projected into a timeline + knowledge graph, both bi-temporal, supporting time travel and contradiction tracking.

Evaluation directions (memo): the trade-off of context footprint vs recall accuracy; long-range consistency across multiple sessions; contradiction-detection recall; comparison against RAG / Zep / mem0 baselines.
