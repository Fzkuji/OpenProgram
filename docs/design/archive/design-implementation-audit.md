# 设计文档与当前实现差异审查（清理前记录）

生成时间：2026-05-27

边界：本文件只整理设计文档与当前实现的差异，不包含实现修改。

后续清理：已确认当前实现口径以 git-backed ContextCommit 为准。`context-commit-chain.md` 已按当前实现重写；旧口径文档和重复 HTML 已删除。本文件保留清理前的审查记录，旧文档行号只用于追溯当时的判断。

审查范围：
- `docs/design/runtime/dag-node-model.md`
- `docs/design/runtime/dag-edge-split.md`
- `docs/design/dag-as-memory-unified.md`
- `docs/design/context/context-commit-chain.md`
- `docs/design/context-snapshot-chain.md`
- `docs/design/context/context-attach-merge.md`
- `docs/design/runtime/streaming-resume.md`
- 对应实现：`openprogram/context/`, `openprogram/store/`, `openprogram/agent/`, `openprogram/webui/`, `web/components/right-sidebar/`, `web/lib/runtime-bridge/`

## 1. Context 设计文档之间存在互斥口径

差异性质：设计文档之间冲突，同时当前实现只符合其中一部分。

设计文档中存在三套不同口径：
- `docs/design/dag-as-memory-unified.md:7-30`：会话记忆应由 DAG 本身加 annotation 表达，context view 运行时计算，不持久化。
- `docs/design/context-snapshot-chain.md:1-23`、`docs/design/context-snapshot-chain.md:51-68`：会话记忆由 DAG + Context Snapshot Chain 表达，并设计 `context_snapshots` 表。
- `docs/design/context/context-commit-chain.md:1-23`、`docs/design/context/context-commit-chain.md:51-68`：会话记忆由 DAG + Context Commit Chain 表达，并设计 `context_commits` 表。

当前实现使用的是 ContextCommit 路径：
- `openprogram/context/engine.py:214-243` 调用 commit chain 生成 LLM 输入。
- `openprogram/context/engine.py:522-605` 通过 `ensure_latest_commit()` 和 `render_commit()` 构造 provider messages。
- `openprogram/context/commit/store.py:1-15` 明确说明当前是 git-backed ContextCommit，而不是 SQLite 表。
- `openprogram/context/commit/store.py:49-64` 将每个 commit 写为 `context/commits/<commit_id>.json`。

判定：
- 当前实现不符合 `dag-as-memory-unified.md` 的“context view 不持久化、annotation 写回 DAG metadata”口径。
- 当前实现不符合 `context-snapshot-chain.md` 的 snapshot 口径。
- 当前实现部分符合 `context-commit-chain.md`，但存储后端和 blob 设计不同。

## 2. `context-snapshot-chain.md` 已经和当前实现脱节

差异性质：文档描述的模块、表结构、UI action 与当前实现不一致。

设计要求：
- `docs/design/context-snapshot-chain.md:51-68` 设计 `context_snapshots` SQL 表。
- `docs/design/context-snapshot-chain.md:307-318` 设计 `context/snapshot.py`、`context/blob_store.py`、`context/views.py`。
- `docs/design/context-snapshot-chain.md:325-333` 计划 UI snapshot timeline。

当前实现：
- `openprogram/context/` 下没有 `snapshot.py` 和 `blob_store.py`。当前目录实际是 `commit/`、`rules/`、`engine.py` 等。
- `openprogram/webui/ws_actions/snapshots.py:17-24` 仍然导入 `openprogram.context.snapshot`，但该模块不存在。
- `openprogram/webui/server.py:1053-1081` 的 WebSocket action registry 没有注册 `ws_actions.snapshots`。
- `web/components/right-sidebar/right-sidebar.tsx:323-324` 实际渲染的是 `ContextCommitTimeline`。
- `web/components/right-sidebar/snapshot-timeline.tsx:86` 定义了 `SnapshotTimeline`，但当前右侧栏没有引用它。

判定：snapshot 相关设计和实现残留应视为过期路径。若调用 `list_snapshots` 或 `get_snapshot_detail`，当前 action 内部会因为缺少 `openprogram.context.snapshot` 进入异常返回。

