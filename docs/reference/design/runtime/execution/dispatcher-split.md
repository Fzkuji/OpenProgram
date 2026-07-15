# 将 `agent/dispatcher.py` 拆分为按职责划分的包

状态：**进行中** · 已删除死代码（1fab7479） · 步骤 0 建包 · 步骤 1 types.py · 步骤 2 titles.py + forced_tool.py · 步骤 3a runtime_attach.py（`_wrap_agentic_runtime_block`） · 步骤 4 finalize.py（阶段 6） · 步骤 5a persistence.py（阶段 5 助手消息持久化） · `__init__.py` 现已 <1000 行 · 负责人：agent/runtime · 创建时间：2026-06-04

> **测试接缝说明（步骤 3 期间发现）。** dispatcher 的单元测试在**包**对象上
> monkeypatch 了 `D._resolve_model` / `D._load_agent_profile` / `D._run_loop_blocking`，
> 并捕获 `orig = D._run_loop_blocking` 以使用伪造的 `stream_fn` 运行真实循环。
> 函数内部对辅助函数的查找会在*其所在*模块的全局命名空间中解析，因此把
> `_run_loop_blocking` 移到 `loop.py` 会使其对 `_resolve_model` 的调用错过
> `D.*` 补丁，从而破坏约 40 个测试。因此：内部调用了被测试 patch 的辅助函数
> （`_run_loop_blocking`）的函数暂时保持原位；而函数内部的各**阶段**（持久化 /
> 收尾）可以干净地抽出——做法是将已解析好的 model/profile 作为显式参数传入
> （dispatcher 在补丁下解析它们一次，再向下传递），这样抽出的模块就永远不会
> 调用被 patch 的辅助函数。不触及任何被 patch 辅助函数的独立函数
> （`_wrap_agentic_runtime_block`）可以自由迁移。最终的 `loop.py` 迁移需要一个
> 补丁稳定的辅助函数接缝（调用时通过 `_model_tools.<fn>` 访问），或更新测试的
> patch 目标——这作为单独的一个步骤跟踪，不并入代码搬移的 commit。

这是「禁止 1000 行以上文件」规则及「层级化代码结构——按职责划分模块目录」约定下
的一项路线图工作。`dispatcher.py` 是 webui 聊天轮次的真实执行路径；本文规划如何
在不改变行为的前提下将其拆开。

## 1. 问题

`openprogram/agent/dispatcher.py` 有 1928 行（原为 2059 行；死代码
`_legacy_dispatch_forced_tool_call_unused` 已在 1fab7479 中删除）。一个文件
承载了整个轮次的生命周期、两个约 300–830 行的函数，以及所有轮次收尾的记账逻辑。
它难以阅读、难以独立测试，且每新增一项关注点（一个新的记账步骤、一处新的持久化
细节）都会让同一个文件继续膨胀。

最严重的罪魁祸首是约 835 行的 `process_user_turn`（599–1433）。它本身已自带文档，
分为七个编号阶段，因此接缝是清晰的；只是这些接缝处于同一个函数内部，而非可分离
的独立单元。

## 2. 当前结构（基于实情，1fab7479 之后）

```
line   symbol                                  role
49     _InheritParent                          sentinel for "inherit parent id"
58     TurnRequest                             input dataclass
116    TurnResult                              output dataclass (+ error taxonomy fields)
158    _wrap_agentic_runtime_block (~308 ln)   wrap an @agentic_function block as a turn
466    dispatch_forced_tool_call (~133 ln)     forced single tool-call path
599    process_user_turn (~835 ln)             MAIN turn orchestration — phases 1–7
1434   _noop / _default_title                  tiny helpers
1443   _maybe_auto_title (~28 ln)              placeholder-title backfill
1471   trigger_compaction (~63 ln)             compaction trigger
1534   _run_loop_blocking (~395 ln)            the actual agent loop (chat main path)
```

`process_user_turn` 的七个阶段（行号 → 阶段）：

```
648    1. ensure session, load active-branch history
676    2. persist user message + attachment manifest
772    3. attach Runtime (real provider) with the session GraphStore
864    4. run the agent loop; classify + report errors        <- error taxonomy lives here
1036   5. persist assistant message
1193   6. bookkeeping: head_id, tokens, context-commit backfill (6.1),
       usage feedback (6.4), auto-title (6.5), compaction signal (6.6),
       git commit (6.8), project auto-commit (6.9), snapshot eviction (6.95)
1413   7. final TurnResult event
```

