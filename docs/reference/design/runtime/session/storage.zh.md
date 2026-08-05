# Session 数据模型

## 磁盘布局

```
<state>/sessions/
├── index.json                    # 注册表（所有 session 的摘要缓存）
├── <session_id_1>/
│   ├── meta.json                 # 元数据
│   └── history/                  # 消息 DAG（Git 仓库）
├── <session_id_2>/
│   └── ...
```

## 持久字段（meta.json）

| 字段 | 类型 | 注册表 | 说明 |
|------|------|--------|------|
| `id` | str | 是 | session 唯一标识 |
| `agent_id` | str | 是 | 绑定的 agent |
| `title` | str | 是 | 显示名称 |
| `created_at` | float | 是 | 创建时间戳 |
| `updated_at` | float | 是 | 最后活动时间戳 |
| `project_id` | str? | 否 | 绑定的项目（列举时由 project_map 补充为 `project` 名称） |
| `source` | str? | 是 | 来源："tui" / "web" / "wechat" / ... |
| `channel` | str? | 是 | 渠道类型 |
| `account_id` | str? | 是 | 渠道账号 |
| `peer_display` | str? | 是 | 对方显示名 |
| `peer_id` | str? | 是 | 对方 ID |
| `pinned` | bool | 是 | 置顶 |
| `archived` | bool | 是 | 归档 |
| `group` | str? | 是 | 分组标签 |
| `status` | str | 是 | 生命周期状态（见下方） |
| `unread` | bool | 是 | 未读标记 |
| `_auto_titled` | bool | 否 | 自动命名幂等标记（内部控制，不进注册表、不返回前端） |

"注册表"列标记该字段是否缓存到 `index.json`。`_auto_titled` 和 `project_id` 不进注册表：前者是内部标记，后者在列举时由项目目录映射补充。

## 注册表独有字段

以下字段只在注册表中，不在 meta.json 中：

| 字段 | 说明 |
|------|------|
| `preview` | 最后一条用户消息前 80 字符，由写消息时截取维护 |

## status 枚举

| 值 | 含义 | 前端显示 |
|----|------|----------|
| `idle` | 空闲，无 turn 在执行 | 无指示 |
| `running` | 有 turn 正在执行 | 运行动画 |
| `needs_input` | agent 等待用户输入 | 琥珀点 |
| `done` | 后台任务完成 | 配合 `unread` 显示蓝点 |
| `failed` | turn 执行失败 | 红点 |
| `interrupted` | worker 在 turn 中途死掉 | 无指示（不算 run-active） |

`running` 由 dispatcher 在 turn 开始时写入、结束时清除。worker 中途被杀
（SIGKILL、崩溃）就跑不到清除那一步，会话行会永远停在 `running`，把聊天容器
钉在 `data-run-active="true"` 上，除非手改磁盘状态否则出不来。因此
`reconcile_interrupted_runs()` 在 worker 启动时把仍是 `running` 的行重置为
`interrupted`——新起的 worker 按定义没有任何东西在跑。这一步与同一函数里的 DAG
节点扫描相互独立：worker 若在写 status 和插入 placeholder 之间被杀，就会留下一
个 running 的**行**却没有 running 的**节点**。

## 移动 HEAD：`_set_active_head`

`webui/server.py` 按会话持有一份内存镜像 `_sessions[sid]`，含 `head_id` 与
`messages`，而 `_save_session` 会把两者原样写回 SessionStore。所以只改 store
的 HEAD、不同步镜像的路径不只是"数据过期"——**下一次保存会主动把这次移动撤销。**

`_set_active_head(session_id, head_id)` 是移动 HEAD 的唯一正确入口。它依次完成：
写 SessionStore、把新分支读回镜像的 `head_id` 与 `messages`、清消息缓存。所有会
改动的路径都走它：retry、edit、兄弟节点 checkout、deepest-leaf 跳转、分支
checkout、删分支、attach、rewind。

有 turn 在运行时（`_is_run_active`），所有移动 HEAD 的操作一律拒绝，返回
`RUN_ACTIVE_ERROR` 并带 `code: "run_active"`。没有这道保护，在飞的回复落地时
predecessor 会指向用户已经离开的分支；删分支更糟——要删的那条尾巴可能正是当前
turn 正在写入的。

## 非持久对象（`_sessions` dict）

agent runtime、WebSocket 连接等无法序列化的对象存在进程内存的 `_sessions` dict 中，按 session id 索引：

| 键 | 类型 | 说明 |
|----|------|------|
| `runtime` | AgentRuntime? | LLM 连接、session state |
| `ws` | WebSocket? | 当前连接的 WebSocket |
| `agent` | Agent? | agent 实例 |

目标：持久字段全部通过 SessionStore 读写，不在 `_sessions` 中冗余。

> **当前状态**：`_sessions` 仍冗余了 title、agent_id、created_at、channel 等持久字段，因为 `_save_session` 从 dict 读取所有字段写 meta.json。`run_active` 已删除（由 status 字段替代）。完全瘦身需要重写 `_save_session` 使其从 SessionStore 读持久字段——留作后续。

## 接口

```python
class SessionStore:
    def create_session(session_id, agent_id, *, title="", source=None, **meta) -> None
    def get_session(session_id) -> dict | None
    def update_session(session_id, **fields) -> None
    def delete_session(session_id) -> None
    def list_sessions(*, limit=100, offset=0, **filters) -> list[dict]
    def get_branch(session_id, head_id=None) -> list[dict]
    def append_message(session_id, msg) -> None
    def latest_user_text(session_id) -> str | None
```

每个方法的完整行为见 [operations.md](operations.md)。
