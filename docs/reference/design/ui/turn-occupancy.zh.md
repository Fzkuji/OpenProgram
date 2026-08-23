# 轮次占用 — 停止、队列与 session 槽位

一个 session 同时只允许 **一个活着的 turn**。跑着的时候打字是排队，不是第二轮。停止是中断：占用在取消 *意图* 上释放，而不是等旧 turn 线程死掉。

相关：[`interaction-feedback.md`](interaction-feedback.md)（0ms UI）、
[`send-queue-reliability.html`](send-queue-reliability.html)（队列机制）。
代码：`use-chat-submit.ts`（`stopSession`）、
`server.py`（`_finish_owned_run` / `_try_reserve_run`）、
`run_control.py`（`cancel_execution`、`CANCEL_GRACE_S`）、
`providers/utils/cancelable_stream.py`。

## 排队 vs 中断（Claude Code）

Claude Code：turn 还在跑时 type+Enter = **排队**。Esc/停止先打断当前 turn，然后立刻把队列里的发出去。

Vercel AI SDK：`stop` **现在**就中止请求，并把 abort 转给模型。不要等下一个 token。

OpenProgram 两边都抄。发送队列已经会在 `runningTask` 存在时把打好的字停住。停止必须：

1. 先发 `execution.cancel`（或 `stop` 兜底）。
2. 把活着的 assistant 补成 `cancelled`（保留已流出的文字）。
3. `setRunningTaskFor(sessionId, null, "always")`，让队列在 0ms 出队。

把 `cancelling: true` 留在 running task 上，或 `drain: "never"`，会锁住输入框、干等到 `running_task_clear`。那是旧 bug。

## 占用在取消意图上释放

session 槽位是 `_running_tasks` **加上** active runtime。只 pop 任务表不够：`_is_run_active` 还会因 `_has_active_runtime` 返回 True。

`cancel_execution` 成功后：

- 把 `execution_id` 映射到 `session_id` + `msg_id`（聊天执行是 `{msg_id}_reply`）。
- 调用 `_finish_owned_run(session_id, msg_id)`，任务条目和 runtime 一起注销。
- 广播 `running_task_clear`，让其他客户端对齐。clear 必须带上结束那一轮的 `msg_id` / `execution_id`。

迟到的、属于旧 turn 的 clear / cancelled / result **不得**把新预占清成空闲，包括刚发出去、还没有 `msg_id` 的占位 `{ msg_id: "" }`。只有对上当前槽位的 execution 才认。stop-and-send 之后的无 id clear 一律当过期。

`msg_id` 对不上时 `_finish_owned_run` 是空操作 — 更新的预占不会被抢走。被取消的 turn 的 `finally` 也会再调一次，第二次是空操作。

同时退役这次 execution 的取消 token。占用是槽位，token 是停止旗标。cancelled 的 token 留在 `_current_tokens` 里，下一轮 `claim_cancel_event` 会失败，或者复用的 `{msg_id}_reply` 一上来就是已取消。旧流仍看着自己的 Event（`opts.signal`），那个已经被 set。

**不要**等 `process_user_turn` 返回才允许下一次 `_try_reserve_run`。

## 0ms UI

0ms 规则已经写了：停止立刻清掉 `runningTask` 并补丁 assistant。停止也在同一瞬间把发送队列出队。

迟到的、属于已取消 `execution_id` 的 `running_task` / `stream_event` 不得把任务救活，也不得再追加 token。消息状态 `cancelled`（或 `cancelling`）就是守卫。

`runningTask` 一旦是 null，`showStop` / `isCancelling` 为假，发送可用。这是预期。不要再为旧的收尾把发送禁用。

## HTTP 中止 — 不要等下一个 token

Anthropic 和 OpenAI Completions 以前是 `async for` SDK 流，然后才看取消信号。推理中途那就是好几秒。

`iter_until_cancelled` 每 ≤250ms 看一次取消信号，但 **同一条** `__anext__` 不能被超时取消。`wait_for` 一超时就会 cancel 这次读，httpx/OpenAI 的 SSE 迭代器会断。Grok 思考经常超过 250ms 没有分片，于是收成一条空的 completed 回复。取消时再退出，让 `async with` 关 HTTP 流。

`finish_reason` 就是这一轮的结束。不要死等 `[DONE]` 或后面的
`include_usage`：Grok/xAI 经常在最后一个 token 之后不关 SSE，界面就会一直显示还在回答。usage 只再短等一下（`USAGE_DRAIN_S`），然后发 `EventDone`。

## 4 秒宽限只给真子进程

`CANCEL_GRACE_S = 4.0` 保留。只在 owner 有子进程、或有会杀掉子进程的 terminate 钩子时生效（工具、process runner）。只有 token、没有进程、没有杀子进程钩子的聊天 owner，取消意图之后不得再等 4 秒。协作取消 + HTTP 中止就够了；占用已经释放。
