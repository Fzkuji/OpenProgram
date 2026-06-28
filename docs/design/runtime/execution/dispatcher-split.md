# Splitting `agent/dispatcher.py` into a responsibility-scoped package

Status: **in progress** · dead-code removed (1fab7479) · step 0 package · step 1 types.py · step 2 titles.py + forced_tool.py · step 3a runtime_attach.py (`_wrap_agentic_runtime_block`) · step 4 finalize.py (phase 6) · step 5a persistence.py (phase 5 assistant persist) · `__init__.py` now <1000 lines · Owner: agent/runtime · Created: 2026-06-04

> **Test seam note (discovered during step 3).** The dispatcher unit tests
> monkeypatch `D._resolve_model` / `D._load_agent_profile` / `D._run_loop_blocking`
> on the **package** object, and capture `orig = D._run_loop_blocking` to run
> the real loop with a fake `stream_fn`. A function's internal helper lookups
> resolve in *its own* module globals, so moving `_run_loop_blocking` to
> `loop.py` would make its `_resolve_model` call miss the `D.*` patch and break
> ~40 tests. Therefore: functions that internally call the test-patched helpers
> (`_run_loop_blocking`) stay put for now; in-function **phases** (persist /
> finalize) extract cleanly by passing the already-resolved model/profile as
> explicit args (the dispatcher resolves them once, under the patch, and hands
> them down), so the extracted module never calls a patched helper. Standalone
> functions that touch none of the patched helpers (`_wrap_agentic_runtime_block`)
> move freely. The eventual `loop.py` move needs either a patch-stable helper
> seam (access via `_model_tools.<fn>` at call time) or updated test patch
> targets — tracked as its own step, not folded into a code-motion commit.

Roadmap item under the no-1000-line-files rule and the "hierarchical code
structure — module dirs by responsibility" convention. `dispatcher.py` is the
webui chat turn's real execution path; this plans how to break it apart without
changing behavior.

## 1. Problem

`openprogram/agent/dispatcher.py` is 1928 lines (was 2059; the dead
`_legacy_dispatch_forced_tool_call_unused` was deleted in 1fab7479). One file
holds the whole turn lifecycle, two ~300–830-line functions, and all the
turn-finalization bookkeeping. It is hard to read, hard to test in isolation,
and every new concern (a new bookkeeping step, a new persistence detail) grows
the same file.

The single worst offender is `process_user_turn` at ~835 lines (599–1433). It is
already self-documented as seven numbered phases, so the seams are clear; they
just live inside one function instead of being separable units.

## 2. Current structure (grounded, post-1fab7479)

```
line   symbol                                  role
49     _InheritParent                          sentinel for "inherit parent id"
58     TurnRequest                             input dataclass
116    TurnResult                              output dataclass (+ error taxonomy fields)
158    _wrap_agentic_runtime_block (~308 ln)   wrap an @agentic_function block as a turn
466    dispatch_forced_tool_call (~133 ln)     forced single tool-call path
599    process_user_turn (~835 ln)             MAIN turn orchestration — phases 1–7
1434   _noop / _default_title                  tiny helpers
1443   _maybe_auto_title (~28 ln)              placeholder-title backfill
1471   trigger_compaction (~63 ln)             compaction trigger
1534   _run_loop_blocking (~395 ln)            the actual agent loop (chat main path)
```

`process_user_turn`'s seven phases (line → phase):

```
648    1. ensure session, load active-branch history
676    2. persist user message + attachment manifest
772    3. attach Runtime (real provider) with the session GraphStore
864    4. run the agent loop; classify + report errors        <- error taxonomy lives here
1036   5. persist assistant message
1193   6. bookkeeping: head_id, tokens, context-commit backfill (6.1),
       usage feedback (6.4), auto-title (6.5), compaction signal (6.6),
       git commit (6.8), project auto-commit (6.9), snapshot eviction (6.95)
1413   7. final TurnResult event
```

## 3. Proposed package layout

Convert the module into `openprogram/agent/dispatcher/` (a package), each file a
single responsibility, none over ~500 lines:

```
dispatcher/
  __init__.py        re-export the public surface (back-compat, see §5)
  types.py           _InheritParent, TurnRequest, TurnResult, INHERIT_PARENT
  turn.py            process_user_turn — thin orchestrator calling the phases
  persistence.py     phase 2 + 5: persist user/assistant nodes, attachment manifest
  runtime_attach.py  phase 3: create_runtime + GraphStore wiring, _wrap_agentic_runtime_block
  finalize.py        phase 6: head/token bookkeeping, usage feedback, git + project commit, eviction
  titles.py          _default_title, _maybe_auto_title, trigger_compaction
  forced_tool.py     dispatch_forced_tool_call
  loop.py            _run_loop_blocking — the agent loop + its error boundary
```

`turn.py`'s `process_user_turn` becomes an orchestrator: load → persist user →
attach runtime → run loop → persist assistant → finalize → emit result, each a
named call into the sibling modules. The error taxonomy classification (phase 4
/ the loop's except) stays co-located with the loop in `loop.py`, matching
`docs/design/providers/reliability/error-taxonomy-propagation.md`.

## 4. Migration order (smallest blast radius first)

Each step is its own commit, compiles + imports + worker-restart-healthz green
before the next. Pure code-motion — no logic edits in the same commit as a move.

1. **types.py** — move the three dataclasses + sentinel. Lowest risk: they have
   no internal deps. `__init__` re-exports them.
2. **titles.py + forced_tool.py** — leaf helpers, few callers.
3. **persistence.py** — extract phases 2 & 5 as `persist_user_turn(...)` /
   `persist_assistant_message(...)` taking explicit args (no hidden closure
   over `process_user_turn` locals). This is where most care goes — the phases
   read/write many locals, so the function signatures must be drawn deliberately.
4. **finalize.py** — extract phase 6 as `finalize_turn(...)`; it is the most
   self-contained block (bookkeeping only, already sub-numbered 6.1–6.95).
5. **runtime_attach.py** — phase 3 + `_wrap_agentic_runtime_block`.
6. **loop.py** — `_run_loop_blocking` + its error boundary.
7. **turn.py** — what remains of `process_user_turn` is the orchestrator.

If any phase resists clean extraction (too many interdependent locals), stop and
record why in this doc rather than forcing a leaky split.

## 5. Back-compat

Everything imports `from openprogram.agent.dispatcher import process_user_turn`
(and `dispatch_forced_tool_call`, `TurnRequest`, `TurnResult`,
`trigger_compaction`). The package `__init__.py` re-exports the full current
public surface so **no caller changes**. Verify with a repo-wide grep of
`from openprogram.agent.dispatcher import` / `dispatcher\.` before and after — the
import set must be identical.

## 6. Verification

Per step: `py_compile` the package, `python -c "from openprogram.agent import
dispatcher; dispatcher.process_user_turn; dispatcher.dispatch_forced_tool_call"`,
`openprogram worker restart` + `/healthz` ok + `tools_registered` unchanged
(55), then a real chat turn through the webui (send a message, get a streamed
reply, confirm it persists across reload). The existing dispatcher-touching unit
tests must stay green. No behavior assertion changes — this is structure only.

## 7. Non-goals

Not changing the turn lifecycle, the error taxonomy, persistence schema, or any
event payload. Not splitting `runtime.py` / `server.py` (separate items). Not
introducing async where the path is currently blocking.
