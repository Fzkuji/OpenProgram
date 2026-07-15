# 模型调用的错误处理与重试决策逻辑

本文档描述一次 `@agentic_function` 内的模型调用从发起到失败/成功之间,系统在每一层"做选择"的逻辑:什么时候重试、什么时候放弃、什么时候判定为永久错误。

## 调用链

一次 `runtime.exec()` 触发模型调用,自上而下经过:

```
@agentic_function 内 runtime.exec()
  └─ Runtime.exec() ............. 重试循环(本层)
       └─ _call → _call_via_providers
            └─ AgentSession.run() .... 自动重试层
                 └─ agent.run() ....... try/except 包装,产出 AssistantMessage
                      └─ agent_loop ... provider 流式 HTTP
```

错误在两个地方"成形":
1. `agent_loop` 里抛出的异常,被 `agent.py` 捕获,产出一条 `stop_reason="error"` 的 `AssistantMessage`。
2. provider 流自己发出 `error` 事件,`agent_loop` 直接把它当 `final_message`。

两种情况下游都看到同一个东西:一条 `stop_reason="error"` 的消息,带一个 `error_message`(可能为空)。

## 第一层:`AgentSession` 自动重试

实现:`openprogram/agent/session.py::_run_with_retry` + `openprogram/agent/retry.py`。

配置 `RetrySettings(enabled=True, max_retries=3, base_delay_ms=2000)`。每次 `run()` 后检查最后一条 assistant 消息:

- `is_retryable_error(msg)` 为 `True` 且未超 `max_retries` → 退避后重试。
- 退避 `compute_backoff_ms(attempt) = base * 2^(attempt-1)` → 2s、4s、8s。
- 重试前把上一条失败的 assistant 消息从历史里弹掉(`replace_messages(msgs[:-1])`),避免污染下一次 prompt。

`is_retryable_error` 的判定顺序:

```
stop_reason != "error"            → False(不是错误,不重试)
context overflow                  → False(交给 compaction,不是重试能解决的)
error_message 为空                → True
error_message 命中 _RETRY_PATTERN → True,否则 False
```

`_RETRY_PATTERN` 匹配:`overloaded`、`rate limit`、`429`、`5xx`、`service unavailable`、`connection error/refused`、`other side closed`、`fetch failed`、`reset before headers`、`terminated` 等瞬时故障特征。

**关键设计点:`error_message` 为空时判定为可重试。** provider 流中途断开(连接重置、SSL EOF、网关抖动)时,往往还没收到结构化的错误体就断了,`error_message` 是空字符串。如果空消息走正则匹配,匹配不到任何模式 → 判"不可重试" → 这一层重试根本不触发 → 错误一路下沉,最终在 `runtime.py` 兜底成不透明的 `"Agent session failed"`。把空消息直接判为可重试,正是堵这个洞:内容为空的错误几乎必然是流断了,而流断属于瞬时故障。

## 第二层:`Runtime.exec()` 重试循环

实现:`openprogram/agentic_programming/runtime.py::Runtime.exec` / `async_exec`。

构造参数 `max_retries=3`(默认)。`_call` 抛异常时:

```
TypeError / NotImplementedError → 直接 raise(编程错误,不重试)
_is_permanent_error(e)          → 直接 raise,标注 "failed permanently"
attempt == max_retries - 1      → raise,标注 "failed after N attempts"
其余                            → time.sleep(_RETRY_BACKOFF * 2^attempt) 后重试
```

退避 `_RETRY_BACKOFF=1.5`,即 1.5s、3s、6s。

**永久错误判定** `_is_permanent_error`:把 `类型名: 异常文本` 转小写,匹配 `_PERMANENT_ERROR_MARKERS` 任一子串:

```
not a valid image / invalid image / image data is not   ← 请求体里的图像损坏
login expired / login failed / re-auth / unauthorized    ← 网关侧鉴权失效
invalid api key / invalid_api_key                        ← 凭证错误
```

这类错误下一次完全相同的请求会以完全相同的方式失败,重试只是白白消耗次数和墙钟时间,所以一旦命中立即放弃,并在错误信息里写明 "permanently",和"重试 N 次后失败"区分开,便于排查。

## 两层重试是相乘的

第一层(AgentSession)和第二层(exec)各自独立计数。最坏情况:exec 重试 3 次,每次内部 AgentSession 又重试 3 次 = 9 次实际 API 调用,加退避总耗时可达数十秒才最终失败。

这是历史叠加的结果:AgentSession 那层是早就有的通用重试,exec 那层是 Runtime 自己的保护。两层都保留时,职责重叠。如果确认 AgentSession 那层已稳定生效,exec 层可以降到 `max_retries=1`(不重试),让重试职责单一化。当前两层都开。

## 边界:重试解决不了的

- **网关鉴权失效**(`openai-codex` login expired/failed):属于永久错误,exec 层命中 `_PERMANENT_ERROR_MARKERS` 后立即放弃。跑批前必须保证鉴权有效,重试不是补救手段。
- **上下文溢出**:`is_retryable_error` 显式排除,应由 compaction 处理。
- **编程错误**(`TypeError` / `NotImplementedError`):函数签名/实现错了,exec 层直接抛。

## 错误信息的可诊断性

`agent.py` 捕获异常时,`err_text = f"{type(err).__name__}: {err}"`(异常文本为空时退化为只有类型名),并把 traceback 打到 stderr。`runtime.py` 的兜底信息不再是裸的 `"Agent session failed"`,而是带上每次 attempt 的 `类型名: 异常文本` 列表和失败原因(permanently / after N attempts)。
