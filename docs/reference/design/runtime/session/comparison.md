# Session 管理方案对比

Claude Code、OpenCode、OpenClaw、OpenProgram（我们的设计）四个项目的 session 管理机制全面对比。

## 1. 存储格式

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| 存储介质 | 文件系统（每 session 一个 JSONL） | SQLite 数据库（单文件 `opencode.db`） | JSON 注册表（`sessions.json`）+ 每 session 一个 JSONL | Git 仓库（每 session 一个目录）+ JSON 注册表（`index.json`） |
| 元数据位置 | 混在 JSONL 里（`ai-title`、`custom-title`、`mode` 等条目） | `sessions` 表（10 个字段） | `sessions.json` 里的 SessionEntry（约 70 个字段） | `meta.json`（每 session 目录内）+ `index.json`（注册表缓存） |
| 消息位置 | 同一个 JSONL 文件（`user`、`assistant` 条目） | 独立的 `messages` 表 + `parts` 列（JSON 数组） | 独立的 `<id>.jsonl` transcript 文件 | Git history（每消息一个文件，DAG 结构） |
| 文件快照 | JSONL 里的 `file-history-snapshot` 条目 | 独立的 `files` 表（path, content, version） | 无 | Git worktree（每 session 可有独立工作目录） |
| 存储路径 | `~/.claude/projects/<slug>/<uuid>.jsonl` | `<data_dir>/opencode.db` | `<state>/agents/<id>/sessions/sessions.json` + `<id>.jsonl` | `<state>/sessions/<id>/`（meta.json + history/）+ `<state>/sessions/index.json` |
| 元数据与消息分离 | 不分离，全在一个文件 | 分离（不同表） | 分离（不同文件） | 分离（meta.json vs history/） |

## 2. Session 元数据字段