## 3. 提议的包布局

把该模块转换为 `openprogram/agent/dispatcher/`（一个包），每个文件承担单一职责，
没有一个超过约 500 行：

```
dispatcher/
  __init__.py        re-export the public surface (back-compat, see §5)
  types.py           _InheritParent, TurnRequest, TurnResult, INHERIT_PARENT
  turn.py            process_user_turn — thin orchestrator calling the phases
  persistence.py     phase 2 + 5: persist user/assistant nodes, attachment manifest
  runtime_attach.py  phase 3: create_runtime + GraphStore wiring, _wrap_agentic_runtime_block
  finalize.py        phase 6: head/token bookkeeping, usage feedback, git + project commit, eviction
  titles.py          _default_title, _maybe_auto_title, trigger_compaction
  forced_tool.py     dispatch_forced_tool_call
  loop.py            _run_loop_blocking — the agent loop + its error boundary
```

`turn.py` 中的 `process_user_turn` 变为一个编排器：加载 → 持久化用户消息 →
挂载 runtime → 运行循环 → 持久化助手消息 → 收尾 → 发出结果，每一步都是对兄弟
模块中一个具名函数的调用。错误分类（阶段 4 / 循环的 except）与循环一起保留在
`loop.py` 中，与
`docs/design/providers/reliability/error-taxonomy-propagation.md` 保持一致。

## 4. 迁移顺序（爆炸半径最小者优先）

每个步骤都是独立的一个 commit，须在编译 + 导入 + worker-restart-healthz 全绿之后
再进行下一步。纯代码搬移——同一个搬移 commit 中不做任何逻辑改动。

1. **types.py** —— 移动三个 dataclass + sentinel。风险最低：它们没有内部依赖。
   `__init__` 重新导出它们。
2. **titles.py + forced_tool.py** —— 叶子级辅助函数，调用方很少。
3. **persistence.py** —— 把阶段 2 与 5 抽成 `persist_user_turn(...)` /
   `persist_assistant_message(...)`，接收显式参数（不隐式闭包捕获
   `process_user_turn` 的局部变量）。这是最需要谨慎之处——这些阶段读写大量局部
   变量，因此函数签名必须刻意精心设计。
4. **finalize.py** —— 把阶段 6 抽成 `finalize_turn(...)`；它是最自成一体的块
   （仅记账，且已细分编号为 6.1–6.95）。
5. **runtime_attach.py** —— 阶段 3 + `_wrap_agentic_runtime_block`。
6. **loop.py** —— `_run_loop_blocking` + 其错误边界。
7. **turn.py** —— `process_user_turn` 剩下的部分就是编排器。

如果某个阶段难以干净抽出（相互依赖的局部变量过多），就停下来在本文中记录原因，
而非强行做一次有泄漏的拆分。

## 5. 向后兼容

所有代码都通过 `from openprogram.agent.dispatcher import process_user_turn`
（以及 `dispatch_forced_tool_call`、`TurnRequest`、`TurnResult`、
`trigger_compaction`）导入。包的 `__init__.py` 重新导出当前完整的公共接口面，
因此**所有调用方都无需改动**。在改动前后各做一次全仓库 grep
`from openprogram.agent.dispatcher import` / `dispatcher\.` 来验证——导入集合
必须完全一致。

## 6. 验证

每一步：对该包执行 `py_compile`，运行 `python -c "from openprogram.agent import
dispatcher; dispatcher.process_user_turn; dispatcher.dispatch_forced_tool_call"`，
执行 `openprogram worker restart` + `/healthz` 正常 + `tools_registered` 不变
（55），然后通过 webui 走一次真实聊天轮次（发送一条消息，得到流式回复，确认其在
刷新后仍持久存在）。现有触及 dispatcher 的单元测试必须保持全绿。不改变任何行为
断言——这只是结构调整。

## 7. 非目标

不改变轮次生命周期、错误分类体系、持久化 schema 或任何事件负载。不拆分
`runtime.py` / `server.py`（属于另外的工作项）。不在当前为阻塞式的路径上引入
async。