## 3. `context-commit-chain.md` 的 SQL 表和 blob 设计没有按文档实现

差异性质：功能方向一致，但存储实现不同。

设计要求：
- `docs/design/context/context-commit-chain.md:51-68` 设计 `context_commits` SQL 表。
- `docs/design/context/context-commit-chain.md:261-288` 设计 `context_blobs` 表和应用层 hash dedup。
- `docs/design/context/context-commit-chain.md:307-318` 设计 `context/commit.py`、`context/blob_store.py`、`context/views.py`。
- `docs/design/context/context-commit-chain.md:335-343` 把同份 rendered 只存一次作为不变式。

当前实现：
- `openprogram/context/commit/store.py:1-15` 明确改为 git-backed，每个 ContextCommit 是一个 JSON 文件。
- `openprogram/context/commit/store.py:7-12` 明确说明不再有应用层 blob dedup 表，依赖 git object dedup。
- `openprogram/context/commit/store.py:29-32` 的 `init_schema()` 是 no-op。
- `openprogram/context/commit/store.py:49-64` 写入 `context/commits/<id>.json`。

判定：`context-commit-chain.md` 中 SQL schema、`context_blobs`、应用层 refcount GC 仍停留在旧设计，和当前 git-backed 实现不同。

## 4. `context-commit-chain.md` 的“没有 pin”与当前 anchor 机制不一致

差异性质：实现扩展了文档没有描述的保留机制。

设计要求：
- `docs/design/context/context-commit-chain.md:106-107` 写明没有用户 pin 机制。
- `docs/design/context/context-commit-chain.md:342` 把“没有强制保留机制”列为不变式。

当前实现：
- `openprogram/context/commit/types.py:56-60` 增加了 `is_anchor` 和 `anchor_for_summary`。
- `openprogram/context/rules/summarize.py:55-60` 会跳过 locked item。
- `openprogram/context/rules/summarize.py:81-143` 在 summary 选择中会按规则跳过不可合并项目；anchor 会影响这一过程。

判定：当前实现有 anchor 保留机制。它不等同于用户 pin，但已经违反了文档中“没有强制保留机制”的描述。

## 5. `dag-edge-split.md` 的 schema 拆分没有按字段名实现

差异性质：概念上已有拆分，数据结构和文档字段名不一致。

设计要求：
- `docs/design/runtime/dag-edge-split.md:25-33` 要求 schema 层拆成 `conv_pred` 和 `caller`，且互斥。
- `docs/design/runtime/dag-edge-split.md:116-124` 要求增加 `conv_pred` 和 `caller` 字段。
- `docs/design/runtime/dag-edge-split.md:126-136` 要求写入时只写其中一条，并废弃隐藏的 `metadata.called_by`。

当前实现：
- `openprogram/context/nodes.py:50-98` 的 `Call` dataclass 只有 `called_by` 和 `metadata`，没有 `conv_pred` 字段。
- `openprogram/store/session_store.py:59-70` 用 `metadata.parent_id` 表示 conversation predecessor，用 `Call.called_by` 表示 caller。
- `openprogram/store/session_store.py:302-320` append 时确实把 `predecessor` 和 `caller` 分别传入 index。
- `openprogram/store/_msg_adapter.py:72-83` tool 节点仍会从 `tool_use.called_by` 或旧 predecessor 推导 `called_by`。
- `openprogram/store/_msg_adapter.py:91-105` assistant attach pointer 仍会把 message 里的 `called_by` 提升为 `Call.called_by`。
- `openprogram/store/_msg_adapter.py:119-185` 输出 msg 时仍写出 `parent_id`、`caller` 和兼容用 `called_by`。

判定：实现已经把 conversation edge 和 call edge 分开处理，但不是文档中的 `conv_pred` 字段方案；兼容字段仍存在，不能说已经“彻底废弃 metadata.called_by / called_by”。

## 6. `dag-edge-split.md` 要求删除 `aggregate_tool_messages`，当前仍在使用

差异性质：文档要求删除的旧聚合路径仍存在。

设计要求：
- `docs/design/runtime/dag-edge-split.md:138-147` 要求删除 `aggregate_tool_messages`，让前端按 caller 关系渲染 tool 节点。

