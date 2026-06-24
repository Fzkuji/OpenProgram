# Runtime

Agent execution runtime — the run loop, worktrees, async tasks, streaming/resume, the DAG model, and revert layers.

- [`session-dag.md`](session-dag.md) — **权威**:历史记录数据模型(一整张图 / 三种节点 user·llm·code / called_by 边 / render_context 上下文检索)+ 两套调用路径合并的实现设计
- [`agent-call-flow.md`](agent-call-flow.md) — 调用流程骨架(turn / loop,跟节点模型正交)
- [`agent-worktree.md`](agent-worktree.md)
- [`async-task-lifecycle.md`](async-task-lifecycle.md)
- [`dispatcher-split.md`](dispatcher-split.md) — break `agent/dispatcher.py` into a responsibility-scoped package (no-1000-line rule)
- [`multi-agent-revert-todo.md`](multi-agent-revert-todo.md)
- [`file-management.md`](file-management.md)
- [`runtime.md`](runtime.md)
- [`session/`](session/) — Session 子系统：数据模型、存储、命名、列举、生命周期、广播
- [`streaming-resume.md`](streaming-resume.md)
- [`user-input-requests.md`](user-input-requests.md) — pause a running function to ask the user (`runtime.ask`/`confirm`), question registry + WS/REST protocol + subprocess bridge
- [`user-input-requests-references.md`](user-input-requests-references.md)
