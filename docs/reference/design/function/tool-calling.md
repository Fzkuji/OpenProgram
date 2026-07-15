# 模型选择下一步的逻辑(工具调用循环)

本文档描述一次模型调用里,LLM 如何在每一轮"做选择"——是选一个函数去运行,还是输出文本结束。

> 配套文档:[`function-calling-unification.md`](./function-calling-unification.md)
> 描述整个 function-calling 框架的设计 —— `@function` / `@agentic_function`
> 两个装饰器、共享注册表、6 层 gating、deferred loading 等。本文只讲
> "选下一步" 这一步的循环机制。

## 一句话概括

给 LLM 一组工具(`@agentic_function` 或工具 dict),它每轮返回一条 assistant 消息。消息内容里**有 `ToolCall` 就是选了函数**,框架去执行、把结果喂回历史、让它再选一轮;**只有文本没有 `ToolCall` 就是选了"结束"**,把文本作为最终回复返回。这个循环跑在 `openprogram/agent/agent_loop.py::_run_loop`。

## 入口:`runtime.exec`

`@agentic_function` 内调 `runtime.exec(content, tools=..., tool_choice=..., max_iterations=...)`:

- `tools` 是 LLM 可选的函数清单。每项可以是 `@agentic_function`、`{"spec":..., "execute":...}` dict、或带 `.spec`/`.execute` 的对象。
- **工具是 opt-in 的。** 不传 `tools=` 也不传 `toolset=` 时,LLM 拿到的工具是 `None`——这是一次纯推理调用,LLM 无函数可选,只能输出文本。要让它能"选函数",必须显式传 `tools=[...]` 或 `toolset="default"`。
- 传了 `tools` 时,`exec` 进入工具循环,直到模型返回纯文本,或撞上 `max_iterations`(默认 20)。

`tool_choice` 控制这一轮"允不允许选 / 必须不必须选":

```
"auto"(默认)                      模型自己决定调不调、调哪个
"required"                          这一轮必须选一个函数,不许直接输出文本
"none"                              这一轮不许选函数,只能输出文本
{"type":"function","name":"X"}      强制选 X 这个函数
```

`parallel_tool_calls`(默认 `True`)允许模型一轮里一次选多个函数。

## 循环主体:`_run_loop`

`_run_loop` 里有一个内层 while `has_more_tool_calls or pending_messages`,每一轮:

1. **拿模型这一轮的输出** — `_stream_assistant_response` 流式调 provider,返回一条 `AssistantMessage`。
2. **判终止错误** — `message.stop_reason in ("error","aborted")` → 直接结束流,不再循环。
3. **检查模型选了什么** —
   ```python
   tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
   has_more_tool_calls = len(tool_calls) > 0
   ```
   - `tool_calls` 非空 → 模型选了函数 → 走第 4 步,然后回到循环顶再选下一轮。
   - `tool_calls` 为空 → 模型这一轮只输出了 `TextContent` → `has_more_tool_calls=False` → 内层 while 退出 → 这条文本就是结果。
4. **执行选中的函数** — `_execute_tool_calls` 逐个跑,产出 `ToolResultMessage`,追加进 `current_context.messages` 和 `new_messages`。下一轮 LLM 看到的历史里就带上了工具结果,据此决定再选什么。

也就是说:**"选下一步"不是一个独立的决策模块,而是 provider 返回的 assistant 消息里 `ToolCall` 与 `TextContent` 的二选一。** 框架不替模型决定,只解析它的输出并据此分流。

## 函数执行:`_execute_tool_calls`

对模型选中的每个 `ToolCall`,按 `tool_call.name` 在 `tools` 里找对应工具:

```
找不到工具                          → ValueError,产出 is_error 的结果
validate_tool_arguments 校验失败    → 异常,产出 is_error 的结果
tool.execute(...) 抛异常            → 捕获,异常文本作为 is_error 结果
正常                                → 结果内容包进 ToolResultMessage
```

校验和执行的异常都不会中断循环——它们变成一条 `is_error=True` 的工具结果喂回模型,让模型看到"这个函数选错了/参数错了"并自行纠正。

并行多选时按顺序逐个执行。中途若 `get_steering_messages` 返回了用户插入的新消息,剩余未执行的 `ToolCall` 被 `_skip_tool_call` 标记为 "Skipped due to queued user message",优先处理用户消息。

## 终止条件

内层选择循环在以下任一情况停:

```
模型这一轮没选函数(纯文本)         正常结束,文本即结果
stop_reason = error / aborted       异常/取消结束
inner_iterations > 50               硬上限 MAX_INNER_ITERATIONS,防模型空转;
                                    按"正常结束"处理,返回已有内容
exec 层 max_iterations(默认 20)    exec 自己的工具循环安全帽
```

内层退出后,`get_follow_up_messages` 若有后续消息则把它们设为 `pending_messages` 再开一轮;没有就彻底结束,推 `AgentEventAgentEnd`。

## 与 `@agentic_function` 的关系

被当作工具传给 `exec(tools=[...])` 的 `@agentic_function`,在模型眼里就是一个可选函数。模型选中它 → `_execute_tool_calls` 调它的 `.execute` → 这个函数内部如果又调 `runtime.exec`,就再开一层同样的选择循环。"选下一步要运行的函数"在多层 agentic function 嵌套下是同一套机制递归展开。
