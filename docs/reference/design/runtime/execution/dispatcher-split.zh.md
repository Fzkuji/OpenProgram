# `agent/dispatcher` —— 按职责划分的包

> 本文描述聊天轮次执行路径的组织方式：为什么 dispatcher 是一个包而不是单个模块、
> 每个文件负责什么、限制代码位置的测试接缝，以及调用方依赖的兼容接口面。

`dispatcher` 是聊天轮次的真实执行路径。它是一个包而不是单个模块，遵循「单文件不
超过 1000 行」规则和「按职责划分模块目录」的层级代码结构约定。所有调用方
（webui / channels / CLI / task runner）都从 `process_user_turn` 进入。

## 1. 形态

`__init__.py` 只做编排：它持有 `process_user_turn`（入口 + 会话目标续跑循环）、
`_process_turn_once`（按编号阶段推进的流水线，每个阶段是对同级模块的一次具名调
用）以及 re-export 接口面。每个流水线阶段各有自己的模块。

## 2. 包结构

```
dispatcher/
  __init__.py        编排器：process_user_turn / _process_turn_once + 公共接口面
  types.py           _InheritParent、TurnRequest、TurnResult、INHERIT_PARENT
  prep.py            阶段 1-2：确保会话存在、解析 history（override / 分支回走 /
                     fork）、predecessor + memory prefetch、构造并持久化用户消息
  turn_context.py    阶段 3：TurnBindings —— 每轮 ContextVar（GraphStore、DAG
                     runtime、turn id、worktree cwd、deferred-tool 集合）+ 项目
                     自动提交基线快照；bind/release 成对
  stream_tap.py      对 on_event 的包裹：tool_result 事件流过时把每个完成的
                     工具行增量写入 DB
  loop_runner.py     阶段 4：run_loop_blocking —— 构建 AgentContext、snip /
                     自动压缩、运行 agent_loop 并排空其 EventStream
  persistence.py     阶段 5：持久化 assistant 消息
  finalize.py        阶段 6：head/token 记账、context-commit 回填、用量反馈、
                     自动标题、git + 项目提交、快照淘汰
  error_path.py      except 分支：把错误折叠进占位行（或写独立错误节点）、
                     finalize 失败轮次、taxonomy 分类、错误 TurnResult
  turn_writer.py     TurnWriter —— 包内唯一允许移动会话 head 的写者
                     （persist_user / open_placeholder / record_failure /
                     head_for_finalize）
  titles.py          _default_title、_maybe_auto_title、trigger_compaction
  forced_tool.py     dispatch_forced_tool_call（webui 强制单工具调用）
  runtime_attach.py  _wrap_agentic_runtime_block —— 把 @agentic_function 调用
                     渲染为 runtime-block 轮次
```

`_process_turn_once` 内的阶段编号：

```
1-2  prep.prepare_turn            会话 + history + 用户消息持久化
3    turn_context.TurnBindings    ContextVar 绑定（finally 中释放）
3b   turn_writer.open_placeholder assistant 占位行
4    loop_runner.run_loop_blocking（溢出时 reactive compact 重试）
5    persistence.persist_assistant_message
6    finalize.finalize_turn
7    最终 result 事件 + TurnResult
err  error_path.handle_turn_error
```

## 3. head 收敛不变量

`TurnWriter` 是本轮 head 去向的唯一决策者：它在用户消息持久化和失败记录时写
head，`head_for_finalize` 提供 `finalize.py` 在阶段 6 `update_session` 记账时
盖章的值。唯一例外是 `forced_tool.py`（强制单工具路径，绕过 agent loop，自己写
head）。其余 dispatcher 模块都不调用 `set_head`、不产生 `head_id` 值；
`error_path.py` 决定失败后*哪个*节点成为 head，但把写入委托给
`TurnWriter.record_failure`。

## 4. 限制代码位置的测试接缝

dispatcher 单测在**包**对象上 monkeypatch `D._resolve_model` /
`D._load_agent_profile` / `D._run_loop_blocking` / `D.process_user_turn`，并用
`orig = D._run_loop_blocking` 捕获原函数、注入假 `stream_fn` 跑真实循环。两条规
则保证这些接缝持续生效：

- `__init__.py` 的调用点以模块全局名引用接缝（`_run_loop_blocking(...)`、
  `_load_agent_profile(...)`），`patch.object(D, ...)` 的替换在调用时可见。
- 拆出的模块绝不用模块级 from-import 把接缝固化。`loop_runner.py` 在调用时经包
  属性解析 profile 与 model（函数内 `from openprogram.agent import dispatcher`，
  再 `dispatcher._load_agent_profile(...)`），同样的 patch 对真实循环生效。需要
  已解析 profile/model 但不在接缝下的模块（`finalize.py`）以显式参数接收——编排
  器在 patch 之下解析一次再传下去。

## 5. 兼容接口面

调用方通过 `from openprogram.agent.dispatcher import process_user_turn` 导入
（以及 `TurnRequest`、`TurnResult`、`dispatch_forced_tool_call`、
`trigger_compaction`、`approval_registry`，`process_runner.py` 用
`_wrap_agentic_runtime_block`）。包 `__init__.py` re-export 这一完整接口面，调
用方零改动。lazy import（重量级 provider / context 链）在所有模块里都留在函数体
内——webui 启动时 import 本包不会拉起它们。

## 6. 验证

对包跑 `py_compile`、import 冒烟检查（`dispatcher.process_user_turn`、
`dispatcher.dispatch_forced_tool_call`）、涉及 dispatcher 的单测全绿且无行为断
言改动，再用 grep 确认 §3 的 head 不变量（包内 `set_head` / 产生 head 的
`update_session(head_id=...)` 调用只出现在 `turn_writer.py` 与
`forced_tool.py`；`finalize.py` 只盖章 `TurnWriter.head_for_finalize` 递给它的
值）。

## 7. 非目标

拆分不改变轮次生命周期、错误 taxonomy、持久化 schema 或任何事件 payload。阻塞
路径不引入 async。

Owner: agent/runtime。
