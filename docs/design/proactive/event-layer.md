# 事件层：统一模型与框架定位

这一篇是**事件底座**的设计——一条统一的事件流，给整个 OpenProgram 用。它独立于 proactive：
proactive 只是这条流的第一个消费者。先讲清楚 Event 长什么样、这层在框架里站哪个位置、跟谁
交互，最后给框架图。

> 这层和 proactive 规则是两件事。规则（Policy、出手逻辑）见 `overview.md` 及后续；这篇只讲
> 它们脚下的地基。地基做完的验证标准：能订阅这条流，打印出一个真实活动的完整事件序列。

## 1. 为什么要这层（一句话）

框架里"某件事发生了"的信号现在散在**六套互不相通的机制**里：agent loop 内部的 AgentEvent 流、
auth 的 `_emit`、context 的 on_event 回调、channels 的 WS 广播、memory 的定时 poll、store 的
纯日志。想"在某时机做某事"，你得先搞清楚那个时机属于哪套、用哪种方式接。

这层的全部价值：**把这六套统一成"往同一条总线 emit、用同一个 `subscribe` 订阅"。**

有意思的是，框架里**已经有人把事件做对了**——`AuthStore`（`auth/store.py:204`）有规范的
`subscribe/_emit`，11 种事件。所以这层不是发明，是**把 auth 这种好做法推广到全框架**。

## 2. 统一的 Event 模型

一条事件就是一个统一格式的小数据包。模型刻意学成熟事件系统（DOM 事件、结构化日志、消息
队列、分布式追踪）的共同形状——**核心永远只有三样：是什么事 + 内容 + 时间；其余关联信息一律
放进一个开放的口袋，不写死成固定字段。**

```python
@dataclass(frozen=True)
class Event:
    # ── 核心三样（雷打不动）──
    id: str              # 唯一编号
    ts: float            # 发生时间
    type: str            # 是什么事，见 §4 类型表
    origin: str          # 谁引起的：user / agent / tool / system / proactive

    payload: dict        # 这件事本身的内容（命令是什么、改了哪个文件、哪个账号被限流）

    # ── 开放口袋：关联信息塞这，需要才塞，没有就不塞 ──
    metadata: dict       # 如 {"session": ..., "turn": ..., "lane": ...}
```

为什么是这个形状，而不是给 session/turn/lane 各留一个固定字段——这是上一轮讨论的结论，值得
写进设计：

- **turn / session 不是事件的内在属性，是外加的关联。** 看成熟系统：DOM 事件带的是 `target`
  （这事确实发生在某个对象上，内在属性），不是"属于哪次会话"；日志、消息队列把关联信息放进
  开放的 labels / headers，需要才加；分布式追踪的 `trace_id` 也只为"串联"这一个功能存在。
  共同规律是：**核心三样固定，关联信息进开放口袋。**
- **写死 `turn_id` 同时犯两个错**：一，turn 是 agent 对话循环的内部概念，对 auth 凭据限流、
  外部消息、技能变更这些事件毫无意义，硬塞一个对一半事件无意义、还得靠"可空"打补丁的字段，
  就是设计味道不对的信号；二，把开放的关联硬做成固定 schema，以后想加新的关联维度就得改模型。
- **降进 metadata 后两类事件都自然**：agent 事件想带 turn 就往口袋里塞（塞的就是框架现成的
  id，见 §5），auth/channel 事件口袋里天然没有 turn——谁也不用解释"这个字段为什么是空的"。
- `origin` 留在核心里有 `system`/`proactive`：它不是"关联谁"，是"这事什么性质"，属于"是什么
  事"的一部分，所以进核心。`proactive` 给框架自己出手产生的事件用，将来防"自己触发自己"。
- `frozen=True`：事件一旦产生就不改（流水账只记不涂），也让多线程共享它天然安全。

**turn 怎么来的（说清楚，免得它显得神秘）**：框架里没有"Turn"这个对象，turn 就是"从某条
assistant 回复算起的这一轮"，用那条消息的 id 当标识，通过一个 ContextVar（`_current_turn_id`，
`store/__init__.py:88`）在调用链里传着，本来是给文件备份"撤销这一轮改动"用的
（`backup_before_edit(turn_id, ...)`）。事件层不重新建模它——agent 事件 emit 时这个 ContextVar
正好有值，顺手塞进 metadata 即可；不在 agent 流程里的事件（auth/channel），这个 ContextVar 是
空的，metadata 里自然就没有 turn。