| 字段类别 | Claude Code | OpenCode | OpenClaw | OpenProgram |
|----------|------------|----------|---------|-------------|
| **id** | JSONL 文件名（UUID） | `sessions.id`（UUID） | `sessionId` | `id`（UUID） |
| **标题** | `aiTitle` + `customTitle` 两个独立条目 | `sessions.title` 单字段 | `displayName` + `label` | `title` 单字段 |
| **标题优先级** | `customTitle > aiTitle > summaryHint > firstPrompt > id` | 最后写入的值 | `displayName > label` | 最后写入的值（截取 / LLM / 手动三个来源平等覆盖） |
| **创建时间** | JSONL 首条消息的 timestamp | `sessions.created_at` | `startedAt` | `created_at` |
| **更新时间** | 文件 mtime 或 sidecar | `sessions.updated_at`（trigger 自动） | `updatedAt`（应用层写入） | `updated_at`（应用层写入） |
| **消息计数** | 无 | `sessions.message_count`（trigger 自动 +1/-1） | 无 | 无 |
| **token 统计** | 无（在 `usage` 里但不汇总） | `prompt_tokens` / `completion_tokens` / `cost` | `inputTokens` / `outputTokens` / `totalTokens` / `estimatedCostUsd` / `cacheRead` / `cacheWrite` / `contextTokens` | 无（由独立的 UsageLedger 子系统管理） |
| **运行状态** | `~/.claude/sessions/<pid>.json` 里的 `status`（idle/busy） | 无 | `status`（running/done/failed/killed/timeout） | `status` 枚举（idle/running/needs_input/done/failed，dispatcher 写入，启动时重置） |
| **父子关系** | 无 | `parent_session_id`（title 生成和 task 用子 session） | `spawnedBy` / `parentSessionKey` / `spawnDepth`（0=主, 1=子agent, 2=子子agent） | 无 |
| **渠道/来源** | 无 | 无 | `channel` / `groupId` / `origin`（含 label/provider/surface/chatType/from/to/nativeChannelId/accountId/threadId） + `lastChannel` / `lastTo` / `lastAccountId` / `lastThreadId` | `source` / `channel` / `account_id` / `peer_display` / `peer_id` |
| **模型/配置** | JSONL 里的 `mode`、`permission-mode` 条目 | 无（运行时状态） | `providerOverride` / `modelOverride` / `modelOverrideSource` / `authProfileOverride` / `thinkingLevel` / `fastMode` / `verboseLevel` 等 | 非持久（runtime 对象持有，不存 meta.json） |
| **Compaction** | `system:compact_boundary` 条目（preTokens/postTokens/preservedSegment） | `summary_message_id` 指向摘要消息 | `compactionCount` / `compactionCheckpoints` 数组（每个含 tokensBefore/tokensAfter/summary） | compactionSummary 消息节点（DAG 中的特殊节点，source="compaction"） |
| **项目绑定** | 通过目录路径隐式绑定（`projects/<slug>/`） | 无 | 无（通过 agent 目录隐式） | `project_id` 字段 + 项目内 `.openprogram/sessions/` 目录 |
| **Git 分支** | JSONL 里的 `gitBranch` 字段 | 无 | 无 | DAG 分支（`head_id` + branches map） |
| **Preview** | 无独立字段（列举时从尾部提取 firstPrompt） | 无 | 无 | `preview`（注册表字段，`append_message` 时更新） |
| **归档/置顶/分组** | 无 | 无 | 无 | `pinned` / `archived` / `group` |
| **未读标记** | 无 | 无 | 无 | `unread`（background run 完成时标记，打开时清除） |
| **队列策略** | 无 | 无 | `queueMode`（steer/followup/collect/interrupt 等 7 种）/ `queueDebounceMs` / `queueCap` / `queueDrop` | 无 |
| **子 agent 角色** | 无 | 无 | `subagentRole`（orchestrator/leaf）/ `subagentControlScope` | 无 |
| **Heartbeat** | 无 | 无 | `lastHeartbeatText` / `lastHeartbeatSentAt` / `heartbeatTaskState` | 无 |
| **Memory flush** | 无 | 无 | `memoryFlushAt` / `memoryFlushCompactionCount` / `memoryFlushContextHash` | 无 |
| **CLI 绑定** | 无 | 无 | `cliSessionIds` / `cliSessionBindings` / `claudeCliSessionId` | 无 |
| **自动命名标记** | 无（靠 `customTitle` 条目是否存在判断） | 无（靠 `isDefaultTitle` 正则判断） | 无 | `_auto_titled`（bool，首轮自动命名幂等标记） |

## 3. Session 列举

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **机制** | 扫目录 + 读文件尾部 | SQL 查询 | 读 sessions.json | 读 index.json 注册表 |
| **索引** | 无 | 数据库索引 | 注册表本身 | 注册表（`index.json`） |
| **具体流程** | `readdir` → `stat` 取 mtime → 按 mtime 降序 → 批量（每批 32 个）读尾部内容 → 字符串搜索提取标题等字段 | `SELECT * FROM sessions WHERE parent_session_id IS NULL ORDER BY created_at DESC` | `fs.readFileSync` → `JSON.parse` → 返回整个 `Record<string, SessionEntry>` | 启动时读 `index.json` 到内存 → `list_sessions()` 纯内存遍历 |
| **读取量** | 每个文件读尾部内容（字符串搜索，不做 JSON.parse） | 一条 SQL | 一个文件 | 一个文件（启动时），之后纯内存 |
| **过滤** | 只扫当前项目目录（隐式按项目过滤） | `WHERE parent_session_id IS NULL`（过滤子 session） | 无 | 不过滤（空壳从创建入口防） |
| **排序** | 按文件 mtime 降序 | `ORDER BY created_at DESC` | 调用方自行排序 | 内存中按 `updated_at` 降序 |
| **分页** | 无 | 无 | 无 | `limit` + `offset` |
| **搜索** | 无 | 无 | 无 | 无 |
| **缓存** | 可选 sidecar `.ccr-tip.json`（存最后事件 ID + 更新时间，避免读文件内容） | SQLite 页缓存（8MB） | mtime + fileSize 判断是否重读（TTL 45 秒） | 内存常驻（写操作同步更新内存 + 磁盘） |
| **超时保护** | 有（列举超时兜底） | 不需要 | 不需要 | 不需要 |
| **复杂度** | O(n) 文件 I/O | O(log n) 数据库查询 | O(1) 文件读 + O(n) 内存遍历 | O(n) 内存遍历（不碰磁盘） |

