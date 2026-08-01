# `agent/dispatcher` —— 按职责划分的包

> 本文描述 webui 聊天轮次执行路径的组织方式：为什么 dispatcher 是一个包而不是单个
> 模块、每个文件负责什么、限制代码位置的测试接缝，以及调用方依赖的兼容接口面。
> 实现状态见附录。

`dispatcher` 是 webui 聊天轮次的真实执行路径。在「禁止 1000 行以上文件」规则和
「层级化代码结构——按职责划分模块目录」约定下，它是一个包而不是单个模块。

## 1. 为什么要拆

作为单个模块时，`openprogram/agent/dispatcher.py` 有 1928 行。一个文件承载了整个
轮次的生命周期、两个约 300 行和 830 行的函数，以及所有轮次收尾的记账逻辑。这种形态
难以阅读、难以独立测试，且每新增一项关注点（一个记账步骤、一处持久化细节）都会让
同一个文件继续膨胀。

其中最大的单个函数是约 835 行的 `process_user_turn`。它本身已自带文档，分为七个编号
阶段，因此接缝是清晰的；只是这些接缝处于同一个函数内部，而非可分离的独立单元。

## 2. 拆分前的单模块结构

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

## 3. 包布局

`openprogram/agent/dispatcher/` 是一个包，每个文件承担单一职责，没有一个超过约
500 行：

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

`turn.py` 中的 `process_user_turn` 是一个编排器：加载 → 持久化用户消息 → 挂载
runtime → 运行循环 → 持久化助手消息 → 收尾 → 发出结果，每一步都是对兄弟模块中一个
具名函数的调用。错误分类（阶段 4，即循环的 `except`）与循环一起保留在 `loop.py`
中，与 `docs/reference/design/providers/reliability/error-taxonomy-propagation.md`
保持一致。

## 4. 限制代码位置的测试接缝

dispatcher 的单元测试在**包**对象上 monkeypatch 了 `D._resolve_model` /
`D._load_agent_profile` / `D._run_loop_blocking`，并捕获 `orig = D._run_loop_blocking`
以使用伪造的 `stream_fn` 运行真实循环。函数内部对辅助函数的查找会在*其所在*模块的
全局命名空间中解析，因此把 `_run_loop_blocking` 移到 `loop.py` 会使其对
`_resolve_model` 的调用错过 `D.*` 补丁，破坏约 40 个测试。由此有三条推论：

- 内部调用了被测试 patch 的辅助函数（`_run_loop_blocking`）的函数留在 `__init__.py`。
- 函数内部的各**阶段**（持久化、收尾）可以干净地抽出——做法是把已解析好的
  model 和 profile 作为显式参数传入：dispatcher 在补丁下解析它们一次，再向下传递，
  这样抽出的模块就永远不会调用被 patch 的辅助函数。
- 不触及任何被 patch 辅助函数的独立函数（`_wrap_agentic_runtime_block`）可以自由迁移。

因此把 `_run_loop_blocking` 移入 `loop.py` 需要一个补丁稳定的辅助函数接缝（调用时
通过 `_model_tools.<fn>` 访问），或者更新测试的 patch 目标。这是一次单独的改动，
不并入代码搬移的 commit。

## 5. 向后兼容

调用方通过 `from openprogram.agent.dispatcher import process_user_turn`（以及
`dispatch_forced_tool_call`、`TurnRequest`、`TurnResult`、`trigger_compaction`）
导入。包的 `__init__.py` 重新导出原模块完整的公共接口面，因此**所有调用方都无需
改动**。任何一次搬移前后各做一次全仓库 grep
`from openprogram.agent.dispatcher import` / `dispatcher\.`，导入集合必须完全一致。

## 6. 验证

每次搬移的验证方式：对该包执行 `py_compile`，运行 `python -c "from
openprogram.agent import dispatcher; dispatcher.process_user_turn;
dispatcher.dispatch_forced_tool_call"`，执行 `openprogram worker restart` 并确认
`/healthz` 正常、`tools_registered` 不变（55），然后通过 webui 走一次真实聊天轮次
（发送一条消息，得到流式回复，确认其在刷新后仍持久存在）。现有触及 dispatcher 的
单元测试保持全绿，且不改变任何行为断言——这只是结构调整。

## 7. 非目标

这次拆分不改变轮次生命周期、错误分类体系、持久化 schema 或任何事件负载；不拆分
`runtime.py` / `server.py`（属于另外的工作项）；也不在当前为阻塞式的路径上引入
async。

## 附录：实现状态

搬移一次一个 commit，纯代码搬移，须在编译 + 导入 + worker-restart-healthz 全绿之后
再进行下一次。顺序按爆炸半径从小到大：先 `types.py`（三个 dataclass + sentinel，
没有内部依赖），再 `titles.py` + `forced_tool.py`（叶子级辅助函数，调用方很少），
再 `persistence.py`（阶段 2 与 5 抽成 `persist_user_turn(...)` /
`persist_assistant_message(...)`，接收显式参数，不闭包捕获 `process_user_turn` 的
局部变量——这些阶段读写大量局部变量，函数签名需要精心设计），再 `finalize.py`
（阶段 6 抽成 `finalize_turn(...)`，是最自成一体的块），再 `runtime_attach.py`，
再 `loop.py`，最后 `turn.py` 剩下的部分就是编排器。若某个阶段因局部变量相互依赖
而难以干净抽出，就保持原位并在本文记录原因，而非强行做一次有泄漏的拆分。

已落地：删除死代码（`_legacy_dispatch_forced_tool_call_unused`）、建包、`types.py`、
`titles.py` + `forced_tool.py`、`runtime_attach.py`（`_wrap_agentic_runtime_block`）、
`finalize.py`（阶段 6）、`persistence.py`（阶段 5 助手消息持久化）。`__init__.py`
现为 1234 行，原 1928 行。`turn.py` 与 `loop.py` 尚未抽出；`loop.py` 依赖于先解决
§4 的测试接缝问题。

负责人：agent/runtime。
