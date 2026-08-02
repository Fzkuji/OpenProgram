# Memory subsystem

How OpenProgram makes the agent "remember" things across conversations.

> This document covers the memory subsystem end to end: the two-tier
> architecture it targets, the linear summarization chain running today, and the
> path between them. For the entity tier's git substrate in detail see
> [`git-as-entity-memory.md`](git-as-entity-memory.md) and
> [`entity-memory.md`](entity-memory.md); for the abstract tier's Timeline /
> Graph / Core stores see [`virtual-memory.md`](virtual-memory.md).
>
> Path conventions: all state lives under `~/.openprogram/` (= `get_state_dir()`);
> named profiles use `~/.openprogram-<profile>/`.

## Why this exists

A vanilla LLM forgets everything when a conversation ends. Each new chat
starts from zero, so the user retells the same facts ("I'm a product
manager, please avoid jargon", "the project lives at `~/Projects/foo`")
session after session. The Memory subsystem fixes that by reading every
finished conversation, distilling durable facts, and feeding the most
important ones back into the next conversation's prompt.

Two product properties we care about:

1. **The model gets the right facts unprompted.** When you open a new
   chat, your stable preferences and the project's stable facts are
   already in the model's working memory — no manual `/remember`.
2. **Storage stays small and reviewable.** Memory is plain Markdown
   files on disk, human-readable, easy to edit or wipe by hand. No
   opaque vector store, no fine-tuned weights.

## Architecture: entity tier + virtual tier

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

The entity tier's two git stores are detailed in
[`entity-memory.md`](entity-memory.md) and
[`git-as-entity-memory.md`](git-as-entity-memory.md); the virtual tier's
Timeline / Graph / Core stores, the five-stage distillation pipeline, and the
navigation tools are detailed in [`virtual-memory.md`](virtual-memory.md).

## The three layers running today

The abstraction tier currently in the code is the linear summarization chain
below. Timeline supersedes `short-term/`, Graph supersedes `wiki/`, and `core.md`
is re-projected from both once the pipeline above lands.

```
┌────────────────────────────────────────────────────────────────┐
│  short-term/YYYY-MM-DD.md                                      │
│  Raw daily notes. Append-only. Each line records one observation.│
│  Lifetime: kept indefinitely, but only the recent ones feed     │
│  the next phase. Source of truth for "what was actually said".  │
└────────────────────────────────────┬───────────────────────────┘
                                     │  sleep · light + deep
                                     ▼
┌────────────────────────────────────────────────────────────────┐
│  wiki/<kind>/<slug>.md                                         │
│  Curated knowledge pages with structured frontmatter (claims,  │
│  evidence, confidence, sources). Four kinds:                    │
│      user/         — facts about the human                      │
│      entities/     — people, products, places, organizations    │
│      concepts/     — things they keep talking about             │
│      procedures/   — things they keep doing                     │
│  + index.md / log.md / reflections.md at the root.              │
│  Lifetime: indefinite. Rewritten by deep / REM phases.          │
└────────────────────────────────────┬───────────────────────────┘
                                     │  sleep · deep + REM
                                     ▼
┌────────────────────────────────────────────────────────────────┐
│  core.md                                                       │
│  <2 KB. The bits the model literally sees at the top of every  │
│  system prompt. Frozen for the duration of any one session so  │
│  the provider's prompt cache hits.                             │
└────────────────────────────────────────────────────────────────┘
```

Everything lives under `<state>/memory/`, which defaults to
`~/.openprogram/memory/` and follows `--profile` / `OPENPROGRAM_STATE_DIR`.

## End-to-end flow

There are two ways a memory observation enters the system, and one
background process that consolidates them.

### Flow A — session-end summarization (the main one)

Triggered automatically by `session_watcher` (`memory/session_watcher.py`).

```
conversation ends ─────► poll every 5 min ─────► session idle ≥30 min?
                                                       │ yes
                                                       ▼
                                            load all messages from SessionDB
                                                       │
                                                       ▼
                                       send to LLM with summarizer prompt
                                       (build_default_llm + BuiltinMemoryProvider)
                                                       │
                                                       ▼
                          parse JSON array of {type, text, tags, confidence}
                                                       │
                                                       ▼
                              append each entry to short-term/<today>.md
```

The prompt template lives in `memory/builtin/summarizer.py:SYSTEM_PROMPT`.
It asks the model for 0–10 short facts, classified into:

- `user-pref` — "user prefers concise responses"
- `env` — "project lives at ~/Projects/foo, Python 3.12"
- `project` — "product is called OpenProgram"
- `procedure` — "user runs tests via `pytest -q`"
- `fact` — anything else durable

Each entry carries a confidence score (0.0–1.0) — important later when
deep-sleep promotes the high-confidence ones to wiki.

State of which sessions have already been processed sits at
`<state>/memory/.state/session-end.json`, so a worker restart doesn't
re-process every conversation.

### Flow B — pre-compression summarization

When a conversation grows past the context window, the runtime compresses
older messages. Before they're dropped, the same summarizer runs over the
about-to-be-dropped slice (`on_pre_compress` in
`memory/builtin/provider.py`). The extracted facts feed into the
compression summary so insights survive even when the raw turns don't.

This path is automatic and silent. Not a separate file, not a separate
schedule.

### Sleep — the consolidation worker

A daemon thread in the worker (`memory/scheduler.py`) wakes at 03:00
local time every day and runs three cooperative phases in order:

```
light  ─► dedupe + score short-term entries                  (no LLM)
   │      Output: write phase signals to .state/sleep-stage.json
   ▼
deep   ─► promote candidates to wiki, rewrite affected pages, refresh core.md (LLM)
   │      Light scored each entry; deep picks the top N by score and
   │      writes / updates a wiki page per fact, then regenerates core.md
   │      with the highest-signal short text snippets that fit in 2 KB.
   ▼
rem    ─► scan wiki for themes / contradictions, append reflections.md (LLM)
          Looks at the whole wiki and writes free-form observations:
          "user mentioned X in three sessions, suggests a recurring
          interest", "concepts/A says X but procedures/B implies Y".
```

The phases are decoupled: light runs unconditionally; deep and REM
need an LLM callable to be wired (the worker passes one in at boot via
`build_default_llm`). If no LLM is available, light still collects and
scores; deep is a no-op until next sweep with an LLM.

Files involved per phase:

| Phase | File                              | Output                                |
|-------|-----------------------------------|---------------------------------------|
| light | `memory/sleep/light.py`           | `.state/sleep-stage.json` (scores)    |
| deep  | `memory/sleep/deep.py`            | `wiki/<kind>/<slug>.md` + `core.md`   |
| rem   | `memory/sleep/rem.py`             | `wiki/reflections.md`                 |

After every sweep, `.state/last-sleep.json` records `{ts, phase,
promoted, skipped}` so you can `cat` it to see when memory last ran.

## What the model actually sees

At session start, the runtime adds `core.md` to the system prompt as a
prefix block. Format mirrors Hermes' `MEMORY.md / USER.md` banner:

```
═════════════════════════════════════════════════════
OpenProgram memory (machine-wide) — 6% (116/2048 chars), last consolidated 2026-05-08
═════════════════════════════════════════════════════
USER: User prefers terse answers in Chinese.
§
ENTITY: Backend daemon called worker, not daemon
§
ENTITY: Uses Ink for TUI

[for full context use memory_recall <query>]
```

### The 2 KB budget is enforced, not advisory

`CORE_BUDGET_CHARS = 2048` caps what this block contributes. The cap is
real: `system_prompt_block` runs the file through `strip_chrome` (dropping
the rule-line header and the trailing pointer, which are decoration rather
than content) and then `truncate_to_budget` before the text reaches the
prompt. Consolidation can overshoot — the header prints the overshoot as a
percentage — so the read path is where the budget has to hold.

Truncation cuts at **section boundaries**: markdown headings are kept or
dropped whole, so the model never reads half a thought. A single section
larger than the whole budget falls back to the next boundary down, a
paragraph then a sentence, so the block never ends mid-word. Whatever was
dropped is announced in place, pointing at `memory_browse` for the rest.

This matters more than its size suggests, because this block is on the
system prompt of **every turn of every session**. Left unenforced it was
the largest single item in the prompt, at 174% of its own stated budget.

The footer points to `memory_recall` — a tool the model can call mid-turn
to fetch a specific wiki page when it needs more detail than `core.md`
holds. Implementation in `memory/tools/` (the tool surface), backed by
`memory/builtin/recall.py` (the FTS search).

## Retrieval: FTS index for recall

`<state>/memory/index.sqlite` carries a SQLite FTS5 index over wiki
pages and short-term entries. Two tables:

- `wiki_fts` — every wiki page indexed on title + body + claims + aliases
- `short_fts` — every short-term entry indexed on text + tags

The `memory_recall` tool queries this index, ranks by BM25 + recency,
returns the top 3-5 matching entries. Index is rebuilt incrementally
on every write (no separate sync step).

## File layout reference

```
<state>/memory/
    core.md                           injected into system prompt
    short-term/
        2026-05-08.md                 daily notes
        2026-05-09.md
        ...
    wiki/
        index.md                      hand-edited TOC
        log.md                        free-form notes
        reflections.md                REM-phase output
        user/
            profile.md                facts about the human
        entities/
            <slug>.md
        concepts/
            <slug>.md
        procedures/
            <slug>.md
    index.sqlite                      FTS index over wiki + short-term
    .state/
        recall-counts.json            "this page was recalled N times"
        last-sleep.json               last sweep timestamp + outcome
        sleep-stage.json              light phase's scored candidates
        session-end.json              per-session "already processed" markers
        sleep.lock                    advisory lock for concurrent sweeps
```

## Code map

```
openprogram/memory/
    __init__.py            public API + module-level docstring
    provider.py            MemoryProvider abstract interface
    builtin/
        provider.py        BuiltinMemoryProvider — default implementation
        summarizer.py      LLM prompt + JSON parser for session-end
        recall.py          FTS query + ranking
    short_term.py          append-only daily file writer
    wiki.py                wiki page read / write helpers
    core.py                core.md render / write
    index.py               FTS index management
    store.py               filesystem layout (paths + ensure dirs)
    schema.py              dataclasses (ShortTermEntry, WikiPage, …)
    session_watcher.py     polls SessionDB, fires on idle
    scheduler.py           daemon thread that runs sleep at 03:00 daily
    llm_bridge.py          provider-agnostic LLM callable factory
    recall_counts.py       per-page recall counter (used by ranking)
    sleep/
        __init__.py        re-exports run_sweep + run_phase
        runner.py          orchestrates light → deep → REM
        light.py           dedupe + score
        deep.py            promote to wiki + rewrite core
        rem.py             cross-page reflections
        scoring.py         signal heuristics (frequency, recency, etc.)
```

## Plugin point

`MemoryProvider` (`memory/provider.py`) is the abstract base. The default
is `BuiltinMemoryProvider`. To swap in a different memory backend (mem0,
Honcho, Hindsight, a vector store, …) register a subclass and wire it
into the runtime via the agent config. The runtime calls only these
lifecycle hooks:

```python
initialize(session_id, **kwargs)
system_prompt_block() -> str            # injected at session start
prefetch(query, *, session_id="") -> list[str]   # before each LLM call
on_session_end(messages) -> None        # after a turn ends idle
on_pre_compress(messages) -> str        # before context compression drops messages
```

Everything else (file layout, sleep phases, FTS index) is implementation
detail of the builtin provider. A plugin doesn't have to mirror the
three-layer model.

## Failure modes

| Symptom                          | Likely cause                                   | Fix                                     |
|----------------------------------|------------------------------------------------|-----------------------------------------|
| No `short-term/<today>.md`       | Session-end summarizer found nothing durable, or LLM call returned empty / unparseable | Inspect `.state/session-end.json` — if today's session_ids are there with timestamps, summarizer was called; the conversation just lacked durable facts |
| `core.md` only has framework facts | Deep-phase has no high-confidence personal observations yet | Have a few real conversations about your project / preferences |
| `last-sleep.json` shows `promoted=0` | Same — short-term entries below score threshold | Raise count of long conversations or hand-edit a wiki page |
| Summarizer returns []            | LLM ignored the system prompt (e.g. `claude-code` provider — both meridian and the older claude-max-api-proxy drop the system role) | `build_default_llm` folds system into user when provider needs it; verify with `grep '_inline_system' openprogram/memory/llm_bridge.py` |
| Stale session-end state          | A previous worker crashed mid-process          | Delete `.state/session-end.json` — sessions get re-scanned next poll |
| Sleep didn't run last night      | Worker wasn't running at 03:00 OR LLM unavailable | `scheduler.start_in_worker` is called at worker boot; check `worker.log` for `[worker] memory: sleep + session-end watcher running` |

## Design lineage

- **Three-layer split** (short / wiki / core): borrowed from Karpathy's
  "LLM Wiki" pattern, where raw observations get distilled into a wiki
  and the wiki's TL;DR feeds the prompt.
- **`MEMORY.md` injection format**: copied from Hermes so users moving
  between agents see familiar banners.
- **MemoryProvider interface**: also from Hermes (`memory_provider.py`),
  to keep the option of plugging in mem0 / Honcho / etc. later.
- **Sleep as a daily cron with light/deep/REM phases**: a riff on actual
  sleep cycles, mostly to make the deep-LLM-pass cheap (one batch per
  day) instead of running it on every turn.

## Research angle

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

## Appendix: Implementation status

The entity tier is in place: the Project schema, session binding, project-git,
and sessions landing inside the project are all implemented
(`store/project_store.py`, `store/session_store.py`). The read layer for
distillation is available in `store/session/provenance.py`.

The virtual tier is not yet built. What runs today is the linear
journal/wiki/core chain described above, and it still reads the conversation
text rendered by `get_branch()` rather than the session-git `Call` DAG; the
project-git commit history is not read at all. Wiring that hop is the first step
of the distillation pipeline.

Recall still injects the v1 core; the navigation tools are not yet registered.
The UI carries a topbar project selector; the Projects panel, backtrack
timeline, and `/memory` command are not built.
