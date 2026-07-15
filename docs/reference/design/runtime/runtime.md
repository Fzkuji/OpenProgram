# Runtime — Design Rationale

For API usage, see [`../api/runtime.md`](../../api/runtime.md). This document only explains **why**
Runtime is shaped the way it is, which alternatives were rejected, and which trade-offs were made deliberately.

## 1 runtime = 1 session

Each `Runtime` instance is bound to one provider session, with a 1:1 lifecycle:

```
create_runtime()      = open session
runtime.exec()        = send one request within the session
runtime.close()       = close session
```

There is no `reset()` / `new_session()`; to get a new session, call `create_runtime()` again.

**Why not let one Runtime reuse multiple sessions?**

- For CLI providers (Claude Code / Codex / Gemini CLI), session state lives in the subprocess.
  Reusing would require hanging mutable state like "which session id is current" on the Runtime, introducing concurrency races.
- API providers are themselves stateless; doing "multiple sessions" is just an upper-layer dict, equivalent to "multiple Runtimes" —
  no new capability.
- ContextVar auto-injection (next item) relies on the simple model of "the runtime is bound to the current function tree";
  multiple sessions would complicate the injection semantics.

The cost: a user who wants to run two independent conversations has to manage two runtime objects. This is acceptable, because such scenarios are rare.

## ContextVar auto-injects the runtime

The `@agentic_function` decorator reads the `_current_runtime` ContextVar; if the current function
was not passed a `runtime=` argument, it uses that. If the entry function has none either, it automatically calls `create_runtime()`.

**Why not have functions declare the runtime explicitly?**

With explicit declaration, every agentic function would have to add `runtime: Runtime` to its signature,
and every nested call would have to explicitly pass `runtime=runtime` through — pure plumbing
boilerplate unrelated to the function logic. ContextVar hides it away: a child function naturally inherits its parent's
runtime, one is created automatically at the entry point, and it is closed automatically at exit.

**Why not use a module-level singleton?**

A singleton is shared across threads / coroutines, so two concurrent agents would step on each other's session state.
ContextVar is isolated per thread + coroutine, making it naturally concurrency-safe.

## Session-provider and API-provider share one abstraction

Whether the backend is the Claude Code CLI (has a session) or the Anthropic API (no session),
`@agentic_function` authors see the same interface, `runtime.exec(content=[...])`.
The framework uses the `has_session` attribute to distinguish the two provider classes and take different internal paths:

| | session provider (CLI) | API provider |
|---|---|---|
| Conversation memory | managed by the subprocess itself | none; each exec is independent |
| Context injection | skips DAG render, sends only the docstring + the current content | assembles history from the DAG via `render_context` + `render_dag_messages` |
| `render_range.subcalls` | no effect (the session remembers the conversation itself) | takes effect (used to bound the window of injected history) |

**Why not split into two separate Runtime classes?**

When writing `gui_agent`, the author should not care which kind of provider the backend is. Forcing a split would require two
implementations per function, violating the layering of "functions describe the task, providers describe the execution channel." The cost of the shared abstraction is
the single `has_session` conditional branch — worth it.

## Retry lives in the runtime layer, not the provider layer

`exec()` / `async_exec()` have a built-in `max_retries`, defaulting to 2, so any provider
gets retries.

**Why not add retry to each provider class separately?**

- The retry policy is consistent across all providers (network timeout / rate limit / 5xx); no need to duplicate it
- Failure reports have a uniform format (`Attempt N: ErrorType: msg`), which aids debugging
- Programming errors like `TypeError` / `NotImplementedError` are uniformly not retried (only the
  runtime layer knows "this is a provider implementation bug, retrying won't help")

The provider layer only cares about "send the request out, get the reply back." Cross-cutting concerns like retry / throttling / caching
all live in the runtime.

## DAG writes: entering and exiting a function both write a code node, exec writes an llm node

```
enter @agentic_function       → write a code node (status=running)
                              → set the _call_id ContextVar to point at this node
runtime.exec() in the body    → write an llm node under the current _call_id
                              → the node's called_by = _call_id
exit the function (return / except) → backfill the same code node's output / status
```

When `expose="hidden"`, the code node write is skipped (but a phantom `_call_id`
is still set, so that LLM calls inside the function body have a frame to reference).

**Why not write a single completed-state node only on exit?**

- While the function is still running, the webui visualizer needs to immediately show "it's running" (display a spinner)
- On an exceptional exit there must also be a node present (so the error information can be recorded)

Writing twice (entry + exit backfill) suits real-time observability better than writing once (on completion).

## Related implementation files

- `openprogram/agentic_programming/runtime.py` — Runtime base class, `exec` / `_call` protocol, retry loop
- `openprogram/agentic_programming/function.py` — decorator / `_inject_runtime` / `_call_id` / `_current_runtime` ContextVar
- `openprogram/providers/__init__.py` — `detect_provider` / `create_runtime` auto-detection
- `openprogram/providers/<vendor>/runtime.py` — each provider's `_call` implementation
- `openprogram/context/nodes.py` `render_context` — DAG → reads computation (the actual semantics of `render_range`)
- `openprogram/context/render.py` `render_dag_messages` — reads → provider messages conversion
