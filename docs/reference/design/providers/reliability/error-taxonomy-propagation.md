# 错误分类传播 —— 把结构化的 LLM 错误一路传到 UI

状态：**agent 边界 + 模型已落地 (079e0072)** · webui emit + 前端待办 · 负责人：providers/agent/webui · 创建于：2026-06-04

优化路线图条目："把结构化的 LLMError 分类体系向上传播到 provider 层之上"。基于
`openprogram/providers/utils/errors.py` 中已有的分类体系构建。

## 1. 问题

在 **provider-stream** 层已经存在一套丰富的、opencode 风格的错误分类体系：
`providers/utils/errors.py` 定义了 `ErrorReason`
(`transport / rate_limit / authentication / authorization / context_length /
content_policy / invalid_request / provider_internal / unknown`)、一个携带
`reason` + `retry_after_s` 的 `LLMError`，以及 `classify(exc) -> (reason, retryable)`。
`stream_retry` 用它来驱动退避（backoff）。

但在 stream 层**之上**，这套结构就被丢弃了：
- agent 循环捕获到失败后，将其压扁成 `str(exc)`；
- webui 的 chat-turn 错误事件是 `{"type": "error", "content": "<string>"}`
  (`ws_actions/chat.py`)；
- WS 连接处理器原本会记录一段原始 traceback（已修复）。

因此 **UI 无法区分**：
- 一个 **rate limit**（可重试 —— 显示 "retrying in Ns"，甚至自动重试）与
- 一个 **auth** 失败（致命 —— "check your API key / re-login"）与
- 一个 **context-length** 溢出（致命 —— "the conversation is too long; compact
  or start a new chat"）与
- 一个临时性的 **provider_internal**（可重试）之间的差别。

每个失败看起来都是一个不透明的红色字符串。这是错误 UX 中最大的一处缺口。

## 2. 目标

把 `reason` / `retryable` / `retry_after_s` 从 provider 失败一路传到 chat-turn
错误事件，并在 UI 中渲染出一个**分类清晰、可操作**的错误。范围仅限于**主
chat-turn 流式错误**本身 —— 那些操作性错误字符串（重试/压缩失败的消息）保持纯文本。

## 3. 设计

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

## 4. 迁移

1a. **（已完成，079e0072）** 在 agent 错误边界处分类 ——
   `errors.taxonomy_fields(exc)` + 新增的 `AssistantMessage.error_reason /
   error_retryable / error_retry_after_s` 字段。已有单元测试（LLMError
   透传、通用错误分类）。
1b. **（已在全部三个 emit 点落地；尚未捕获到实时渲染）** 一个聊天失败会在三个
   层级被捕获，现在全部通过 `taxonomy_fields` 分类并 emit
   `reason / retryable / retry_after_s`：
   - `agent.py`（`Agent` 类边界，079e0072）—— 供 Agent run 使用。
   - `_execute/__init__.py` 外层 except (5efc95ab) —— action 级别的错误。
   - **`dispatcher.py` (5c17b848) —— 真正的公共路径。** webui 的聊天 turn 通过
     dispatcher 的 `_run_loop_blocking` 运行，其失败在 dispatcher 自己的 except
     中被捕获；reason 经由 `TurnResult`
     (`error_reason/error_retryable/error_retry_after_s`) 同时流入运行中的
     dispatcher 错误事件和运行后的 `chat.py` 广播。
   **仍未验证：** 一次实时的分类渲染。强制制造一个确定性的 provider 错误受阻于：
   前端使用它**自己**选中的模型（codex）而非 agent 默认模型，且 codex 表现不稳定
   （有时 401，有时成功）。更换 agent 模型并不会改变前端发送的内容。需要在选中的
   模型上用一次真实、可复现的 provider 失败来确认。注意：持久化的错误节点只携带
   字符串（不含 reason）—— 只有实时广播才携带；要在重新加载时渲染分类错误，需要
   DB 节点也携带分类信息（未来）。
2. **（已落地，可编译；尚未捕获到实时渲染，e5f95445）** assistant 气泡
   (`assistant-bubble.tsx`) 根据 `errorReason` 渲染一个分类标题
   （rate_limit → 重试提示，auth → 检查密钥，context → 压缩，
   provider/transport/timeout → 临时性），下方附上原始消息；
   `ChatResponseData` + `ChatMsg` 携带这些字段，`finalize()` 捕获它们。编译干净；
   正常路径的聊天未回归。**仍未验证：** 一次实时的分类错误渲染 —— 本次会话未能
   强制制造出一个确定性的 provider 失败（codex 一直成功；模型选择器 / 原始 WS /
   store 注入各自都太繁琐）。可通过命中一个真实错误来确认（过期密钥 → "auth"；
   一个会 503 的 OpenRouter `:free` 模型 → "provider"），或临时把
   `default_model` 设为一个会 503 的模型。

每一步单独 commit；即使前端尚未落地，后端本身也是有用的（API 消费者、日志、未来的
channels）。

## 5. 验证

诱发每一种 reason，并确认 WS 负载的 `reason/retryable` + UI 渲染：一个被拒绝的
密钥 → `authentication`，致命，"check your key"；一个 429 → `rate_limit`，可重试，
重试提示；一个超大的 context → `context_length`，致命，"compact"。
`errors.classify` 已经有了对该映射的单元覆盖；再加一个测试，验证 agent 边界会
原样保留 `LLMError` 的 reason。

## 6. 非目标

不是要重写那约 991 处 `except Exception` 站点 —— 只有 chat-turn LLM 错误路径会
被分类并对外暴露。那次全面的 blanket-except 审计是另一回事。也不是要改动自动重试
策略；这里只是把 `retryable`/`retry_after_s` *暴露*出来，以便 UI（以及任何未来的
策略）可以据此行动。