当前实现：
- `openprogram/webui/persistence.py:172` 仍定义 `aggregate_tool_messages()`。
- `openprogram/webui/persistence.py:164` 仍调用该函数。
- `openprogram/webui/ws_actions/session.py:150-158` 在 `load_session` 时仍导入并调用 `aggregate_tool_messages()`。

判定：这一项没有按设计完成。当前仍保留“把 standalone tool rows 折进 assistant.tool_calls”的兼容路径。

## 7. `dag-node-model.md` 的 DAG 视觉规则基本实现，但 attach / merge / task 交互语义有风险

差异性质：视觉和 layout 大体对齐；点击行为可能和“正式节点”口径不一致。

设计要求：
- `docs/design/runtime/dag-node-model.md:18-20` 合成 followup user message 不渲染、不画 DAG。
- `docs/design/runtime/dag-node-model.md:28-37` `task` / `attach` / `merge` 都是 branch operation，使用 `square_outline`。
- `docs/design/runtime/dag-node-model.md:43-68` sequence / call / reference 三类边分开。
- `docs/design/runtime/dag-node-model.md:133-144` 要求跳过 `task_followup` user，并让 `attach` / `merge` 作为正式节点进 sequence。

当前实现对齐部分：
- `openprogram/webui/graph_layout/filter.py:20-31` 识别 `source == "task_followup" && role == "user"`。
- `openprogram/webui/graph_layout/filter.py:34-69` 将 task followup assistant 的 parent 改到 attach pointer 上。
- `openprogram/webui/graph_layout/filter.py:72-86` 不把 task followup user 交给 layout。
- `openprogram/webui/graph_layout/lane.py:142-157` 明确让 `attach` / `merge` 留在 caller lane，并继续遍历它们的 conv children。
- `web/lib/runtime-bridge/history/shapes.ts:73-83` 对 `task` / `attach` / `merge` 全部返回 `square_outline`，和 `dag-node-model.md:32-37` 一致。

当前实现风险：
- `web/lib/runtime-bridge/history-graph.ts:584-600` 把任何有 `caller` / `called_by` 的节点都标记为 internal。
- `web/lib/runtime-bridge/history-graph.ts:903-916` 把这个标记写入 `data-internal`。
- `web/lib/runtime-bridge/history-graph.ts:1326-1337` 单击 internal 节点时只滚动到 owner，不走普通节点折叠逻辑。
- `web/lib/runtime-bridge/history-graph.ts:1365-1385` 双击 internal 节点时也滚动到 owner，不 checkout 到该节点。

判定：如果 `attach` / `merge` / `task` 节点带 `caller`，它们会被当作 internal 节点处理；这和文档中“branch-referencing 是当前 branch 的新节点，进 sequence”的交互预期可能不一致。视觉上已经画出来，但节点交互不完全像普通 sequence 节点。

## 8. `dag-node-model.md` 中 merge reference edge 的要求没有完整实现

差异性质：attach reference edge 已实现，merge reference edge 没有完整数据来源。

设计要求：
- `docs/design/runtime/dag-node-model.md:104-118` 描述 `merge` 和 `attach` 同构，DAG 层应有 `function_call(merge, ref=B_tip)`，通过 reference edge 指向被合并 branch tip。
- `docs/design/runtime/dag-node-model.md:130` 写明 merge 的 reference edge 来源是 `ContextCommit.parent_ids[1:]`。

当前实现：
- `web/lib/runtime-bridge/history-graph.ts:754-759` 前端确实尝试对 `attach` 和 `merge` 都读取 `node.attach_ref` 并画 reference edge。
- `openprogram/webui/ws_actions/branch.py:8-40` 的 `_attach_info()` 只对 `function == "attach"` 返回 source head。
- `openprogram/webui/ws_actions/branch.py:207-227` graph payload 的 `attach_ref` 来自 `_attach_info(m)`。
- `openprogram/webui/ws_actions/session.py:306-328` load_session graph payload 的 `attach_ref` 也来自 `_ainfo(m)`，同样只处理 attach。
- `openprogram/agent/_merge.py:295-320` merge 前写入的是临时 `function="attach"` pointer。
- `openprogram/agent/_merge.py:340-348` 随后调用 `process_user_turn()` 生成合并回复。
- `openprogram/agent/_merge.py:373-386` 保存 multi-parent ContextCommit，但没有生成一个带 `function="merge"` 和 `attach_ref` 的持久 DAG 节点。

