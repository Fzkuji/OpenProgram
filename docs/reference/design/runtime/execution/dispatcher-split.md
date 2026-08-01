# `agent/dispatcher` — a responsibility-scoped package

> This document describes how the webui chat turn's execution path is organized:
> why the dispatcher is a package rather than one module, what each file owns,
> the test seam that constrains where code may live, and the compatibility
> surface callers depend on. Implementation status is in the appendix.

`dispatcher` is the real execution path of a webui chat turn. It is a package
rather than a single module, under the no-1000-line-files rule and the
"hierarchical code structure — module dirs by responsibility" convention.

## 1. Why it is split

As a single module, `openprogram/agent/dispatcher.py` was 1928 lines. One file
held the whole turn lifecycle, two functions of roughly 300 and 830 lines, and
all the turn-finalization bookkeeping. That shape is hard to read, hard to test
in isolation, and every new concern — a bookkeeping step, a persistence detail —
grows the same file.

The largest single function is `process_user_turn` at ~835 lines. It is
self-documented as seven numbered phases, so the seams are clear; they simply
live inside one function instead of being separable units.

## 2. The single-module structure it comes from

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

## 3. Package layout

`openprogram/agent/dispatcher/` is a package, each file a single
responsibility, none over ~500 lines:

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

`process_user_turn` in `turn.py` is an orchestrator: load → persist user →
attach runtime → run loop → persist assistant → finalize → emit result, each a
named call into a sibling module. Error taxonomy classification (phase 4, the
loop's `except`) stays co-located with the loop in `loop.py`, matching
`docs/reference/design/providers/reliability/error-taxonomy-propagation.md`.

## 4. The test seam that constrains code placement

The dispatcher unit tests monkeypatch `D._resolve_model` /
`D._load_agent_profile` / `D._run_loop_blocking` on the **package** object, and
capture `orig = D._run_loop_blocking` to run the real loop with a fake
`stream_fn`. A function's internal helper lookups resolve in *its own* module
globals, so moving `_run_loop_blocking` to `loop.py` makes its `_resolve_model`
call miss the `D.*` patch and breaks about 40 tests. Three consequences:

- Functions that internally call the test-patched helpers (`_run_loop_blocking`)
  stay in `__init__.py`.
- In-function **phases** (persist, finalize) extract cleanly by taking the
  already-resolved model and profile as explicit arguments — the dispatcher
  resolves them once, under the patch, and hands them down, so the extracted
  module never calls a patched helper.
- Standalone functions that touch none of the patched helpers
  (`_wrap_agentic_runtime_block`) move freely.

Moving `_run_loop_blocking` into `loop.py` therefore requires either a
patch-stable helper seam (access via `_model_tools.<fn>` at call time) or
updated test patch targets. That is its own change, not something folded into a
code-motion commit.

## 5. Back-compat

Callers import `from openprogram.agent.dispatcher import process_user_turn`
(and `dispatch_forced_tool_call`, `TurnRequest`, `TurnResult`,
`trigger_compaction`). The package `__init__.py` re-exports the full public
surface of the original module, so **no caller changes**. A repo-wide grep of
`from openprogram.agent.dispatcher import` / `dispatcher\.` before and after any
move must yield an identical import set.

## 6. Verification

Each move is verified by: `py_compile` on the package, `python -c "from
openprogram.agent import dispatcher; dispatcher.process_user_turn;
dispatcher.dispatch_forced_tool_call"`, `openprogram worker restart` with
`/healthz` ok and `tools_registered` unchanged (55), then a real chat turn
through the webui (send a message, get a streamed reply, confirm it persists
across reload). The existing dispatcher-touching unit tests stay green, with no
behavior assertion changes — this is structure only.

## 7. Non-goals

The split does not change the turn lifecycle, the error taxonomy, the
persistence schema, or any event payload. It does not split `runtime.py` /
`server.py` (separate items), and it introduces no async where the path is
currently blocking.

## Appendix: Implementation Status

Moves are made one per commit, pure code motion, with compile + import +
worker-restart-healthz green before the next one. The order runs from smallest
blast radius outward: `types.py` (three dataclasses + sentinel, no internal
deps), then `titles.py` + `forced_tool.py` (leaf helpers, few callers), then
`persistence.py` (phases 2 and 5 as `persist_user_turn(...)` /
`persist_assistant_message(...)` with explicit args, no closure over
`process_user_turn` locals — the phases read and write many locals, so the
signatures need deliberate design), then `finalize.py` (phase 6 as
`finalize_turn(...)`, the most self-contained block), then `runtime_attach.py`,
then `loop.py`, leaving `turn.py` as the orchestrator. A phase that resists
clean extraction because of interdependent locals is left in place and the
reason recorded here, rather than forced into a leaky split.

Landed: dead-code removal (`_legacy_dispatch_forced_tool_call_unused`), the
package itself, `types.py`, `titles.py` + `forced_tool.py`,
`runtime_attach.py` (`_wrap_agentic_runtime_block`), `finalize.py` (phase 6),
and `persistence.py` (phase 5 assistant persist). `__init__.py` is 1234 lines,
down from 1928. `turn.py` and `loop.py` are not yet extracted; `loop.py`
depends on resolving the test seam in §4.

Owner: agent/runtime.