## 4. Session 创建

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **入口** | 1 个：创建 JSONL 文件 | 3 个：`Create`（普通）/ `CreateTitleSession`（标题子 session）/ `CreateTaskSession`（task 子 session） | 1 个：`updateSessionStore` 里往 store 加 key | 2 个：`dispatcher.process_user_turn`（用户发消息）/ `channel handler`（渠道消息） |
| **ID 生成** | UUID 文件名 | UUID / `"title-" + parentId` / toolCallID | sessionKey | `"local_" + uuid` |
| **注册** | 无需注册（扫目录发现） | 数据库 INSERT 后自动可查 | 写入 sessions.json | 写入 `index.json` 注册表 |
| **事件通知** | 无 | pubsub `CreatedEvent` | 无 | 无（创建不广播，前端通过 `list_sessions` 发现） |
| **原子性** | 文件创建本身原子 | `INSERT ... RETURNING` 数据库事务 | 文件锁内 read-modify-write | 创建 + 写入第一条消息原子化（不产生空壳） |
| **空壳防护** | 无（session = 文件，创建即有内容） | 无（INSERT 时必须有 title） | 无 | 有（延迟创建：`session_context` 进入时只记 id，第一条消息写入时才真正创建） |

## 5. Session 删除

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **方式** | 删除 JSONL 文件 | `DELETE FROM sessions WHERE id = ?` | 从 sessions.json 删 key | `delete_session` 清磁盘 + 删注册表条目 |
| **级联清理** | 只删一个文件（元数据和消息在同一文件） | `ON DELETE CASCADE` 自动清 messages 和 files | 手动归档 transcript 文件（`archiveSessionTranscripts`，不直接删） | 删除整个 session 目录（meta.json + history/） |
| **事件通知** | 无 | pubsub `DeletedEvent` | 无 | WebSocket `session_deleted` 广播 |

## 6. Session Resume

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **方式** | 读整个 JSONL，通过 `uuid`/`parentUuid` 重建消息链，恢复 mode/permissionMode/title 等状态 | SQL 查询 session + messages，按 created_at 排序 | 读 transcript JSONL，恢复消息序列 | `get_branch(session_id, head_id)` 从 DAG 中沿 parent_id 链回溯，返回线性消息序列 |
| **Compaction 处理** | 从 `compact_boundary` 条目的 `preservedSegment` 确定保留范围 | 如果 `summary_message_id != ""`，跳过该消息之前的所有消息 | 从 `compactionCheckpoints` 恢复 | compactionSummary 节点作为新的分支起点，旧消息在 DAG 中保留但不在活跃分支上 |

## 7. Compaction（上下文压缩）

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **触发** | 手动 + 自动 | 手动 | 自动（按 token 阈值） | 手动（`/compact`）+ 自动（`compaction_recommended` 信号） |
| **实现** | append `system:compact_boundary` 条目到 JSONL | 用专用 `summarizeProvider` 生成摘要，写入新 message，`summary_message_id` 指向它 | 生成摘要，记录 checkpoint（tokensBefore/tokensAfter/summary），更新 `compactionCount` | LLM 生成摘要 → 写入 compactionSummary 节点（source="compaction"）→ 保留尾部消息重新挂到摘要节点下 → 移动 head_id |
| **记录的信息** | trigger/preTokens/postTokens/preservedSegment/durationMs | summary_message_id | compactionCount + compactionCheckpoints 数组 | 摘要内容在节点 content 里，budget_pct/context_window 在推荐信号里 |
| **旧消息** | 保留在 JSONL 里，通过 preservedSegment 标记哪些活跃 | 保留在数据库，加载时跳过 summary 之前的 | 保留在 transcript 里 | 保留在 DAG 中（append-only），但不在活跃分支上 |