## 3. 两大类事件源

这是理解整层的关键。框架里的事件来源分两类，性质不同：

| | A 类：agent 活动事件 | B 类：系统事件 |
|---|---|---|
| 什么时候发生 | agent 干活的过程中 | 全局状态变化，可能没有任何 agent 在跑 |
| 例子 | 用户发消息、模型回复、工具前后、文件改、一轮结束 | 凭据被限流、上下文要溢出、外部消息进来、技能变了 |
| metadata 里带什么关联 | 通常带 session / turn / lane（emit 时 ContextVar 有值） | 带各自的关联（账号、channel…），不带 turn |
| 对 proactive | 基础 | **往往更有价值**——"凭据被限流了""上下文要溢出了"是很明确的可响应时机 |

之前容易只盯着 A 类（agent loop 内部那套），但 B 类——auth、context、channels 这些 agent loop
**之外**的——对主动性常常更重要。事件层必须两类都装得下，而 metadata 开放口袋天然装得下：
A 类口袋里有 turn，B 类没有，同一个模型不用为谁开特例。

## 4. 事件类型（第一版）

| 类 | type | 何时 | 来源（现有代码） |
|---|---|---|---|
| A | `user.prompt_submitted` | 用户发消息 | dispatcher 入口 |
| A | `model.response_started` / `.completed` | 模型开始/说完回复 | `agent/types.py` AgentEventMessageStart/End |
| A | `tool.before` | 工具即将执行 | AgentEventToolStart（**+ 可截语义，见 §6**） |
| A | `tool.after` | 工具执行完 | AgentEventToolEnd |
| A | `file.changed` | 文件被改 | **新增**，挂 `backup_for_current_turn` |
| A | `turn.ended` | 一轮结束 | AgentEventTurnEnd |
| A | `subagent.started` / `.ended` | 子任务起止 | TaskRunner 广播 |
| B | `credential.cooldown` / `.exhausted` / `.rotated` | 凭据被 429 限流 / 池耗尽 / 轮换 | `AuthStore._emit`（已是规范发布订阅） |
| B | `context.compaction_recommended` / `.compacted` | 上下文用到 70% 提示 / 80% 压缩 | `context/engine.py` on_event |
| B | `channel.message_inbound` | 外部消息从 Telegram/Discord/飞书进来 | `channels/_broadcast.py` |
| B | `skills.changed` / `plugins.update_available` | 技能文件改了 / 插件有新版 | webui 文件 watcher |

## 5. 事件层在框架里的定位

**一句话：worker 进程里一个进程级单例总线，所有事件源往它 emit，所有消费者从它 subscribe。**

调研确认的关键事实：OpenProgram 的 webui server、agent loop、channels、memory、auth、task runner
**全部跑在同一个 worker 进程里**（各是 daemon 线程，`worker/runner.py`）。而且框架里已经有
`get_store()` / `get_runner()` / `default_store()` 这种"进程级单例 + 双检锁"的成熟先例。

所以事件总线就照这个先例做——在 `agent/event_bus.py`（已有 EventBus 类，闲置）加一个
`get_event_bus()`，进程级单例。同一进程内的所有线程都能拿到同一个实例、直接 emit/subscribe，
**不需要任何跨进程桥接**（将来若拆出独立进程，再上 Redis/ZMQ，现在不需要）。

```python
# agent/event_bus.py，照 AuthStore 的双检锁模式
_event_bus = None
_lock = threading.Lock()

def get_event_bus() -> EventBus:
    global _event_bus
    with _lock:
        if _event_bus is None:
            _event_bus = EventBus()
        return _event_bus
```

总线接口（在现有 EventBus 上收敛）：

```python
class EventBus:
    def emit(self, event: Event) -> None:
        # 广播给所有订阅者。fire-and-forget，不阻塞调用方。

    def subscribe(self, handler, *, types=None) -> unsubscribe_fn:
        # 订阅。types 可选——只关心某几类就只收那几类。
```

跟现有 EventBus 的差别：现在它按 channel（字符串）订阅、传任意 data；改成按**事件类型**订阅、
传**统一 Event**。下游"我只关心 tool.before"能精确订阅。

## 6. 两种交互方式：观察 vs 拦截

总线对大多数事件是"观察型"，但有一个时机特殊，要"拦截型"。这两套机制要分清。

