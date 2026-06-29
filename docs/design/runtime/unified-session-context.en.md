# Unified session context creation

## Problem (why this doc exists)

Several of OpenProgram's core capabilities — **automatically injecting a function's docstring into the prompt**, **DAG persistence**,
**ask_user tracking**, and **called_by attribution for nested calls** — all depend on a per-turn
**session context**: a set of ContextVars (`_store` / `_current_turn_id` /
`_current_runtime` / `_call_id`), built from a `SessionDB` + `session_id`.

This context is **only set up by the dispatcher (the web / chat entry point)**, and its setup logic is **entirely inlined inside
`process_user_turn()`, never factored into a reusable function**. The consequences:

- **The command-line direct entry point (research harness `main.py`) never builds a session at all** → `_store=None`
  → all of the above capabilities silently fail. Concretely: the
  docstrings of `design_experiments` / `write_section` contain detailed instructions, but on a command-line run the model never receives them → the agent degrades into a "What would you like to
  do" conversation. **The same function works on the web but not on the command line.**
- `process_runner` (the subprocess) and the tests each **hand-copied** the dispatcher's set/reset
  logic (`process_runner.py:149-174`, `tests/.../test_runtime_exec_dag.py`) —
  proof that this area is missing a shared unit.

The core tension: **once OpenProgram is installed, behavior should be identical whether you run from the command line or the web**. Today it is not, and
the root cause is that "building a session" has no unified entry point — whoever remembers to do it does it, and the CLI forgot.

## List of capabilities that silently fail in standalone mode (`_store=None`)

| Capability | Failure point | Symptom |
|---|---|---|
| docstring into prompt | `render.py:101` (never reached) | function instructions never reach the model, degrades into conversation |
| DAG persistence | `runtime` append node is a no-op | the run is not recorded in session history |
| rendering history from the DAG | `_render_history_messages` returns None | each exec can't see the preceding steps |
| ask_user tracking | placeholder/finish nodes are not written | questions are not recorded in history |
| nested called_by | `_call_id=None` | node attribution is lost |

The implementation pattern is uniformly "`store=_store.get(); if store is None: return`" — it doesn't crash, but it does nothing.

## What the session context is made of (investigation conclusion)

```
SessionDB (default_db / SessionStore)           # persistence backend (per-session git repo)
   └─ ~/.openprogram/sessions/<session_id>/      # history/ + context/ + meta.json
GraphStoreShim(db, session_id)                   # thin wrapper: append/update/load DAG nodes
ContextVars (per turn, must be set+reset in pairs):
   _store           = GraphStoreShim(...)         # deep code reads it to write the DAG / render docs
   _current_turn_id = assistant_msg_id            # which message a file backup is attributed to
   _current_runtime = create_runtime()            # used for @agentic_function auto-injection
   _call_id         = (set by the @agentic_function wrapper)  # node called_by attribution
```

work-dir and session are **two independent persistence layers**: work-dir stores research output files
(literature review/ ideas/ paper/), session stores the conversation DAG. They are complementary, not in conflict — wiring up the session
does not touch work-dir.

## Design: extract a unified session context manager

Factor the dispatcher's inlined logic into **a single reusable unit** that every entry point (dispatcher / CLI /
research / process_runner / tests) uses. Use a context manager (which guarantees set/reset are
paired and no token leaks):

```python
# openprogram/store/session/context.py  (new file)

@contextmanager
def session_context(
    session_id: str | None = None,
    *,
    agent_id: str = "main",
    turn_id: str | None = None,
    runtime=None,            # reuse an existing runtime if present, otherwise build on demand
    create_runtime_if_none: bool = True,
):
    """Install the per-turn session ContextVars and tear them down on exit.

    The single place that wires _store / _current_turn_id / _current_runtime
    so docstring-into-prompt, DAG persistence, ask_user tracking all work the
    SAME whether the caller is the web dispatcher, the CLI, research harness,
    a subprocess, or a test. Standalone callers that pass nothing still get a
    real (ad-hoc) session instead of silently degrading.
    """
    db = default_db()
    sid = session_id or ("adhoc_" + _short_uuid())
    if db.get_session(sid) is None:
        db.create_session(sid, agent_id, source="cli")
    rt = runtime
    if rt is None and create_runtime_if_none:
        rt = create_runtime()           # fallback: if no provider then rt=None, store is still installed
    tid = turn_id or ("turn_" + _short_uuid())

    tokens = []
    tokens.append(("_store",  _store.set(GraphStoreShim(db, sid))))
    tokens.append(("_turn",   _current_turn_id.set(tid)))
    if rt is not None:
        tokens.append(("_rt", _current_runtime.set(rt)))
    try:
        yield SessionHandle(db=db, session_id=sid, runtime=rt, turn_id=tid)
    finally:
        for _name, tok in reversed(tokens):
            tok.var.reset(tok)   # the implementation holds a reference to the var so it can reset
```