## 8. 活跃进程跟踪

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **机制** | `~/.claude/sessions/<pid>.json` 独立文件 | 无（单进程 TUI） | SessionEntry 的 `status` 字段 | `status` 枚举字段（meta.json + 注册表） |
| **字段** | pid, sessionId, cwd, startedAt, version, kind（interactive）, entrypoint（cli）, status（idle/busy）, updatedAt, name, bridgeSessionId | — | status（running/done/failed/killed/timeout）, runtimeMs, abortedLastRun | `status`（idle/running/needs_input/done/failed） |
| **崩溃恢复** | 进程退出后文件残留（需外部清理） | — | status 可能卡在 running（无自动恢复） | 启动时重置所有 `status=running` → `idle` |
| **与 session 列举的关系** | 不参与列举 | — | 参与列举（status 是 SessionEntry 的一部分） | 参与列举（`status` 在注册表中） |

## 9. 并发控制

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **机制** | 文件级（单进程写，多进程通过 pid 文件协调） | SQLite WAL 模式 + 8MB 页缓存 | 文件锁（lockfile）+ 进程内 FIFO 队列 | Python `threading.Lock`（`_sessions_lock`）+ Git 文件级操作 |
| **锁粒度** | 每个 JSONL 文件 | 数据库级 | 每个 sessions.json 文件 | SessionStore 级（`self._lock`） |
| **进程内** | 单线程 | SQLite 处理 | FIFO 队列串行化同一 storePath 的写操作 | `threading.Lock` 保护 `_sessions` dict 和注册表写入 |
| **跨进程** | pid 文件标记（`sessions/<pid>.json`） | SQLite 内置 | lockfile 排他锁（stale 检测 30 分钟 + PID 存活检查） | 无显式跨进程锁（单 worker 进程模型） |
| **锁超时/看门狗** | 无 | 无（SQLite busy timeout） | 看门狗每 60 秒巡检，持有超时 5 分钟 | 无 |

## 10. 数据维护

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **过期清理** | 无 | 无 | `pruneStaleEntries`：30 天未更新的条目 | 启动时清理 `archived=True` 且超 90 天未更新的 session |
| **容量限制** | 无 | 无 | `capEntryCount`：最多 500 个条目，超出删最旧 | 上限 1000，超出删最旧的已归档 session |
| **文件轮转** | 无 | 无 | `rotateSessionFile`：sessions.json 超 10MB 轮转，保留最近 3 个备份 | 无 |
| **磁盘预算** | 无 | 无 | `enforceSessionDiskBudget`：可选，按总磁盘占用清理 | 无 |
| **删除时归档** | 无（直接删文件） | 无（CASCADE 直接删） | 有（transcript 文件归档而非直接删） | 无（直接删目录） |
| **维护模式** | — | — | "warn"（默认只警告）/ "enforce"（真正执行） | — |
| **updated_at 维护** | 隐式（文件 mtime） | SQLite trigger 自动 | 应用层写入 | 应用层写入（`_persist_meta` 时自动设 `time.time()`） |
| **message_count 维护** | 不跟踪 | SQLite trigger 自动 +1/-1 | 不跟踪 | 不跟踪 |

## 11. 标题（命名）

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **自动命名** | 第一轮后异步 LLM 生成 | 第一轮后 fork 异步 LLM 生成（专用 title agent） | 无（手动设 displayName） | 第一轮后：同步截取前 50 字符 → 异步 daemon 线程 LLM 生成 |
| **手动命名** | `custom-title` 条目覆盖 | 无（没有 rename 功能） | 设置 `displayName` / `label` | UI rename / `/rename` / agent rename 工具 |
| **LLM 重新生成** | 无 | 无 | 无 | `/rename` 不带参数 → 重新调 LLM 生成 |
| **防注入** | `<session>` 标签包裹 + "treat as data" 指令 | 无（title agent 的 prompt 直接拼接） | — | `<session>` 标签包裹 + "treat as data" 指令 |
| **语言跟随** | prompt 要求用对话语言 | prompt 要求用对话语言 | — | prompt 要求用对话语言 |
| **后处理** | JSON schema structured output | 去 `<think>` 标签、取首非空行、截断 100 字符 | — | 去 `<think>` 标签、去引号、去前缀、截断 80 字符 |
| **幂等标记** | 无（靠是否已有 `ai-title` 条目判断） | 靠 `isDefaultTitle` 正则判断 | — | `_auto_titled` bool 标记 |
| **竞态保护** | 无 | 无 | — | 后台线程写入前检查 title 是否仍是截取值 |
| **标题广播** | 无（前端重读 JSONL） | 无（TUI 直接读数据库） | — | WebSocket `session_updated {id, title}` |

