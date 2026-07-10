# Runtime

Agent execution runtime — the run loop, worktrees, async tasks, streaming/resume, the DAG model, and revert layers.

- [`session-dag.md`](dag/session-dag.md) — **authoritative**: the execution-record data model (one single graph / three node roles user·llm·code / caller+predecessor edges / render_context retrieval) + the two-path merge implementation design
- [`dag/dag-rendering.md`](dag/dag-rendering.md) — **authoritative rendering spec**: layout / edges / legend / default visibility, 12 scenarios
- [`dag/branch-collaboration.md`](dag/branch-collaboration.md) — branch collaboration (communication / dispatch / merge) design and implementation steps
- [`agent-call-flow.md`](execution/agent-call-flow.md) — the call-flow skeleton (turn / loop, orthogonal to the node model)
- [`agent-worktree.md`](execution/agent-worktree.md)
- [`async-task-lifecycle.md`](execution/async-task-lifecycle.md)
- [`dispatcher-split.md`](execution/dispatcher-split.md) — break `agent/dispatcher.py` into a responsibility-scoped package (no-1000-line rule)
- [`multi-agent-revert-todo.md`](operations/multi-agent-revert-todo.md)
- [`file-management.md`](operations/file-management.md)
- [`runtime.md`](runtime.md)
- [`session/`](session/) — Session 子系统：数据模型、存储、命名、列举、生命周期、广播
- [`streaming-resume.md`](operations/streaming-resume.md)
- [`user-input-requests.md`](operations/user-input-requests.md) — pause a running function to ask the user (`runtime.ask`/`confirm`), question registry + WS/REST protocol + subprocess bridge