**观察型（默认，异步，零影响）**：事件 emit 出去，订阅者异步收到，事件源该干嘛干嘛、不等。
所有事件都走这条。订阅者再慢也不拖慢框架。

**拦截型（仅 `tool.before`，同步，要快）**：工具执行前这个点特殊——下游可能想说"别执行这个"。
这需要同步：agent 得等一个准话才继续。在工具执行的单一入口 `_execute_tool_calls`
（`agent_loop.py:466`，所有工具都过这）的 `tool.execute()` 之前，加一个同步问询点，复用现有的
工具批准机制（`_approval.py`）落地"要确认"。要点：必须快（不许调 LLM）；多方表态取最严
（拦下 > 确认 > 放行）；对 subagent 也生效（独立于现在的 `permission_mode=bypass`，否则危险
动作塞进子任务就溜了）。

## 7. 框架图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  OpenProgram Worker 进程（单进程，多 daemon 线程）                              │
│                                                                                │
│   事件源（往总线 emit）                                                         │
│   ┌─────────────────┐  ┌──────────────┐  ┌───────────┐  ┌──────────┐          │
│   │ agent loop      │  │ auth         │  │ context   │  │ channels │          │
│   │ (A类: 模型/工具/ │  │ (B类: 凭据   │  │ (B类: 压缩 │  │ (B类:外部 │          │
│   │  文件/turn)     │  │  限流/轮换)  │  │  阈值)    │  │  消息进)  │          │
│   └────────┬────────┘  └──────┬───────┘  └─────┬─────┘  └────┬─────┘          │
│            │                  │                │             │                 │
│   ┌────────┴────────┐  ┌──────┴───────┐        │             │                 │
│   │ task runner     │  │ memory       │        │             │                 │
│   │ (A类:子任务起止) │  │ (B类:空闲处理)│        │             │                 │
│   └────────┬────────┘  └──────┬───────┘        │             │                 │
│            │                  │                │             │                 │
│            └──────────┬───────┴────────┬───────┴─────────────┘                 │
│                       ▼                ▼                                        │
│            ╔══════════════════════════════════════════╗                        │
│            ║   EventBus（进程级单例 get_event_bus()）   ║                        │
│            ║   · emit(Event)   一条统一事件流           ║                        │
│            ║   · subscribe(handler, types=...)         ║                        │
│            ╚══════════════════════════════════════════╝                        │
│                       │                │                                        │
│         观察型(异步)   │                │  拦截型(同步,仅 tool.before)            │
│            ┌──────────┴──────┐         └──────────┐                             │
│            ▼                 ▼                    ▼                             │
│   ┌─────────────────┐ ┌─────────────┐   ┌──────────────────┐                  │
│   │ webui server    │ │ proactive   │   │ tool.before 同步  │                  │
│   │ 订阅→转发前端 WS │ │ (第一个消费  │   │ 问询点 → 复用     │                  │
│   │                 │ │  者，单独设计)│   │ _approval 拦/确认 │                  │
│   └────────┬────────┘ └─────────────┘   └──────────────────┘                  │
│            │                                                                    │
└────────────┼────────────────────────────────────────────────────────────────┘
             │ WebSocket
             ▼
        前端 / TUI（浏览器、命令行）
```

读图要点：

- **中间那条总线是唯一的枢纽**。左上一圈是事件源（A 类 + B 类），都往总线 emit；下方是消费者，
  从总线 subscribe。源和消费者互不直接认识，只认总线——这就是"统一"的含义。
- **webui server 是一个特殊消费者**：它订阅总线，把事件转发给前端 WebSocket。前端要实时看到
  agent 在干嘛，就是这条路。
- **proactive 是另一个消费者**，跟 webui 平级——它不在事件层里面，是事件层之上的应用。这张图
  里它只占一个框，说明事件层和 proactive 彻底解耦。
- **拦截型单独一条线**（右侧），只为 `tool.before`，同步，连到现有批准机制。其余全是异步观察。

## 8. 怎么接进现有代码 / 落地顺序

这是设计；具体接线点（file:line）、把六套源桥接进总线的做法、分步落地与验证，写在
[实施规划](../../plans/proactive-implementation.md)。一句话版本：先把 A 类（agent loop）收口成
总线并验证能打出完整序列，再补文件改动事件和工具前可截，再把 B 类系统事件单向桥接进来，
最后补并发的 lane 区分。每步独立可验证。
