# `openprogram/memory/`

Persistent, machine-wide memory for OpenProgram agents.

**Design docs:** [`docs/design/memory.md`](../../docs/design/memory.md) (v1,
what this directory implements today) and
[`docs/design/memory-v2.md`](../../docs/design/memory-v2.md) (the target
two-tier entity/virtual design — Phase 0+1 done, Phase 2+ pending).
Read those first — this README is a navigation aid for working in the
directory, not a substitute.

> **v1 vs v2:** the code in this directory is the v1 linear chain
> (`journal → wiki → core`). The git-backed *entity* layer (Session-Git
> + Project-Git) is already built under `openprogram/store/`, but the v2
> *virtual* layer (timeline + knowledge graph with provenance) is not —
> v1 is still what runs. See memory-v2.md §0.5 for the status table.

## TL;DR

Three layers on disk under `<state>/memory/`:

1. `journal/YYYY-MM-DD.md` — raw daily notes appended after every
   conversation ends idle.
2. `wiki/<kind>/<slug>.md` — curated knowledge pages with frontmatter,
   organised by `user/` / `entities/` / `concepts/` / `procedures/`.
3. `core.md` — the <2 KB always-on snippet injected into every system
   prompt.

A background "sleep" sweep at 03:00 local time promotes high-confidence
journal notes into the wiki and regenerates `core.md`. Plus an
`on_session_end` hook that summarises freshly idle conversations into
journal.

## File map

| File                  | Role                                              |
|-----------------------|---------------------------------------------------|
| `provider.py`         | `MemoryProvider` abstract base (plugin seam)      |
| `builtin/`            | Default `BuiltinMemoryProvider`                   |
| `builtin/summarizer.py` | Session-end LLM prompt + JSON parser            |
| `builtin/recall.py`   | FTS query + ranking for `memory_recall` tool      |
| `journal.py`       | Append-only daily file writer                     |
| `wiki/`               | Wiki page read / write helpers (package)          |
| `core.py`             | `core.md` render + write                          |
| `index.py`            | SQLite FTS index management                       |
| `store.py`            | Filesystem layout (paths)                         |
| `schema.py`           | Dataclasses (`JournalEntry`, `WikiPage`, …)     |
| `session_watcher.py`  | Polls SessionDB; fires `on_session_end`           |
| `scheduler.py`        | Daemon thread that runs sleep daily at 03:00      |
| `llm_bridge.py`       | Provider-agnostic LLM callable factory            |
| `recall_counts.py`    | Per-page recall counter (recency boost)           |
| `sleep/runner.py`     | Orchestrates light → deep → REM                   |
| `sleep/light.py`      | Dedupe + score (no LLM)                           |
| `sleep/deep.py`       | Promote to wiki + rewrite `core.md` (LLM)         |
| `sleep/rem.py`        | Cross-page reflections (LLM)                      |
| `sleep/scoring.py`    | Signal heuristics                                 |

## Plugin point

To swap in a different memory backend (mem0 / Honcho / vector store / …)
subclass `MemoryProvider` and register the implementation. The runtime
only consumes these hooks:

```python
initialize(session_id, **kwargs)
system_prompt_block() -> str
prefetch(query, *, session_id="") -> list[str]
on_session_end(messages) -> None
on_pre_compress(messages) -> str
```

Everything else — three-layer split, sleep phases, FTS index — is
implementation detail of the builtin provider.

## Common ops

```bash
# Look at today's raw observations
cat ~/.openprogram/memory/journal/$(date +%Y-%m-%d).md

# See what's in the always-on core
cat ~/.openprogram/memory/core.md

# When did sleep last run, and did it promote anything?
cat ~/.openprogram/memory/.state/last-sleep.json

# Manually trigger a session-end scan (idle_minutes=0 = process everything)
python -c "from openprogram.memory.session_watcher import run_now; print(run_now(idle_minutes=0))"

# Manually trigger a sleep sweep
python -c "from openprogram.memory.sleep import run_sweep; from openprogram.memory.llm_bridge import build_default_llm; print(run_sweep(llm=build_default_llm()))"

# Wipe everything (will rebuild on next session-end)
rm -rf ~/.openprogram/memory/journal ~/.openprogram/memory/wiki ~/.openprogram/memory/core.md ~/.openprogram/memory/index.sqlite ~/.openprogram/memory/.state
```

## Provider quirks

Some chat providers silently ignore the OpenAI `system` role. The
`claude-code` provider is the current known offender — both supported
daemons (`meridian` and the older `claude-max-api-proxy`) forward
through the Claude Code SDK, which only honours the user turn.
`llm_bridge.build_default_llm` detects those providers and folds the
system prompt into the user message before calling them, so the
summarizer's instructions actually reach the model. New offending
provider IDs go in the `_proxy_providers` set in `llm_bridge.py`.