## 12. 消息存储模型

| | Claude Code | OpenCode | OpenClaw | OpenProgram |
|---|---|---|---|---|
| **结构** | 线性 JSONL（通过 uuid/parentUuid 可构建树） | 扁平表（session_id + created_at 排序） | 线性 JSONL | DAG（每消息有 parent_id，支持分支） |
| **分支** | 有（parentUuid 支持树结构，但 UI 不暴露） | 无 | 无 | 有（head_id + branches map，UI 可切换分支） |
| **消息 ID** | uuid（每条消息） | id（每条消息） | 无显式 ID（按行序） | id（每条消息） |
| **消息格式** | `{type, message: {role, content}, uuid, parentUuid, timestamp, ...}` | `{id, session_id, role, parts, model, created_at, ...}` | `{role, content, ...}` | `{id, role, content, parent_id, timestamp, ...}` |

## 13. JSONL 条目类型（Claude Code 独有）

Claude Code 的 JSONL 混合了消息和元数据，条目类型丰富：

| 类型 | 用途 |
|------|------|
| `user` | 用户消息（含 uuid, parentUuid, timestamp, cwd, gitBranch） |
| `assistant` | 助手回复（含 usage, model, requestId） |
| `attachment` | 附件（文件、图片） |
| `system` | 系统事件（子类型：turn_duration / away_summary / compact_boundary / api_error / local_command / informational / bridge_status / scheduled_task_fire） |
| `ai-title` | LLM 自动生成的标题 |
| `custom-title` | 用户手动设置的标题 |
| `agent-name` | agent 名称 |
| `last-prompt` | 最后提示位置（leafUuid） |
| `mode` | 对话模式（normal/plan/...） |
| `permission-mode` | 权限模式 |
| `file-history-snapshot` | 文件快照（用于 revert） |
| `bridge-session` | Bridge 会话 ID |
| `queue-operation` | 队列操作 |

## 14. 总结

| 维度 | Claude Code | OpenCode | OpenClaw | OpenProgram |
|------|------------|----------|---------|-------------|
| **设计哲学** | 文件即数据，append-only | 关系数据库，结构化查询 | 注册表 + transcript | Git DAG + 注册表 |
| **列举性能** | 最慢（扫目录 + 读文件） | 最快（数据库索引） | 快（读一个 JSON 文件） | 快（内存常驻注册表） |
| **元数据丰富度** | 少（标题 + 几个状态标记） | 中等（10 个字段） | 最多（约 70 个字段） | 中等（约 18 个字段） |
| **维护能力** | 无 | 无 | 最完善（过期/容量/轮转/预算） | 有（90 天过期清理 + 1000 容量上限） |
| **并发能力** | 弱（单进程写） | 强（SQLite WAL） | 中等（文件锁 + 队列） | 中等（threading.Lock，单 worker） |
| **分支能力** | 有（DAG，UI 不暴露） | 无 | 无 | 有（DAG，UI 可切换） |
| **渠道支持** | 无 | 无 | 有（完整路由字段） | 有（channel/account_id/peer） |
| **归档/置顶** | 无 | 无 | 无 | 有（pinned/archived/group） |
| **崩溃恢复** | 无自动恢复 | 不需要（单进程） | 无自动恢复 | 启动时重置 status=running → idle + 注册表损坏自动重建 |
| **空壳防护** | 不需要（文件即内容） | 不需要（INSERT 即有数据） | 不需要 | 延迟创建 + 创建即写消息原子化 |
