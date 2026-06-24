# 设计文档与代码不对齐的待修项

审计日期: 2026-06-18（第二次审计）

本文件记录设计文档与实际代码的偏差，按优先级排列。修复后从此处删除对应条目。

---

## ~~路径错误~~（已修复）

### ~~extension-gating/implementation.md~~
- ~~文档写的路径: `openprogram/agents/gating.py`, `openprogram/agents/manager.py`~~
- ~~实际路径: `openprogram/agent/management/gating.py`, `openprogram/agent/management/manager.py`~~
- 状态: ✅ 已修正，文档路径现已正确

---

## 需要更新的文档（HIGH）

### providers/thinking-effort.md
1. **§10 待修项中 Opus 4.7 override 条目过时**: 文档把 `["low","medium","high"]` 限制当作 bug，
   但这是 Anthropic（Claude 4.6 guidance）的 deliberate design choice。应删除该待修条目或改为说明设计原因。
2. **"max" 级别映射标记错误**: 文档声称 5 个 provider 的 max 映射"未映射"，但实际代码中
   `anthropic.py` 已有 `xhigh → max` 映射，所有 provider 都支持 max level。应更新映射表。
3. **Fable 5**: 文档提到缺少 Fable 5 说明，但 `thinking_catalog.py` 中也没有 Fable 5 条目。
   需要确认：是代码缺还是文档多写了？如果该模型已经存在于 models.dev 目录但 catalog 未收录，需加上。

---

## 状态标记过期（MEDIUM）

### memory/memory-v2.md
- Phase 0-1: 已完成（文档标记正确）
- Phase 2: 文档标 "❌ 未开始"，但 §0.5 又提到 "前置读层已落地"（Provenance dataclass）
- 应明确：Phase 2 分拆为子步骤，标注哪些 substep 已 partial、哪些待做

### ~~context/contextgit.md → 已并入 context/storage-and-engine.md~~
- ~~文档标记: "Status: proposal, not implemented"~~
- 状态: ✅ 已与 context-commit-chain / context-engine-spec / context-attach-merge / cross-turn 一并合入 `context/context.md`（DAG 底座在 `contextgit/dag.py`，上层未建）

---

## 实施滞后（设计有效但代码未完全跟上）

### context/cross-turn-tool-context.md
- "tool aging + 1 行语义 stub" 策略文档描述完整
- `openprogram/context/tool_aging/` 存在但实现与文档有偏差
- 待实施完成后同步文档

### providers/model-catalog-final.md
- models.dev 自动更新 TTL + fetched 覆盖式保存的完整流水线未完全落地
- 模型列表拉取逻辑存在但自动刷新机制未实现

---

## 内容缺失

### runtime/ 缺 process_runner 设计文档
- `agent/process_runner.py` 是重要的子进程执行模块（spawn、stop、user-input bridge）
- 没有对应的设计文档

### runtime/ 缺 dispatcher 设计文档
- `agent/dispatcher/__init__.py` 是 530 行的核心模块
- 没有独立的设计文档（dispatcher-split.md 只讨论拆分，不是完整设计）

---

## 本次审计确认已正确的文档

以下文档经审计与代码完全一致，无需修改：

- `runtime/controllability-and-three-surface-sync.md` — attended/unattended、graceful stop、三端同步均已实现
- `runtime/user-input-requests.md` — Phase 1+2 已落地（QuestionRegistry、三种 Transport 含新增的 TTYTransport）
- `function/function-calling-unification.md` — 已使用 "profiles" 术语，与代码一致
- `extension-gating/implementation.md` — 路径已正确
- `context/context.md`（合并自 contextgit 等五份 + 总图）— 状态标记正确

---

## 工具调用体系待解决问题

### 1. wiki_agent 自递归原因（✅ 已查明）
- **根因**：`research_harness/wiki/wiki_agent.py:122` 裸调 `runtime.exec(content=[task])`——
  不传 toolset/tools,默认拿 `DEFAULT_TOOLSET="full"`(98 个工具,含 wiki_agent 自身)。
  模型看到 wiki_agent 的 tool description("Maintain a wiki vault — route to ingest...")
  正好匹配当前任务("调研 long horizon agent") → 认为需要调 wiki_agent → 调自己 →
  进去又是裸 exec 又看到自己 → 无限递归。每层返回
  `{'error': "'info|warning|success|error'"}`(wiki 内部 enum validation 失败),
  上层模型收到错误 → 重试又调自己。
- **已解决（commit `1f6f5fce`）**：从「self-deny 屏蔽工具」改成「处境引导 + 递归深度上限兜底」。
  - 处境提示(`runtime._situational_prefix`)注入 user turn 开头,告诉模型「你在 X 体内、调 X = 无限递归、用底层工具」,docstring 降级置后 → 直接否定「该路由给 wiki_agent」的前提。
  - 兜底:`_MAX_AGENTIC_RECURSION_DEPTH=5`,per-function-name 计数,同名超 5 层抛 `RecursionError`。
  - self-deny 已删,工具列表含函数自己,靠引导不靠屏蔽。
  - 设计文档:`docs/design/runtime/agentic-self-recursion.md`;测试:`tests/agentic_programming/test_self_recursion_guard.py`(8 用例)。
