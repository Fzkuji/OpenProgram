# 工具调用循环

本文描述在一次模型调用内，LLM 在每一轮如何"做出选择"——挑选一个函数去执行，或者输出文本并结束。

> 配套文档：[`function-calling-unification.md`](../../../reference/design/function/function-calling-unification.md)
> 介绍了整个函数调用框架的设计——`@function` / `@agentic_function` 装饰器、共享注册表、6 层
> 门控、延迟加载等。本页只涵盖"挑选下一步"这部分的循环机制。

## 一句话总结

给 LLM 一组工具（`@agentic_function` 或工具 dict）；每一轮它返回一条 assistant 消息。如果消息内容
**包含 `ToolCall`，说明它挑选了一个函数**——框架执行该函数，把结果回灌进历史，然后让它再次挑选。如果消息
**只有文本、没有 `ToolCall`，说明它选择了"结束"**——这段文本作为最终回复返回。该循环运行在
`openprogram/agent/agent_loop.py::_run_loop` 中。

## 入口：`runtime.exec`

在 `@agentic_function` 内部，调用
`runtime.exec(content, tools=..., tool_choice=..., max_iterations=...)`：

- `tools` 是 LLM 可挑选的函数菜单。每一项可以是
  `@agentic_function`、`{"spec":..., "execute":...}` dict，或带有
  `.spec` / `.execute` 的对象。
- **工具默认开启。** 如果既不传 `tools=` 也不传 `toolset=`，`exec`
  会解析出**完整**的注册表工具集，任何函数不必逐个声明就能搜索、跑代码、改文件。
  确实不想要工具的调用要用 `toolset="none"`（或 `tools=[]`）显式退出——只有这时
  才是纯推理调用，模型只能输出文本。工具体内部嵌套的 `exec` 会继承外层调用的
  `tools=` 列表（通过 `_current_tools` contextvar）。
- 要裁剪工具菜单，`exec` 还接受策略参数
  `tools_source`、`tools_allow` 和 `tools_deny`。
- 设置了 `tools` 后，`exec` 进入工具循环，直到模型返回纯文本（或触及循环的硬上限——见 [终止](#termination)）。

`tool_choice` 控制每一轮是否允许 / 要求挑选——
`"auto"`（默认：由模型决定）、`"required"`（必须挑选一个函数）、
`"none"`（仅文本），或 `{"type": "function", "name": "X"}` 强制某个函数。它会被转发给 provider，由后者映射到自身的协议形态（已覆盖 OpenAI、Anthropic、Gemini 和 Bedrock）。
在 provider 支持该开关的情况下，`parallel_tool_calls=False` 禁止在一轮内进行多次挑选。`max_iterations` 限制循环的轮数——实际上限是
`min(50, max_iterations)`，下限为 1（见
[终止](#termination)）。对于一次强制的、结构化的决策*结尾*（而非逐轮控制），`exec(choices=...)` 仍是更丰富的工具——见 [下一步决策](./next-step-decision.md)。

## 循环主体：`_run_loop`

`_run_loop` 有一个内层 `while has_more_tool_calls or pending_messages`；每一轮：

1. **获取模型本轮的输出**——`_stream_assistant_response`
   从 provider 流式读取并返回一条 `AssistantMessage`。
2. **检查终止性错误**——`message.stop_reason in ("error",
   "aborted")` → 立即结束流，不再循环。
3. **查看模型挑选了什么**——
   ```python
   tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
   has_more_tool_calls = len(tool_calls) > 0
   ```
   - `tool_calls` 非空 → 模型挑选了函数 → 进入第 4 步，然后回到循环顶部再次挑选。
   - `tool_calls` 为空 → 模型本轮只输出了 `TextContent` →
     `has_more_tool_calls=False` → 内层 while 退出 → 这段文本就是结果。
4. **执行被挑选的函数**——`_execute_tool_calls` 逐个执行它们，产出
   `ToolResultMessage` 并追加到
   `current_context.messages` 和 `new_messages`。LLM 下一轮看到的历史此时已带上工具结果，它据此决定下一步挑选什么。

换句话说：**"挑选下一步"并不是一个独立的决策模块——它就是 provider 返回的 assistant 消息内部 `ToolCall`
与 `TextContent` 的二选一。** 框架从不替模型做决定；它只解析输出并据此分支。

## 函数执行：`_execute_tool_calls`

对模型挑选的每一个 `ToolCall`，按
`tool_call.name` 在 `tools` 中查找该工具：

```
tool not found                        → ValueError, produces an is_error result
validate_tool_arguments fails         → exception, produces an is_error result
tool.execute(...) raises              → caught; the exception text becomes an is_error result
success                               → result content wrapped in a ToolResultMessage
```

校验异常和执行异常都不会中断循环——它们会变成一个回灌给模型的
`is_error=True` 工具结果，使模型能看到"函数错了 / 参数错了"并自行纠正。

并行挑选按顺序依次执行。如果中途 `get_steering_messages`
返回了用户排队的消息，则剩余未执行的 `ToolCall`
会被 `_skip_tool_call` 标记为 "Skipped due to queued user message"，用户消息优先处理。

## 终止

内层挑选循环在以下任一情况下停止：

```
model picked no function (pure text)   normal finish; the text is the result
stop_reason = error / aborted          error / cancel finish
inner_iterations > 50                  hard cap MAX_INNER_ITERATIONS against idle spinning;
                                       treated as a normal finish, returns what exists
```

有一个容易忽略的延续条件：内层 `while` 也会因
`pending_messages` 而继续运行，所以即便在一次纯文本回复之后，排队的用户（steering）消息仍会让循环存活。

内层循环退出后，`get_follow_up_messages` 可能提供后续消息，这些消息成为下一轮的
`pending_messages`；否则本次运行彻底结束并推送 `AgentEventAgentEnd`。

## 与 `@agentic_function` 的关系

作为工具传给 `exec(tools=[...])` 的 `@agentic_function`，在模型眼中只是一个可挑选的函数。模型挑选它 →
`_execute_tool_calls` 调用其 `.execute` → 如果该函数体内又调用了
`runtime.exec`，则同一挑选循环的又一层被打开。
在嵌套的 agentic function 下，"挑选下一个要运行的函数"就是同一机制的递归展开。
