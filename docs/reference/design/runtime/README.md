# Runtime

Agent execution runtime — the run loop, worktrees, async tasks, streaming/resume, the DAG model, and revert layers.

- [`dag/overview.md`](dag/overview.md) — **authoritative**: the execution-record data model (one single graph / three node roles user·llm·code / caller+predecessor edges / render_context retrieval)
- [`dag/rendering.md`](dag/rendering.md) — **authoritative rendering spec**: layout / edges / legend / default visibility, 12 scenarios
- [`dag/branch-collaboration.md`](dag/branch-collaboration.md) — branch collaboration (communication / dispatch / merge) design and implementation steps
- [`execution/agent-call-flow.md`](execution/agent-call-flow.md) — the call-flow skeleton (turn / loop, orthogonal to the node model)
- [`execution/agent-worktree.md`](execution/agent-worktree.md)
- [`execution/async-task-lifecycle.md`](execution/async-task-lifecycle.md)
- [`execution/dispatcher-split.md`](execution/dispatcher-split.md) — break `agent/dispatcher.py` into a responsibility-scoped package (no-1000-line rule)
- [`operations/multi-agent-revert-todo.md`](operations/multi-agent-revert-todo.md)
- [`operations/file-management.md`](operations/file-management.md)
- [`overview.md`](overview.md)
- [`session/`](session/) — the session subsystem: data model, storage, naming, listing, lifecycle, broadcast
- [`operations/streaming-resume.md`](operations/streaming-resume.md)
- [`operations/user-input-requests.md`](operations/user-input-requests.md) — pause a running function to ask the user (`runtime.ask`/`confirm`), question registry + WS/REST protocol + subprocess bridge
- [`sandbox.md`](sandbox.md) — the process isolation layer: the boundary on both platforms, where the switch is lost, which execution points it covers, how eight reference harnesses compare, repair order ([rendered](sandbox-architecture.html))