- **遗留**（待做，见 #2）：各 harness 的 exec 限定工具集 + 跨函数环(A→B→A)识别(当前只防直接自递归)。
- 会话记录：`~/.openprogram/sessions/local_d125e9a9c3/history/`
  context_tree 展示 7 层嵌套(4d76→0c07→0964→c6f9→f1c9→4379→8746→100c)。

### 2. Harness 内部工具集是否需要限制
- 问题：wiki_agent/research_agent/gui_agent 这些 harness 在自己的内部 exec 里,是否应该只看到"做本职工作需要的工具",而不是 full 全集?
- 现状：默认全开(full),self-deny 只挡了自己调自己;一个 harness 仍可调另一个(wiki 调 research、research 调 gui)——可能导致"跑偏"。
- 决策待定：(a) 框架不管,各 harness 自己在 exec 里传 toolset 限制;(b) 框架自动 deny 所有 harness entry points(wiki/research/gui)当一个 harness 在跑时;(c) 保持现状只防自递归。
- 参考：Claude Code 的 subagent 用 allowlist 限工具,正是防这种跑偏。

### 3. Tool Profile 选择尚未影响实际工具解析
- 问题：聊天框 profile picker 可选 profile、后端持久化 active profile,但**选了某 profile 后这次对话实际用的工具仍由 Tools toggle(on/off)决定**,没有把 profile 的工具列表作为 tools_override 发给 dispatcher。
- 修法：WS chat action 传 active profile name → dispatcher 用 `agent_tools(toolset=<profile>)` 解析 → 只给那组工具。
- 位置：`webui/ws_actions/chat.py:313-316` (tools_override 逻辑) + `composer/index.tsx` submit 函数。

### 4. Functions 页 Agentic/Built-in tab 分离(进行中)
- 设计：顶部 tab 栏(类似 Memory 页的 Wiki/Journal/Core),分 Agentic(函数管理+文件夹)和 Built-in Tools(profile 管理)。
- 现状：tab 栏已加、tab 状态已加、sidebar 在 builtin tab 隐藏、agentic 内容在 builtin tab 隐藏、tools 只在 builtin tab 显示。CSS 已加。
- 待做：typecheck + build + 浏览器验证,确认分 tab 渲染正确。

### 5. ~~Functions 页删除操作仍用原生 confirm()~~
- ✅ 已修复：搜索确认无残留原生 `confirm()` 调用，已全部替换为 ConfirmDialog。

### 6. ~~Functions 页工具右键菜单不合理~~
- ✅ 已修复：`functions-page.tsx:579` 已改为 `tab === "agentic" ? contentCtx : undefined`，builtin tab 不触发 contentCtx。

---

## Agentic Function 运行时待解决问题

### 7. 断点续跑（checkpoint resume）
- **问题**：agentic function 内部 `runtime.exec` 连续 6 次重试全失败（provider 不可达）后直接抛错，函数终止，无法恢复。
- **现状**：DAG 状态完整（frame 节点标 `status="error"`，子节点全保留），`_render_history_messages` 从 DAG 加载历史，基础设施已就位。
- **方案**：提供 `resume_function(session_id, node_id)` 入口——把 frame 节点 status 改回 `running`，用同一个 frame_node_id 重新调 `runtime.exec`，DAG 历史自动接上。webui 加"重试"按钮触发。
- **核心改动**：需要一个"重入"入口 + 恢复 contextvar（_call_id 等）+ 重建 runtime/agent 上下文。
- **位置**：`agentic_programming/function.py`（wrapper 层）、`agentic_programming/runtime.py`（exec 层）

### ~~8. Bash 工具文件修改不追踪~~
- ✅ 已解决（`69432d88`）：统一入口触发——`_execute_tool_calls` 中 bash 执行前后做文件状态 diff，变更文件自动补做 checkpoint。
- 已知限制：当前只扫 cwd 顶层文件，子目录变更未覆盖（待后续改为递归扫描）。
- 另外 ④ 系统级沙箱（`cf2edde5`）也从源头限制了 bash 能碰的文件范围。

---

## ~~其它待解决~~（已修复）

### ~~research_agent 的假预设 `toolset=("harness",)`~~
- ✅ 已修复：改为 `toolset=("research",)`（2026-06-18）

### ~~设计文档 "full" 静态列表注释过时~~
- ✅ 已修复：`functions/__init__.py` TOOLSETS["full"] 注释已更新，说明 full 现在只是命名预设，暴露由 `exposed_names()` 动态收集。
