# UI 跨模块不变量

> 单模块文档写"这个模块怎么工作"，这份文档写"模块之间必须共同遵守什么"。
> 这里的每一条都曾经因为没写下来而出过真实 bug（每条附出处）。改动任何
> 一个相关模块前，对着这份清单过一遍；新增会打破清单的功能时，先改清单
> 再改代码。每条不变量都应有钉子测试（tests/unit/），文档和测试一起是
> 规则的两半：文档说给人听，测试挡住回归。

## 1. 启用集合是模型可用性的唯一门控

`list_enabled_models()`（providers 配置里的 spec 行）是"用户启用了哪些
模型"的单一事实源。**任何**展示或使用模型的地方都必须以它为准：

- 聊天顶栏的 chat/exec chip（`GET /api/agent_settings` 门控后的值）；
- 模型选择下拉（`/api/models/enabled`）；
- 发送路径的默认解析（`_resolve_session_provider_model`——包括
  agent.json 的 agent 配置模型这条**曾经不门控**的路径）；
- 函数执行用的 exec runtime。

推论：一个模型被禁用后，它**不得**出现在任何选择器里、**不得**作为任何
默认被解析、**不得**被任何 chip 展示。"picker 空 ⇔ 顶栏无模型 ⇔ 发送
报 enable-a-model" 三者必须同步成立。

出处：禁用后 chip 仍显示旧模型（2026-07-10，commit 66cb7f73）；agent.json
路径不门控导致禁用模型继续跑（cb78dde1）。

## 2. 启用集合变化必须广播，且所有消费者响应

设置页可能开在**另一个浏览器标签页**。模型/Provider 启停、自定义
Provider 删除，后端必须经事件总线发 `agent_settings_changed`
（`ws.frame`），前端 ws 处理器收到后：

- 重新拉取 agent settings（更新 chip）；
- 失效 `["models-enabled"]` react-query 缓存（更新所有下拉）。

只在发起操作的那个标签页里本地刷新是不够的。新增会改变启用集合的入口
（导入配置、批量操作、CLI 写配置……）时必须挂同一个广播。

出处：跨标签页 chip / 下拉不同步（66cb7f73）。

## 3. 禁用当前默认 = 清除默认，不是隐藏默认

默认模型存在三处：`_runtime_management` 的 chat 全局、exec 全局、默认
agent 的 agent.json `model`。当前默认掉出启用集合时，三处必须**全部清
空**（`_clear_stale_defaults`），由用户显式选下一个。只在展示层把它
藏起来会留下僵尸默认：重新启用时它自动复活，禁用期间会话还在偷偷用它。

出处：cb78dde1。

## 4. 前端 store 的"清除"必须可表达

`setAgentSettings` 语义：对象=替换，`null`=清除，`undefined`=保留。
"保留旧值"的合并语义（`??`）会让"设置已变空"永远覆盖不掉旧值——凡是
后端可能回传"此项现在没有了"的 store 字段，setter 必须区分"没提这个字
段"和"这个字段清空了"。

出处：chip 清不掉，整页刷新掩盖了这个 bug（66cb7f73）。

## 5. 后端→前端的帧一律走事件总线

外部源和 webui 路由都用 `emit_ws_frame({"type": ..., "data": ...})`
发帧，server 里唯一的订阅者转发给 socket（event-layer.md）。不要直接
调 `_s._broadcast`——绕过总线的帧对事件层的消费者（proactive、日志）
不可见，也让"哪些地方在发什么"无处可查。
（`_broadcast_envelope` / `_broadcast_chat_response` 是带会话路由逻辑
的 server 内部辅助，不在此列。）

出处：6d93ce4a（routes 迁移，event-layer 步 4 的 routes 部分）。

## 6. spawn 的三个入口语义必须一致

spawn 一个 sub-agent 分支有三个入口：`task()` 同步路径
（functions/tools/task/task.py）、异步 runner
（agent/task/runner.py）、`message_branch`
（functions/tools/agent_collab/）。三者对 clean 模式必须一致地传
`spawn_caller=<发起节点>`，使分支根节点 `caller` 指向发起它的那轮
（session-dag.md §2.3），而不是挂在 ROOT 上。改 spawn 语义时三个入口
一起改，一起测。

同一条链上的 spawn 深度共用一个计数器；task() 有自己的紧上限
`MAX_TASK_DEPTH=2`（主 agent → 协调者 → 干活者，到此为止——更深就是
代理在推活儿而不是干活儿，实测出现过 5 代天气查询委托链），
message_branch 保留宽松的 `MAX_SPAWN_DEPTH=8`（预算给分支间多轮
对话，不是委托链）。

出处：同步路径漏传导致 DAG 分支从头分叉（1d1fe016）；异步 task()
漏传 caller + 深度不计数（后续修复）。

## 7. 聊天兄弟切换器只在"真实的分叉"上出现

`< N/M >` 兄弟集按**分叉点**分组（`predecessor`，缺失退回
`caller`，ROOT 归一为无），且只包含对话轮次：

- tool/code 子调用行不参与（它们天然无 predecessor，会污染根集合）；
- `source=agent_spawn` 的分支根不和用户的轮次混组（那是 agent 开的
  分支，不是用户可翻页的替代版本）；
- `display=runtime` 卡片不参与（fn-run 卡有自己的 fn-run 域导航）。

出处：新会话的"你好"显示 1/6（1d1fe016；更早还有过 1/12 一次）。

## 8. 交互即时反馈（0ms 乐观态）

任何点击立即渲染乐观过渡态（pending 卡、按钮态、切换后的索引），真实
数据到达后回填；超时回滚并 toast。长操作（函数运行有 ~1s 子进程冷启）
绝不允许"点了没反应"。实现入口：`optimisticAction`
（web/lib/runtime-bridge/optimistic-action.ts）。

出处：retry / fn-form / checkout 全套乐观化（0b3b9c2e）。

## 9. 显示顺序可以调，数据顺序不可以

attach 指针节点必须留在对话链尾（head 移动依赖它），但聊天里 Spawned
卡片显示在它喂给的那轮回复**之前**——这类需求一律在渲染层调序
（conv-mapper），不改落盘顺序、不改 head 语义。

出处：1d1fe016。

## 10. SSR 边界：模块顶层不碰 window

`web/lib/runtime-bridge/*` 在模块作用域读 `window`，被 App Router 页面
静态 import 会炸 prerender。settings/页面组件需要它们时用动态
`import()`（例：`refreshAgentChip`）。新写 runtime-bridge 模块时守住
这条，或者把 window 访问推迟到函数体内。

出处：f09ed1c2 期间 settings 页 prerender 崩溃。
