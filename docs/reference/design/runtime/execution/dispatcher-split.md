# `agent/dispatcher` — a responsibility-scoped package

> This document describes how the chat turn's execution path is organized: why
> the dispatcher is a package rather than one module, what each file owns, the
> test seam that constrains where code may live, and the compatibility surface
> callers depend on.

`dispatcher` is the real execution path of a chat turn. It is a package rather
than a single module, under the no-1000-line-files rule and the "hierarchical
code structure — module dirs by responsibility" convention. Every caller
(webui / channels / CLI / task runner) enters through `process_user_turn`.

## 1. Shape

`__init__.py` is the orchestrator only: it holds `process_user_turn` (the
entry point plus the session-goal continuation loop), `_process_turn_once`
(the numbered-phase pipeline, each phase a named call into a sibling module)
and the re-export surface. Every pipeline stage lives in its own module.

## 2. Package layout

```
dispatcher/
  __init__.py        orchestrator: process_user_turn / _process_turn_once + public surface
  types.py           _InheritParent, TurnRequest, TurnResult, INHERIT_PARENT
  prep.py            phases 1-2: ensure session, resolve history (override /
                     branch walk / fork), predecessor + memory prefetch,
                     build + persist the user message
  turn_context.py    phase 3: TurnBindings — per-turn ContextVars (GraphStore,
                     DAG runtime, turn id, worktree cwd, deferred-tool set)
                     + project auto-commit baseline; bind/release pair
  stream_tap.py      the on_event wrap that persists each completed tool
                     row incrementally as tool_result envelopes stream by
  loop_runner.py     phase 4: run_loop_blocking — build AgentContext, snip /
                     auto-compact, run agent_loop, drain its EventStream
  persistence.py     phase 5: persist the assistant message
  finalize.py        phase 6: head/token bookkeeping, context-commit backfill,
                     usage feedback, auto-title, git + project commit, eviction
  error_path.py      the except branch: fold the error into the placeholder
                     (or write a standalone error node), finalize the failed
                     turn, taxonomy classification, error TurnResult
  turn_writer.py     TurnWriter — the ONE writer allowed to move the session
                     head for a turn (persist_user / open_placeholder /
                     record_failure / head_for_finalize)
  titles.py          _default_title, _maybe_auto_title, trigger_compaction
  forced_tool.py     dispatch_forced_tool_call (webui forced single tool call)
  runtime_attach.py  _wrap_agentic_runtime_block — render an @agentic_function
                     call as a runtime-block turn
```

Phase numbering inside `_process_turn_once`:

```
1-2  prep.prepare_turn            session + history + user persist
3    turn_context.TurnBindings    ContextVar bind (released in finally)
3b   turn_writer.open_placeholder assistant placeholder row
4    loop_runner.run_loop_blocking (+ reactive compact retry on overflow)
5    persistence.persist_assistant_message
6    finalize.finalize_turn
7    final result event + TurnResult
err  error_path.handle_turn_error
```

## 3. Head convergence invariant

`TurnWriter` is the only decider of where a turn's head goes: it writes the
head on user persist and failure recording, and `head_for_finalize` supplies
the value `finalize.py` stamps in its phase-6 `update_session` bookkeeping.
The single exception is `forced_tool.py` (the forced single-tool path, which
bypasses the agent loop and writes its own head). No other dispatcher module
calls `set_head` or originates a `head_id` value; `error_path.py` decides
*which* node becomes the head after a failure but delegates the write to
`TurnWriter.record_failure`.

## 4. The test seam that constrains code placement

The dispatcher unit tests monkeypatch `D._resolve_model` /
`D._load_agent_profile` / `D._run_loop_blocking` / `D.process_user_turn` on
the **package** object, and capture `orig = D._run_loop_blocking` to run the
real loop with a fake `stream_fn`. Two rules keep those seams live:

- `__init__.py` call sites reference the seam names as module globals
  (`_run_loop_blocking(...)`, `_load_agent_profile(...)`), so a
  `patch.object(D, ...)` swap is seen at call time.
- Extracted modules never freeze a seam with a module-level from-import.
  `loop_runner.py` resolves the profile and model through the package
  attribute at call time (`from openprogram.agent import dispatcher` inside
  the function, then `dispatcher._load_agent_profile(...)`), so the same
  patches apply to the real loop. Modules that need a resolved profile/model
  but are not under a seam (`finalize.py`) take them as explicit arguments —
  the orchestrator resolves them once, under the patch, and hands them down.

## 5. Back-compat surface

Callers import `from openprogram.agent.dispatcher import process_user_turn`
(and `TurnRequest`, `TurnResult`, `dispatch_forced_tool_call`,
`trigger_compaction`, `approval_registry`, `_wrap_agentic_runtime_block` for
`process_runner.py`). The package `__init__.py` re-exports this full surface,
so no caller changes. Lazy imports (heavy provider / context chains) stay
inside function bodies in every module — importing the package at webui
startup pulls in none of them.

## 6. Verification

`py_compile` on the package, an import smoke check
(`dispatcher.process_user_turn`, `dispatcher.dispatch_forced_tool_call`), the
dispatcher-touching unit tests green with no behavior-assertion changes, and a
grep confirming the §3 head invariant (`set_head` / head-originating
`update_session(head_id=...)` calls appear only in `turn_writer.py` and
`forced_tool.py` within the package; `finalize.py` only stamps the value
`TurnWriter.head_for_finalize` hands it).

## 7. Non-goals

The split does not change the turn lifecycle, the error taxonomy, the
persistence schema, or any event payload. It introduces no async where the
path is blocking.

Owner: agent/runtime.