### The session boundary: determined by how session_id is passed, not by call count

Within a chat the session boundary is naturally clear (start chatting → end). Command-line / client calls are "stateless one-shot
function calls" with a fuzzy boundary — **"one call = one session" is wrong**, otherwise "run a task once, then keep
optimizing it" would split into two unrelated sessions and break the history; conversely, two unrelated things should not be crammed into one session.

**Who decides "continue the previous one" vs. "start a new one" is expressed by the caller explicitly passing `session_id`** — this is precisely
the mechanism OpenProgram already has (`openprogram --resume <id>`, the webui frontend passing the id back). The
unified session just makes every entry point follow the same rules; it invents nothing new:

| Caller intent | What to pass | `session_context` behavior |
|---|---|---|
| Run a new task | don't pass session_id | create a new one, **return/print the id** to the caller |
| Keep chatting / optimizing on the same task (2nd, 3rd call) | pass the id returned last time | **reuse**: keep writing to the same session, history continues |
| Start something entirely unrelated | don't pass (or pass a different id) | an independent new session |

The key decision (in response to "you can't store one session per call"): **`session_context` does not create a new adhoc session by default.** The rule is:

- `session_id` passed → reuse (write to it if it exists; create it under that id if not).
- nothing passed → only then create a new one, and **the id must be exposed** (the CLI prints "session: research_xxx
  (--resume to continue)"; a code client returns `session_id` in its return value).

This way continuity is expressed by **passing the id**, independent of how many times you call:

```python
# ideal usage from a code client
r1 = run_research("survey agent reliability")          # session_id=None → create new
print(r1.session_id)                                   # -> research_ab12cd
r2 = run_research("now turn it into a paper",
                  session_id=r1.session_id)            # continue the same session, history carries over
r3 = run_research("unrelated: GUI agent benchmark")    # not passed → independent new session
```

The CLI equivalent: the first run without `--session` → prints a new id; `--session <id>` (or reuse `--resume`) continues.

### Ending a session

A session **does not need to be explicitly "ended"** — it is an append-only git history; you stop writing when done, and next time you come back with the same
id you keep writing. There is no "close" action to perform (exiting `session_context` only resets the ContextVars; it does not delete the
session). "Ending" just means the caller stops passing that id. When cleanup is needed it is handled by session management (the existing
session list / delete), not by the responsibility of each individual call.

### How each entry point uses it

- **dispatcher**: replace the existing inlined set/reset with `with session_context(req.session_id, ...)`.
  Behavior is unchanged (it already builds a session); this is just deduplication.
- **research harness `main.py`**: wrap a layer around the call to `research_agent` —
  ```python
  with session_context(session_id="research_" + uuid, runtime=rt) as h:
      result = research_agent(task=task, runtime=h.runtime, ...)
  ```
  This layer makes the docstring mechanism take effect on the command line too, **without changing any stage function and without moving instructions into
  content**. This is the right way to fix the degradation in the second half of a research run.
- **process_runner / tests**: use the same manager to replace their hand-copied set/reset.

### Implementation order (each step independently verifiable)

| Step | What to do | Verification |
|---|---|---|
| S1 | Add the `session_context` manager (extract dispatcher logic, behavior-equivalent) | unit test: after entering, `_store/_current_turn_id/_current_runtime` are non-None; after exiting they are reset |
| S2 | research `main.py` wraps research_agent with it | run a function with a detailed docstring from the command line, capture the prompt, confirm the docstring went in (empirically confirmed doc=True after installing _store) |
| S3 | dispatcher switches to it (deduplication, behavior unchanged) | all existing dispatcher tests pass |
| S4 | converge process_runner / tests onto it | subprocess DAG tracking and test fixtures still work |

S1+S2 alone fix the research command line (your current pain point); S3+S4 are the deduplication wrap-up.

## Revert the previous "move instructions into content" workaround

To let the second half of research run, the instructions for `design_experiments` / `write_section` were previously moved out of the
docstring and into `content` (part of commit 09dc750). That was a temporary workaround for the missing session,
and it violates the design intent of "instructions in the docstring, function body clean". Once S2 lands, this part should be **reverted** so the docstring
carries the instructions again. (The `write_paper` orchestrator is a genuinely new capability; keep it.)

## In one sentence

Nothing is missing as a component — **"building a session" simply has no unified entry point; only the dispatcher does it, the CLI does not**.
Unify it into a single `session_context` manager that every entry point goes through, and the command line will match the web.
