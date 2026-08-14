# Turn Cancellation — 设计

> 一轮正在执行的对话如何被停止。本文是取消信号本身的权威说明：信号由哪个对象承载、
> 存活多久、哪些代码检查它。异步 Task 实体自身的生命周期（排队、worker 池、持久化）见
> [`async-job-lifecycle.zh.md`](async-job-lifecycle.zh.md)；一个 task 的取消使用这
> 里描述的机制。

## 1. 每轮一个 token

**一轮开启且只开启一个取消 token。轮内的一切都检查这同一个对象，轮结束时该 token 被
退役。**

停止的含义是触发*此刻*正在跑的那一轮的 token。已经结束的那一轮的 token 拒绝被触发，
所以迟到的停止无法波及下一轮。没有会话级标志需要重置，因为没有任何状态活得比一轮更
久：下一轮直接拿到另一个对象。

正是这个性质让设计成立，而不只是整洁。会话级的粘滞布尔量必须由结束这一轮的人清掉，
任何一条忘了清的路径——提前 return、被吞掉的异常、落在两轮之间的停止——都会污染下一
条消息。把信号的作用域收进一轮，是取消了清理义务，而不是多加一个需要记住的地方。

## 2. token

`openprogram/agent/run_control.py` 中的 `CancelToken`：

```
CancelToken:
  session_id   这一轮属于哪个会话
  turn_id      轮 id，调用方提供时才有
  event        一个 threading.Event，让被阻塞的 worker 线程可以等它
  retired      一旦为 True，cancel() 不做任何事
```

Event 是互操作面。worker 线程阻塞在它上面，dispatcher 把它桥接进 asyncio，provider 流
把它当作中止信号——三者都不需要知道 token 这层包装。

| 操作 | 含义 |
|---|---|
| `begin_turn(session_id, turn_id=None)` | 新开一个 token 并注册为该会话的当前 token。此时仍在注册表里的 token 属于已经结束的一轮，在这里退役。 |
| `end_turn(session_id, token=None)` | 退役该 token 并从注册表移除。传入 token 时只移除这一个，所以结束得晚的一轮绝不会把它的后继反注册掉。 |
| `current_token(session_id)` | 该会话上正在跑的那一轮的 token；两轮之间为 `None`。 |
| `token.cancel()` | 触发它。若该轮已结束则返回 `False`。 |
| `token.is_cancelled()` | 这一轮是否被停止。 |

## 3. token 的一生

```
begin_turn ──▶ running ──┬──▶ cancel()  ──▶ cancelled ──▶ end_turn ──▶ retired
                         └──────────────────────────────▶ end_turn ──▶ retired
```

token 在一轮开始时创建，在这一轮内是唯一的取消信号，并在轮结束时退役——无论这一轮是
成功、失败还是被停止。退役是单向的。退役之后 `cancel()` 返回 `False`，该 token 再也
无法影响任何东西。

两个竞态由构造本身消除：

- **停止与轮收尾竞争。** 它落在一个已退役的 token 上就此终止，而不会去置一个下一轮会
  读到的标志。
- **一轮在其后继开始之后才结束。** `end_turn` 只反注册传给它的那个 token，
  `begin_turn` 退役它顶掉的那个，所以两轮都无法退役对方的 token。

## 4. 谁检查 token

一轮之内的每一层都检查同一个对象。不存在第二个信号。

| 层 | 位置 |
|---|---|
| `@agentic_function` 入口与 `Runtime.exec` | `_cancel_hook`，导入时经 `add_pre_invocation_hook` 注册一次；抛 `CancelledError`。 |
| LLM 调用 | provider 调用前紧邻处的 `check_cancelled()`，转成 `ExecInterrupt("cancelled")`。 |
| agent 循环与流式输出 | token 的 Event，桥接成 `asyncio.Event` 后作为中止信号传给 `agent_loop`。 |
| 工具执行 | 同一个 Event，作为 `cancel` 参数交给 `tool.execute(...)`。 |
| 长耗时工具体 | 在重同步阶段之间调 `check_cancelled()`，让停止不必等到下一个函数边界才生效。 |
| 子任务与子 agent | 该 task 的 token；runner 经 `register_cancel_event` 注册它的 Event。 |

