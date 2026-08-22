# 运行时

Agent 执行运行时 —— 运行循环、worktree、异步任务、流式传输/恢复、DAG 模型，以及回退层。

- [`dag/overview.md`](dag/overview.md) — **权威**:历史记录数据模型(一整张图 / 三种节点 user·llm·code / caller+predecessor 边 / render_context 上下文检索)
- [`dag/rendering.md`](dag/rendering.md) — **权威渲染规范**：布局/连线/图例/默认可见性，12 场景
- [`dag/branch-collaboration.md`](dag/branch-collaboration.md) — 分支协作（通信 / 派活 / 合并）设计与实现步骤
- [`execution/agent-call-flow.md`](execution/agent-call-flow.md) — 调用流程骨架(turn / loop,跟节点模型正交)
- [`execution/agent-worktree.md`](execution/agent-worktree.md)
- [`execution/async-job-lifecycle.md`](execution/async-job-lifecycle.md)
- [`execution/dispatcher-split.md`](execution/dispatcher-split.md) — 将 `agent/dispatcher.py` 拆分为按职责划分的包(遵循「单文件不超过 1000 行」规则)
- [`operations/multi-agent-revert-todo.md`](operations/multi-agent-revert-todo.md)
- [`operations/file-management.md`](operations/file-management.md)
- [`overview.md`](overview.md)
- [`session/`](session/) — Session 子系统：数据模型、存储、命名、列举、生命周期、广播
- [`operations/streaming-resume.md`](operations/streaming-resume.md)
- [`operations/user-input-requests.md`](operations/user-input-requests.md) — 暂停正在运行的函数以向用户提问(`runtime.ask`/`confirm`),问题注册表 + WS/REST 协议 + 子进程桥接
- [`agent-collaboration.md`](agent-collaboration.md) — **权威**：agent 协作收敛成一个分支间通信原语 —— 四个域、工具面、三个预算（[工具面图示](agent-collab-architecture.html)、[八家参考实现对照](agent-collab-comparison.html)）
- [`sandbox-architecture.html`](sandbox-architecture.html) — 唯一权威执行安全设计：Authority 权限档、Permission 模式与审批、沙箱执法、框架对照和实现证据。[`permission-model.md`](permission-model.md) 与 [`sandbox.md`](sandbox.md) 只保留为稳定旧链接入口。
