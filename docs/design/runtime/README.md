# Runtime

Agent execution runtime — the run loop, worktrees, async tasks, streaming/resume, the DAG model, and revert layers.

- [`execution-graph.md`](execution-graph.md) — **权威**:历史记录数据模型(一整张图 / 三种节点 user·llm·code / called_by 边 / compute_reads 上下文检索)+ 两套调用路径合并的实现设计
- [`agent-call-flow.md`](agent-call-flow.md) — 调用流程骨架(turn / loop,跟节点模型正交)
- [`agent-worktree.md`](agent-worktree.md)
- [`async-task-lifecycle.md`](async-task-lifecycle.md)
- [`dag-node-model.md`](dag-node-model.md) — 旧节点模型;数据语义已由 `execution-graph.md` 取代,本文仅保留 graph_layout(lane/depth)相关参考
- [`dispatcher-split.md`](dispatcher-split.md) — break `agent/dispatcher.py` into a responsibility-scoped package (no-1000-line rule)
- [`multi-agent-revert-todo.md`](multi-agent-revert-todo.md)
- [`revert-layers.md`](revert-layers.md)
- [`runtime.md`](runtime.md)
- [`session/`](session/) — Session 子系统：数据模型、存储、命名、列举、生命周期、广播
- [`streaming-resume.md`](streaming-resume.md)
- [`user-input-requests.md`](user-input-requests.md) — pause a running function to ask the user (`runtime.ask`/`confirm`), question registry + WS/REST protocol + subprocess bridge
- [`user-input-requests-references.md`](user-input-requests-references.md)
