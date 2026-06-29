# 运行时

Agent 执行运行时 —— 运行循环、worktree、异步任务、流式传输/恢复、DAG 模型，以及回退层。

- [`session-dag.md`](dag/session-dag.md) — **权威**:历史记录数据模型(一整张图 / 三种节点 user·llm·code / called_by 边 / render_context 上下文检索)+ 两套调用路径合并的实现设计
- [`agent-call-flow.md`](execution/agent-call-flow.md) — 调用流程骨架(turn / loop,跟节点模型正交)
- [`agent-worktree.md`](execution/agent-worktree.md)
- [`async-task-lifecycle.md`](execution/async-task-lifecycle.md)
- [`dispatcher-split.md`](execution/dispatcher-split.md) — 将 `agent/dispatcher.py` 拆分为按职责划分的包(遵循「单文件不超过 1000 行」规则)
- [`multi-agent-revert-todo.md`](operations/multi-agent-revert-todo.md)
- [`file-management.md`](operations/file-management.md)
- [`runtime.md`](runtime.md)
- [`session/`](session/) — Session 子系统：数据模型、存储、命名、列举、生命周期、广播
- [`streaming-resume.md`](operations/streaming-resume.md)
- [`user-input-requests.md`](operations/user-input-requests.md) — 暂停正在运行的函数以向用户提问(`runtime.ask`/`confirm`),问题注册表 + WS/REST 协议 + 子进程桥接
