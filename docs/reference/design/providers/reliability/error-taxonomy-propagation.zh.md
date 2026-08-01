# 错误分类传播 —— 把结构化的 LLM 错误一路传到 UI

`reason`、`retryable`、`retry_after_s` 从 provider 失败一路传到 chat-turn 错误
事件，使 UI 渲染出一个分类清晰、可操作的错误，而不是一个不透明的字符串。
它建立在 `openprogram/providers/utils/errors.py` 已有的分类体系之上。

## 1. 为什么这套结构必须一路留存

分类体系位于 **provider-stream** 层：`providers/utils/errors.py` 定义了
`ErrorReason`（`transport / rate_limit / authentication / authorization /
context_length / content_policy / invalid_request / provider_internal /
unknown`）、一个携带 `reason` + `retry_after_s` 的 `LLMError`，以及
`classify(exc) -> (reason, retryable)`。`stream_retry` 用它来驱动退避。

如果在该层之上把结构压扁成 `str(exc)` —— 无论是 agent 循环捕获失败，还是把
chat-turn 错误事件塑造成 `{"type": "error", "content": "<string>"}` —— UI 就
无法区分：

- 一个 **rate limit**（可重试 —— 显示 "retrying in Ns"，甚至自动重试）与
- 一个 **auth** 失败（致命 —— "check your API key / re-login"）与
- 一个 **context-length** 溢出（致命 —— "the conversation is too long; compact
  or start a new chat"）与
- 一个临时性的 **provider_internal**（可重试）之间的差别。

于是每个失败看起来都是同一个红色字符串，而且因为没有任何环节知道这是哪一类失败，
也就无法给出对应的操作入口。

范围是**主 chat-turn 流式错误**。那些操作性错误字符串（重试与压缩失败的消息）
保持纯文本。

## 2. 设计

1. **在 agent 错误边界处分类。** 在 agent turn 捕获 stream 失败的地方，如果它是
   一个 `LLMError`，就使用它的 `reason` / `retry_after_s`；否则运行
   `errors.classify(exc)` 来推导出 `(reason, retryable)`。把这些信息带到 agent
   对外暴露的错误上（一个小的结构化错误对象，而不是一个裸字符串）。
2. **扩展 chat-turn 错误事件。** webui 的错误负载变为
   `{"type": "error", "content": <human string>, "reason": <ErrorReason>,
   "retryable": <bool>, "retry_after_s": <float|null>}`。`content` 保留以向后
   兼容；新字段是增量式添加的。
3. **前端按 reason 渲染。** 一个分类清晰的错误 chip 将 reason 映射到
   可操作的文案 + 交互能力：
   - `rate_limit` → "Rate limited — retrying in {retry_after_s}s"（并且，如果
     存在重试策略，给出一个自动重试/▸ 倒计时）。
   - `authentication`/`authorization` → "Your {provider} key was rejected —
     check it in Settings → Providers."
   - `context_length` → "This conversation is too long — compact it or start a
     new chat."
   - `content_policy` → "The provider blocked this request (content policy)."
   - `provider_internal`/`transport` → "Temporary provider/network error — try
     again."（可重试样式）
   - `invalid_request`/`unknown` → 原始 `content`（兜底）。

## 3. 分类发生在哪里

一次聊天失败会在三个层次被捕获，每一处都经 `taxonomy_fields` 分类并发出
`reason / retryable / retry_after_s`：

- `agent.py` —— `Agent` 类边界，供 Agent 运行使用。
- `_execute/__init__.py` 的外层 except —— 动作级错误。
- `dispatcher.py` —— webui chat turn 的公共路径，它经由 dispatcher 的
  `_run_loop_blocking` 运行。失败在 dispatcher 自己的 except 中被捕获，reason
  经 `TurnResult`（`error_reason` / `error_retryable` / `error_retry_after_s`）
  流入 dispatcher 的运行中错误事件与运行后的 `chat.py` 广播。

前端一侧是 `assistant-bubble.tsx`，它按 `errorReason` 渲染分类标题，下方附上
原始消息；`ChatResponseData` 与 `ChatMsg` 携带这些字段，`finalize()` 捕获它们。

后端边界处的分类本身就有价值：API 消费方、日志以及其他 channel 都会读取 reason，
与聊天 UI 如何呈现它无关。

## 4. 验证

诱发每一种 reason，确认 WS payload 的 `reason` / `retryable` 与 UI 渲染：
被拒绝的 key 得到 `authentication`、致命、"check your key"；429 得到
`rate_limit`、可重试、带重试提示；超长上下文得到 `context_length`、致命、
"compact"。`errors.classify` 有覆盖该映射的单元测试，另有一条测试确认 agent
边界原样保留 `LLMError` 的 reason。

端到端复现一个确定性的 provider 失败是麻烦的地方：前端发送的是它自己选中的模型
而非 agent 默认模型，因此改 agent 模型并不会改变实际被触发的路径。要确认实时
渲染，需要在选中的那个模型上制造一个可重复的失败 —— 过期的 key 对应 `auth`，
或一个会 503 的 OpenRouter `:free` 模型对应 `provider`。

## 5. 局限

持久化的错误节点只携带字符串，不含 reason；分类信息走的是实时广播。若要在重新
加载后仍渲染分类错误，存储的节点也需要携带这套分类。

## 6. 非目标

不是要重写那约 991 处 `except Exception` 站点 —— 只有 chat-turn LLM 错误路径会
被分类并对外暴露。那次全面的 blanket-except 审计是另一回事。也不是要改动自动重试
策略；这里只是把 `retryable`/`retry_after_s` *暴露*出来，以便 UI（以及任何未来的
策略）可以据此行动。