判定：ContextCommit 层的 `parent_ids` 已经保存，但 history graph 当前没有把 `parent_ids[1:]` 转成 merge 节点的 reference edge。因此文档中“merge 在 DAG 上作为 branch-referencing function_call 并画 reference edge”的要求没有完整实现。

## 9. `context-attach-merge.md` 的 merge 失败回滚没有实现

差异性质：失败路径不符合设计。

设计要求：
- `docs/design/context/context-attach-merge.md:197-209` 要求 merge 前临时注入 attach pointer，merge 失败要回滚 attach pointer，避免留下中间状态。
- `docs/design/context/context-attach-merge.md:206` 明确写了失败要回滚 attach pointer。

当前实现：
- `openprogram/agent/_merge.py:295-320` 在 target session 上先写入多个 `function="attach"` pointer。
- `openprogram/agent/_merge.py:347-363` 如果 `process_user_turn()` 抛异常或 `turn.failed`，直接返回失败结果。
- 这段失败返回前没有删除已经写入的 attach pointer。

判定：merge 失败时可能留下 `merge_temp=True` 的 attach pointer。当前实现没有按设计回滚。

## 10. `context-attach-merge.md` 要求 merge assistant metadata 写 `merged_from`，当前没有实现

差异性质：可追溯字段缺失。

设计要求：
- `docs/design/context/context-attach-merge.md:208` 要求 merge 的 assistant reply 节点 metadata 增加 `merged_from: [peer_session_id_or_head, ...]`。

当前实现：
- `openprogram/agent/_merge.py:373-386` 只保存新的 `ContextCommit`，summary 中包含 peer label。
- `openprogram/agent/_merge.py:439-455` 最后返回 `MergeTurnResult`。
- 代码库中未发现除设计文档外的 `merged_from` 实现。

判定：merge 的 peer 来源保存在 ContextCommit `parent_ids` 和 summary 中，但没有按文档写入 assistant reply metadata 的 `merged_from` 字段。

## 11. `context-attach-merge.md` 的 attach 数据字段命名和当前实现不同

差异性质：字段语义相近，但 schema 名称不一致。

设计要求：
- `docs/design/context/context-attach-merge.md:151` 要求 attach metadata 含 `source_session_id` / `source_head_id` / `source_commit_id` / `label` / `manual`。

当前实现：
- `openprogram/webui/ws_actions/branch.py:625-640` 写入的是 `attach.session_id`、`attach.head_id`、`attach.label`、`attach.manual`、`attach.source_commit_id`。
- `openprogram/context/commit/generator.py:189-218` 主要按 `source_commit_id` 加载 source commit；同 session 加载失败后再全局扫描。

判定：语义基本一致，但字段名不是设计文档中的 `source_session_id` / `source_head_id`。此外 generator 没有直接使用 attach 中存的 `session_id` 做跨 session 精确加载，而是在同 session 查找失败后全局扫描。

## 12. `context-attach-merge.md` 的 attach fallback 标记不完整

差异性质：fallback 功能存在，但文案和 marker 结构不完全符合设计。

设计要求：
- `docs/design/context/context-attach-merge.md:162-163` 要求 attached item 有 open / close marker；source commit 不可用时 fallback，并在 marker 里标注 source unavailable。

当前实现：
- `openprogram/context/commit/generator.py:220-236` 在 source commit 不存在时返回单条 user-role item，`reason="attached_legacy"`。
- `openprogram/context/commit/generator.py:248-328` 只有 source commit 可加载时才生成 open marker、展开内容、close marker。

判定：fallback 可用，但不可用来源没有按文档生成 open / close marker，也没有标注 source unavailable。

## 13. attach expansion、dedup、base lock、attach 边界 summary 已基本实现

差异性质：这些项与设计基本一致，列出是为了区分已完成项和未完成项。

