# 候选标题

1. 想让 Agent 主动干活，先把"发生了什么"变成一条事件流
2. OpenProgram 的事件基础设施：给 proactive agent 打的地基
3. 一条事件总线，为什么是主动式 Agent 的前提

---

现在的 agent 都是被动的：你说一句，它做一件事，做完就停。大家都想要下一步——agent 在合适的时机自己动起来：文件变了主动检查、上下文快满了提前压缩、用户在 Discord 留了言就接手处理。这类需求叫 proactive agent。

我们在做 OpenProgram（一个开源的通用 agent harness）的时候发现，proactive 的难点不在策略，而在基础设施。你想在"某个时刻做某件事"，前提是框架得能告诉你"这个时刻到了"。而大部分框架内部根本没有这样一条统一的通知渠道。

## 问题：六套互不相通的机制

拿 OpenProgram 自己重构前的状态举例。"发生了什么"这个信号散落在六个互不连通的机制里：agent loop 有自己的 AgentEvent 流，auth 模块用私有的 `_emit`，context 引擎走 on_event 回调，channels 靠 WebSocket 广播，memory 是定时轮询，存储层则只有普通日志。

想在"某个时刻"挂一段逻辑，你得先搞清楚这个时刻归哪个机制管、它的钩子怎么接。六个模块六种接法。这种状态下谈 proactive 没有意义——策略层还没写，光是拿到信号就要侵入六处内部实现。

另一个常见做法是轮询：起个定时器，隔几秒查一遍状态。轮询的问题很实际。查得勤，浪费；查得疏，错过时机。而且很多信号根本没地方查——"模型刚生成完一次回复"这种瞬时事件，不推送就是丢了。

## 做法：一条总线，一个信封

OpenProgram 的答案是一条进程级的统一事件总线。所有子系统——agent loop、auth、context、channels、memory——都往同一条总线上发事件，用同一个信封：

```python
@dataclass(frozen=True)
class Event:
    id: str          # 唯一 id
    ts: float        # 发生时间
    type: str        # 事件类型
    origin: str      # 谁触发的：user / agent / tool / system / proactive
    payload: dict    # 内容：命令、文件路径、哪个账号被限流……
    metadata: dict   # 开放口袋：session、turn 等关联信息，按需填
```

订阅方不关心事件从哪个模块来，只按类型订：

```python
from openprogram.events import get_event_bus

get_event_bus().subscribe(                       # 返回一个取消订阅函数
    lambda e: alert(e.payload),
    types={"context.compaction_recommended", "file.changed"},
)
```

两行代码，你就同时监听了"上下文快满了"和"有文件被改了"。这两个信号一个来自 context 引擎，一个来自 write/edit 工具，在旧结构里要接两套完全不同的钩子。

## 总线上有什么

目前注册了二十多种事件类型，覆盖 agent 运行的关键时刻。挑几个说：

- `tool.before` / `tool.after`：每次工具调用前后。前者还是一个"闸门"事件——订阅者可以返回一个理由否决这次调用，理由会作为错误结果传回给模型。
- `turn.start` / `turn.end` / `turn.stop`：一轮对话的开始、结束，以及"agent 想停下来"的时刻。`turn.stop` 也是闸门：订阅者不同意，agent 就得继续干。
- `context.compaction_recommended`：上下文占用超过预算比例，该压缩了。
- `file.changed`：write、edit、apply_patch 工具落盘了。
- `channel.message_inbound`：外部渠道来消息了。channels 层接的是 Telegram、Discord、Slack、微信，它们的入站消息统一从这个类型冒出来——策略层不需要知道消息来自哪个平台。
- `memory.ingest_started` / `memory.ingest_ended`：记忆系统开始、完成一次会话摄取。

一条设计纪律值得单说：事件类型进注册表的唯一条件是**有真实的消费者要订阅它**。不因为代码恰好路过某个时刻就注册一个事件。这条规则挡住了事件流退化成垃圾场——每个类型都有人在用，registry 就是一份"框架里哪些时刻值得响应"的清单。

工程细节也做了取舍。notify 类事件是异步扇出，订阅者抛异常只记日志，拖不慢框架本体；gate 类事件同步执行但 fail-open——一个闸门的 bug 不能把所有工具调用卡死。每个会话的事件落一份 `events.jsonl`，出了问题可以回放。

## 如实说：策略层还没有

要说清楚的一点：OpenProgram 目前提供的是事件基础设施本身，不是一个开箱即用的 proactive agent。README 里的原话是 "a foundation, honestly labelled"——管道铺好了，主动策略层是它的第一个预期消费者，这一层留给你来建。

我们认为这个分界是对的。什么时候该主动、主动到什么程度，是产品决策，每个场景不一样;但"能不能拿到信号"是框架责任,必须框架来解决。基础设施做好了,策略层就是普通的订阅者代码:订 `context.compaction_recommended` 就能做提前压缩,订 `file.changed` 加防抖就能做自动检查,订 `channel.message_inbound` 就能做跨平台的消息接管。

如果你在做 agent 框架,或者想给现有 agent 加主动行为,欢迎来看看这套设计,也欢迎直接在总线上建你的策略层。

- GitHub：https://github.com/Fzkuji/OpenProgram
- 论文：*LLM-as-Code: Agentic Programming for Agent Harness*（KDD 2026 AgenticSE Workshop），arXiv:2606.15874
