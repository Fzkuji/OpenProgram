# Session 子系统

Session 是用户与 agent 的一次对话。

- [storage.md](storage.md) — 数据模型：字段定义、状态枚举、磁盘布局、非持久对象、接口签名
- [operations.md](operations.md) — 操作流程：启动、创建、写消息、更新字段、命名、列举、删除、归档
- [name.md](name.md) — LLM 标题生成细节：prompt、参数、后处理
- [context.md](context.md) — session_context manager：统一上下文，所有入口共享
- [comparison.md](comparison.md) — Claude Code / OpenCode / OpenClaw / OpenProgram 对比