设计要求：
- `docs/design/context/context-attach-merge.md:151-163` attach pointer 展开 source commit items，按 `attached_from` dedup。
- `docs/design/context/context-attach-merge.md:157` summary 不能跨 attach block 边界。
- `docs/design/context/context-attach-merge.md:203` merge 的 base peer 优先保留 full。
- `docs/design/context/context-attach-merge.md:207` merge 写 multi-parent ContextCommit。

当前实现：
- `openprogram/context/commit/types.py:62-67` `ContextItem` 已有 `attached_from`。
- `openprogram/context/commit/generator.py:238-247` 用 `source_commit_id in already_attached` 做 dedup。
- `openprogram/context/commit/generator.py:260-328` 展开 source commit items，并写 open / close marker。
- `openprogram/context/commit/generator.py:190-194` 读取 `is_base`。
- `openprogram/context/commit/generator.py:263-315` base attach items 会 `locked=is_base`。
- `openprogram/context/rules/summarize.py:61-143` 按 `attached_from` 保持 attach block 完整。
- `openprogram/agent/_merge.py:365-386` 保存包含多个 `parent_ids` 的 ContextCommit。

判定：attach commit expansion 的主路径已经实现；主要缺口集中在失败回滚、fallback marker、merge DAG reference edge 和 `merged_from`。

## 14. `streaming-resume.md` 只实现了部分能力

差异性质：已有 placeholder 和部分轮询持久化，但没有实现文档中的统一 streaming registry 和 msg 级订阅。

设计要求：
- `docs/design/runtime/streaming-resume.md:15-18` 任何运行中产物都要第一时间持久化 placeholder，并在增量更新时落盘和推送 WS。
- `docs/design/runtime/streaming-resume.md:23-39` 统一 status schema：`pending` / `running` / `done` / `error` / `aborted`。
- `docs/design/runtime/streaming-resume.md:40-53` 运行中消息约 250ms 节流持久化。
- `docs/design/runtime/streaming-resume.md:55-84` 新增 `subscribe_msg` / `unsubscribe_msg`，通过 `msg_update` 推送。
- `docs/design/runtime/streaming-resume.md:107-114` worker 启动时把 stale `running` 改成 `aborted`。

当前实现：
- `openprogram/agent/_turn_lifecycle.py:52-78` assistant turn 开始时会写 `status="running"` placeholder。
- `openprogram/agent/_turn_lifecycle.py:81-90` terminal status 使用 `completed` / `cancelled`，不是文档里的 `done` / `aborted`。
- `openprogram/webui/_exec_dag.py:134-193` execution DAG 采用约 1.2s polling，广播 `tree_update` 和 `branches_list`，不是 msg 级 `msg_update`。
- `openprogram/webui/_exec_dag.py:163-180` 会把 `context_tree` 和 `last_update_at` 写回 placeholder，但只覆盖 execution DAG 这一类路径。
- `openprogram/agent/streaming/registry.py:1-14` 文件说明当前是 skeleton。
- `openprogram/agent/streaming/registry.py:168-180` `_persist()` 是 no-op。
- `openprogram/agent/streaming/registry.py:182-193` `_broadcast()` 是 no-op。
- 全仓库未发现已注册的 `subscribe_msg` / `unsubscribe_msg` action。
- `openprogram/webui/_exec_dag.py:222-275` worker startup repair 把 `running` 改成 `interrupted`，不是 `aborted`。
- `openprogram/webui/server.py:1128-1136` 启动时调用的是 `reconcile_interrupted_runs()`。

判定：当前实现是分散的部分实现，不是 `streaming-resume.md` 描述的统一方案。status 枚举、WS action、推送事件类型、节流间隔、启动修复终态都和文档不同。

## 15. `streaming-resume.md` 的前端自动续连没有实现为 msg 级订阅

差异性质：前端有 status 字段，但没有文档中的订阅流程。

设计要求：
- `docs/design/runtime/streaming-resume.md:86-106` 前端 `load_session` 后扫描 `status === "running"` 的消息，发送 `subscribe_msg`，收到 `msg_update` 后 patch 对应 ChatMsg。

当前实现：
- `web/lib/session-store.ts:55-75` ChatMsg 类型中有 `status`、`function`、`display`、`source` 等字段。
- 代码搜索未发现 `subscribe_msg` / `unsubscribe_msg` action 使用。
- 当前 live execution UI 依赖 `tree_update`、`branches_list` 等已有事件，而不是 msg 级订阅。

