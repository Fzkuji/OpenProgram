# Session Context

`session_context` is the unified per-turn context manager. It populates the ContextVars (`_store` / `_current_turn_id` / `_current_runtime` / `_call_id`) so that capabilities such as feeding docstrings into the prompt, DAG persistence, and ask_user tracking take effect across all entry points.

## Interface

```python
@contextmanager
def session_context(
    session_id: str | None = None,
    *,
    agent_id: str = "main",
    turn_id: str | None = None,
    runtime=None,
    create_runtime_if_none: bool = True,
):
    db = default_db()
    sid = session_id or ("adhoc_" + _short_uuid())
    if db.get_session(sid) is None:
        db.create_session(sid, agent_id, source="cli")
    rt = runtime
    if rt is None and create_runtime_if_none:
        rt = create_runtime()
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
            tok.var.reset(tok)
```

When the session does not exist, `session_context` calls `create_session` — this is one of the creation entry points described in [operations.md](operations.md).

## Session boundaries

Boundaries are determined by how `session_id` is passed, not by the number of calls.

| Caller intent | What to pass | Behavior |
|---|---|---|
| Run a new task | Do not pass session_id | Creates a new one and returns/prints the id to the caller |
| Continue the same task (2nd, 3rd call) | Pass the id returned last time | Reuses it, continuing the history |
| Start something unrelated | Do not pass one (or pass a different id) | A separate, new session |

CLI equivalent: the first run without `--session` prints a new id; `--session <id>` continues.

## Ending a session

A session does not need to be explicitly "ended" — it is an append-only git history. You write until you stop, and the next time you come back with the same id, you keep writing. Exiting `session_context` only resets the ContextVars; it does not delete the session.

## Usage at each entry point

| Entry point | Usage |
|------|------|
| dispatcher | `with session_context(req.session_id, ...)` replaces the existing inline set/reset |
| research harness | `with session_context(session_id="research_" + uuid, runtime=rt)` wraps research_agent |
| process_runner | The same manager replaces the hand-copied set/reset |
| tests | Same as above |