`CancelledError` 特意派生自 `BaseException`，这样工具体里的 `except Exception` 吞不掉
一次停止。

### 一个 frame 检查哪个 token

frame 先取上下文绑定的 token，只有在没有绑定时才回退到会话注册表。这在会话已经进入新
一轮、而旧 frame 仍然存活时至关重要：旧 frame 继续检查它自己那一轮的 token，因此针对
新一轮的停止不会中止它，而针对它自己那一轮的停止仍然有效。

## 5. 取消桥

dispatcher 在 worker 线程上用一个新建的 asyncio 循环跑 agent 循环，所以线程侧的 Event
必须传达到协程。一个桥接线程等待该 Event，并通过 `call_soon_threadsafe` 置位一个
`asyncio.Event`。

桥接线程同时监视一个轮结束 Event，由 drain 循环完成时释放。没有它，桥接线程会在
`cancel_event.wait()` 上停留到进程结束——每轮泄漏一个线程——并最终向一个已关闭的循环
投递任务。

## 6. 兼容性

公开名字保持原有含义，因此调用方与 WS 协议无需改动：

| 名字 | 现在的含义 |
|---|---|
| `mark_cancelled(session_id)` | 停止该会话上正在跑的这一轮。两轮之间为空操作。 |
| `is_cancelled(session_id)` | 当前这一轮是否被取消。轮一结束即为 `False`。 |
| `clear_cancel(session_id)` | 退役该会话的 token——这一轮结束了。 |
| `register_cancel_event(session_id, ev)` | 把调用方自己持有的 Event 接管为本轮的 token。 |

自己创建 Event 的调用点——聊天路径与 task runner——照旧这么做；
`register_cancel_event` 把它包成一个 token，并在同一次调用里退役上一轮的 token。

WS `stop` 动作的两阶段行为不变：先尝试优雅停止，宽限期后升级为触发 token、杀掉 exec
子进程、解除挂起提问的阻塞，并把仍在运行的行标记为 `cancelled`。

## 7. 记录下来的结果

用户取消在节点上写 `status = cancelled`，绝不写 `error`——status 词表见
[`../dag/overview.zh.md`](../dag/overview.zh.md)。被取消的一轮与其他任何一轮一
样按终态收尾：保留停止之前已经流式输出的内容，并被提交。

## 8. 不变量

1. 一轮只检查一个取消 token，轮内每一层检查的都是同一个。
2. 一次停止只影响它到达时正在跑的那一轮，永远不会影响后面的轮。
3. token 恰好退役一次，在轮结束时，与结果无关。
4. 没有任何取消状态活过一轮，因此没有任何清理路径负有重置义务。
5. 没有线程活得比启动它的那一轮更久。

## 附录：实现状态

已实现。`CancelToken`、`begin_turn`、`end_turn` 与 `current_token` 位于
`openprogram/agent/run_control.py`；取消桥在
`openprogram/agent/dispatcher/__init__.py`。测试：
`tests/component/runtime/test_turn_cancellation.py`。

暂停/恢复（`pause_execution` / `resume_execution`）是另一套全局的协作式机制，不属于每
轮一 token 的模型。`/api/stop` 先恢复再停止，这样被暂停的一轮也能被取消。

## 相关文件

- [`async-job-lifecycle.zh.md`](async-job-lifecycle.zh.md) — 异步 Task 实体
- [`../dag/overview.zh.md`](../dag/overview.zh.md) — status 词表、失败与重试
- [`../../error-handling.zh.md`](../../error-handling.zh.md) — 异常纪律