判定：前端数据结构已能携带 status，但没有按设计实现自动 msg 级续连。

## 16. `dag-as-memory-unified.md` 的 annotation metadata 方案没有实现

差异性质：当前 context state 存在 ContextCommit item 中，而不是 DAG node metadata。

设计要求：
- `docs/design/dag-as-memory-unified.md:52-73` 要求 DAG node metadata 增加 `context_state`、`context_state_set_at`、`context_state_reason`、`summarized_into`，由 context engine 写回。
- `docs/design/dag-as-memory-unified.md:153-185` 计划重组为 `context/annotations/` 和 `context/views/`。
- `docs/design/dag-as-memory-unified.md:220-227` 路线中 Phase 2 是 annotation 持久化。

当前实现：
- `openprogram/context/commit/types.py:33-83` context state 存在 `ContextItem.state`、`locked`、`state_set_at`、`reason`、`merged_into` 中。
- `openprogram/context/engine.py:522-605` 通过 ContextCommit 生成 provider messages。
- `openprogram/context/` 当前没有 `annotations/` 和 `views/` 目录；有 `commit/` 和 `rules/`。

判定：`dag-as-memory-unified.md` 与当前 ContextCommit 实现不是同一设计。该文档应视为未实施或已被后续 commit-chain 方案替代。

## 17. 当前 Branches 列表实现比 `dag-edge-split.md` 描述复杂

差异性质：不是直接错误，但文档中的简化 SQL 与当前实现不一致。

设计要求：
- `docs/design/runtime/dag-edge-split.md:101-114` 定义 branch tip 为 `caller IS NULL` 且没有 `conv_pred` 后继。
- `docs/design/runtime/dag-edge-split.md:138-147` 认为读取路径可以自然统一，不再需要启发式处理。

当前实现：
- `openprogram/store/session_store.py:378-504` 的 `list_branches()` 包含 main lane 检测、跳过 `agent_spawn`、跳过 attach pointer、扫描 merge commit 的 `parent_ids`、过滤 `merged_heads`、手动补入 main tip 等逻辑。

判定：当前实现仍有多条规则来修正 branch tip 展示，和文档中“schema 拆分后 branch tip 查询自然简化”的状态不同。

## 18. 当前工作树中存在新旧设计文件并存

差异性质：不是实现 bug，但会影响后续判断哪份设计是权威来源。

观察：
- `docs/design/context-snapshot-chain.md` 和 `docs/design/context/context-commit-chain.md` 同时存在。
- `docs/design/dag-as-memory-unified.md` 与 `context-commit-chain.md` 对 context view 是否持久化的要求相反。
- `openprogram/webui/ws_actions/snapshots.py` 和 `web/components/right-sidebar/snapshot-timeline.tsx` 仍保留 snapshot 命名路径，但当前 UI 与 engine 使用 commit 命名路径。

判定：当前项目同时保留了至少三套 context 设计命名：annotation、snapshot、commit。实现实际采用 commit；snapshot 和 annotation 文档没有同步到当前实现状态。

## 19. 未列为差异的已对齐项

这些点已经和相关设计基本一致：
- `task_followup` synthetic user 不进入 DAG layout：`openprogram/webui/graph_layout/filter.py:20-86`。
- `task` / `attach` / `merge` 统一 `square_outline`：`web/lib/runtime-bridge/history/shapes.ts:73-83`。
- `attach` / `merge` layout 保持在 caller lane：`openprogram/webui/graph_layout/lane.py:142-157`。
- attach pointer 写入 `source_commit_id`：`openprogram/webui/ws_actions/branch.py:625-640`。
- manual attach 后广播 `session_reload`：`openprogram/webui/ws_actions/branch.py:673-698`。
- attach source commit 展开、dedup、open/close marker：`openprogram/context/commit/generator.py:238-328`。
- merge base peer 锁定：`openprogram/context/commit/generator.py:190-194`、`openprogram/context/commit/generator.py:263-315`。
- summarize 不跨 attach block：`openprogram/context/rules/summarize.py:61-143`。
- merge 保存 multi-parent ContextCommit：`openprogram/agent/_merge.py:365-386`。
