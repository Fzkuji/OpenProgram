# Channel 子系统 —— 要求与不变量

本文说明 channel 层必须守住的东西：每个 platform adapter 对外呈现的接口、
两条发送路径的关系、session 如何取键，以及设计边界落在哪里。对比章节记录
OpenClaw 和 hermes 在这些点上各自的做法，以及我们的答案为何不同。

该层的结构与消息流转见 [`design.md`](design.md)。

## 1. 抽象层

### 1.1 Channel ABC

`openprogram/channels/base.py` 定义了每个 adapter 都满足的契约：

```python
@dataclass(frozen=True)
class MessageHandle:
    platform: str
    account_id: str
    target: str
    message_id: str

    @property
    def editable(self) -> bool: ...


class Channel(abc.ABC):
    platform_id: str = ""

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None: ...

    def send_text(self, target, text) -> Optional[MessageHandle]: ...
    def send_text_full(self, target, text) -> SendResult: ...
    def edit_text(self, handle, new_text) -> bool: ...
    def edit_text_full(self, handle, new_text) -> SendResult: ...
```

`run(stop)` 是唯一的抽象方法，因为各平台的入站事件循环形态差异太大，无法统一：
discord.py 的 Gateway、Slack Socket Mode、Telegram 与 WeChat 的长轮询没有共同形状。
出站则确实有共同形状，所以 `send_text` / `edit_text` 以基类具体实现的形式提供，
底层走 `_transport`。只有当 platform-native SDK 能带来 raw HTTP 做不到的东西时
（mention 解析、附件上传），adapter 才需要 override。

`MessageHandle` 是“我发出去、之后可能要改的一条消息”这个单位。四个字段都是字符串，
因此 handle 可以序列化后跨进程传递：一个进程发送、另一个进程编辑——正是这一点让
cron 驱动的调用方和 dispatcher 能指向同一条消息。

### 1.2 两个入口，一份实现

出站流量按设计有两个入口，服务两类不同的调用方：

```
入口 A（无状态、cron-friendly）    outbound.send(channel, account, user, text)
入口 B（有状态、保留 message_id）  adapter.send_text(target, text) -> handle
                                   adapter.edit_text(handle, text)

                     ↓ 两者都调用

实现层                             _transport.post_message / patch_message
                                   HTTP 调用 + chunking + 凭据加载
```

保留两个入口是一项要求，不是历史遗留。入口 B 需要 adapter 状态，因为 progress
streaming 必须记住要编辑哪条消息。入口 A 则必须在进程里根本没有 adapter 实例时
也能工作——原因见 §5.F。

不变量是：两者共享一份实现。`_transport` 持有 HTTP 调用、凭据加载和 chunking，
两个入口都不重新实现它们。

### 1.3 结构化发送结果

`_transport.post_message` 与 `patch_message` 返回 `SendResult`，含
`ok` / `message_id` / `error_kind` / `error_detail` / `retryable`。`error_kind`
取值为 `auth` / `rate_limit` / `bad_target` / `network` / `not_supported` /
`unknown`，由 HTTP 状态码经 `_classify_http_status` 推断，平台特有的错误描述则经
`_telegram_kind_from_description` 与 `_slack_kind_from_error` 推断。

`bool` 返回值无法区分瞬时网络失败和永久 auth 失败，因此支撑不了智能重试，也无法在
UI 上正确显示原因。返回 `bool` 的形式（`outbound.send`、`Channel.send_text`、
`Channel.edit_text`）保留给只关心成败的调用方；`_full` 变体暴露完整结果。

### 1.4 中性入站消息

`_message.py:ChannelMessage` 是平台中性的入站结构，一个 frozen dataclass，含
`text` / `chat_id` / `user_id` / `user_display` / `chat_type` / `ts` /
`reply_to_id` / `thread_id` / `attachments`。4 个 adapter 都在入口处把
platform-native 对象 parse 成 `ChannelMessage`。

`dispatch_inbound` 目前不消费 `reply_to_id` / `thread_id` / `attachments`，但
adapter 已经把它们抽了出来。parse 这一步已经就位正是要点所在：将来支持 reply
引用或附件读取，改的是消费方，而不是 4 个 adapter。

### 1.5 入站分发

`dispatch_inbound(channel, account_id, peer_kind, peer_id, user_text,
user_display, progress_stream=False) -> Optional[str]` 端到端处理一条消息：

1. 查 `session_aliases` / `bindings` → 决定 agent_id
2. 按 `agent.session_scope` 计算 `session_key`
3. 应用 `daily_reset` / `idle_minutes` reset policy
4. `_load_or_init_session` 写 SessionDB
5. 构造 `TurnRequest` 调 `process_user_turn`
6. 把 reply append 到 SessionDB
7. broadcast `channel_turn` envelope 到 webui

`progress_stream=False` 时返回完整 reply 字符串，由 adapter 自己发送。
`progress_stream=True` 时 dispatcher 直接通过 `send_text` / `edit_text` 驱动
channel，于是 turn 还在运行时 tool 事件就能到达用户。

### 1.6 Platform 注册

`channels/__init__.py` 把注册拆成 `_BUILTIN_CHANNEL_CLASSES`（4 个内置，永远存在）
和 `_PLUGIN_CHANNEL_CLASSES`（外部注册）。Plugin 有两种注册方式：在 `pyproject.toml`
中声明 `[project.entry-points."openprogram.channels"]`，启动时经
`importlib.metadata.entry_points` 扫描；或在 plugin hook 里调用
`register_channel(name, cls)`。

内置优先：plugin 若占用内置名字会被忽略，而不是允许覆盖。`CHANNEL_CLASSES` 保留为
覆盖两者的 dict-like proxy，现有调用方不受影响。

---

## 2. 其他项目怎么解决

可比的只有两个：**OpenClaw**（我们 fork 的来源，TypeScript）和 **hermes**
（chat-bot 专门项目，Python）。opencode 和 claude-code 都没有 channel 子系统——
它们的 surface 是 CLI/TUI/Web/IDE，对接坐在前端的人类，而不是接进 Discord 或
Slack 群。

### 2.1 OpenClaw

来源：`references/openclaw/src/channels/` 加
`references/openclaw/extensions/{discord,slack,telegram}/`。

**布局**：核心 `src/channels/` 有一批细粒度文件（routing / account / approval /
typing / draft-stream / health-check / thread-bindings-policy），每个 platform 在
`extensions/{name}/` 下有独立目录——仅 discord 就有 70+ 文件，slack 40+，
telegram 35+。

**Plugin SDK**（`src/plugin-sdk/channel-*.ts`，50+ contract 文件）把 core 和
platform 实现完全隔离。Core 只看到抽象接口：

```typescript
ChannelMessageSendAdapter        // 发送能力
ChannelMessageLiveAdapterShape   // 实时消息编辑 (draft → live-preview → final)
ChannelApprovalAdapter           // reaction ✓/✗ 确认 + timeout/retry
ChannelMessageActionAdapter      // button/menu action handler
ChannelOutboundAdapter           // 跨进程 send 也走 adapter
```

**Streaming edit**（`src/plugin-sdk/channel-streaming.ts` +
`extensions/discord/src/draft-stream.*`）：消息生命周期有三态。

```
draft → live-preview (节流 edit) → final
```

先发出 draft，tool 运行过程中持续 edit message，最后由 pipeline 收尾。节流策略内置
在 pipeline 里。

**Reaction approval**（`src/channels/ack-reactions.ts` +
`extensions/discord/src/approval-native.ts`）：

```typescript
type ChannelApprovalAdapter {
    onApprove, onDecline, onTimeout
}
```

dangerous tool 触发时 bot 加 ✓/✗ emoji reaction，用户点击，adapter 通知 dispatcher。
完整 lifecycle 覆盖 timeout、retry、cancel。

**DurableMessageSendResult**：send 返回值含 message_id、edited_ids 和 retry 策略，
支持 receipt tracking 与 delivery confirmation。

**Health check**（`health-check-adapter.ts`）：启动时 probe 每个 adapter 的可用性，
失败则 graceful degradation，不让一个挂掉的 platform 拖垮整个 worker。

**注册**：plugin manifest——每个 extension 的 `openclaw.plugin.json` 声明其
`channels` 能力，core loader 扫描 `extensions/*/` 或 npm packages，动态加载 +
lazy instantiation。

### 2.2 Hermes

Python 写的，对接 14+ 平台。设计哲学比 OpenClaw 简单：没有 Plugin SDK 那一层，
单个文件就能装下完整 adapter（base 1500 行）。

**BasePlatformAdapter ABC**（`gateway/platforms/base.py`）：

```python
class BasePlatformAdapter(ABC):
    async def send(self, chat_id: str, content: str,
                   reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult

    async def edit_message(self, chat_id: str, message_id: str,
                          content: str, finalize: bool = False) -> SendResult

    async def send_draft(self, chat_id: str, draft_id: int,
                        content: str, metadata=None) -> SendResult

    async def send_typing(self, chat_id: str,
                         metadata=None) -> None

    async def create_handoff_thread(self, parent_chat_id: str,
                                   name: str) -> Optional[str]
```

5+ 个 async 抽象方法，统一返回含 `message_id` / `retryable` 的 `SendResult`
dataclass。

**中性消息结构**

```python
@dataclass
class MessageEvent:
    text: str
    message_type: MessageType = MessageType.TEXT
    source: SessionSource         # 平台、聊天 ID、用户 ID、thread_id
    media_urls: List[str] = []    # 下载到本地的缓存路径
    reply_to_message_id: Optional[str] = None
    auto_skill: Optional[str | list[str]] = None
    channel_prompt: Optional[str] = None

@dataclass
class SessionSource:
    platform: Platform
    chat_id: str
    chat_type: str = "dm" | "group" | "channel" | "thread"
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    guild_id: Optional[str] = None
    parent_chat_id: Optional[str] = None
```

adapter 负责 platform-native → `MessageEvent` 的翻译；dispatcher 只看
`MessageEvent`。

**Session key 二维隔离**
（`build_session_key(source, group_sessions_per_user, thread_sessions_per_user)`）：

```
DM:    agent:main:{platform}:dm:{chat_id}[:{thread_id}]
Group: agent:main:{platform}:group:{chat_id}[:{thread_id}][:{user_id}]
```

线程默认跨用户共享，组默认按用户隔离，两者都可被 per-channel 配置覆盖。

**Progress streaming**（`gateway/run.py:_edit_progress_message()`）：

```python
async def _edit_progress_message(message_id: str, content: str):
    result = await adapter.edit_message(
        chat_id=source.chat_id,
        message_id=message_id,
        content=content,
    )
```

工具开始 → `adapter.send` 发占位消息 → 拿到 `message_id` → 工具 stream 事件触发
`_edit_progress_message(message_id, latest_text)` → 最后 `finalize=True` 收尾。
`_roll_progress_overflow_if_needed()` 处理 progress 行超过平台字符限制的情况：
第一组 edit 当前 bubble，后续组发新 bubble。

**Debounce 合并**（`base.py:2812-2876`）：

```python
class TextDebounceState:
    event: MessageEvent
    task: asyncio.Task | None
    first_ts, last_ts: float

async def _queue_text_debounce(session_key, event):
    """连续到达的同 session 文本合并成一条, delay 0.35s, hard cap 1.0s"""
```

用户连发 3 条（“hi”“你在吗”“问个问题”），agent 收到的是合并后的一次 turn，
而不是 3 次 agent run。

**快速命令绕路**（`base.py:3205-3219`）：

```python
if should_bypass_active_session(cmd):   # /stop, /new, /reset, /approve
    await self._dispatch_active_session_command(...)
```

`/stop` 和 `/approve` 走快速路径，不进 session 队列，也不等 agent 当前任务结束。

**Attachment 本地缓存**：

```python
def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """同步写到 cache_dir, 文件名 doc_{uuid12}_{原名}"""

def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """删除 24h+ 的缓存"""
```

Telegram URL 在 1 小时过期前下载到本地，后续 agent 能反复读，24h 后清理。

**DeliveryRouter**（`gateway/delivery.py`）：

```python
class DeliveryTarget:
    """origin | local | telegram:123 | slack:..."""
    platform: Platform
    chat_id: Optional[str] = None

class DeliveryRouter:
    async def deliver(content, targets, ...) -> Dict:
        """Route to all targets via adapter instances."""
```

`outbound.send` 的对应物同样走 adapter 实例，而不是另写一条 raw HTTP path。

**Approval flow**：用文本命令而非 reaction。

```python
async def _handle_slash_approve(self, event):
    """Handle /approve — unblock waiting agent thread(s)."""

_pending_approvals: Dict[str, Dict[str, Any]]   # session → pending
# tool 线程: Event.wait() 阻塞
# /approve 命令: Event.set() 唤醒
```

简单稳定。adapter 层确实有 `send_reaction` 实现，但 reaction 不在 approval 的
关键路径上。

**Platform 注册**（`gateway/platform_registry.py`）：

```python
@dataclass
class PlatformEntry:
    name, label, adapter_factory, check_fn,
    validate_config, install_hint

platform_registry.register(PlatformEntry(...))
adapter = platform_registry.create_adapter("slack", config)
```

内置走硬编码 fast path，plugin platform 通过 registry 自注册。

---

## 3. 三方对比

| 方面 | OpenProgram | OpenClaw (fork 来源) | Hermes |
|---|---|---|---|
| Base abstract method 数 | 1 (`run`) + send/edit 具体默认实现 | 5+ (SendAdapter / LiveAdapter / ApprovalAdapter 等) | 5+ (`send/edit/draft/typing/handoff`) |
| 中性消息结构 | `ChannelMessage` dataclass | `ChannelMeta` 含 media/richtext/components | `MessageEvent` + `SessionSource` dataclass |
| Send 返回值 | `SendResult` (ok/message_id/error_kind/retryable) | `DurableMessageSendResult` (message_id/edited_ids/retry 策略) | `SendResult` (message_id + retryable) |
| Dispatch signature | 同步 → str，或 `progress_stream=True` | 异步 streaming pipeline (draft → live → final) | 异步 → 流式事件 |
| Session 隔离 | `session_scope` 4 枚举 | `dmScope` hardcode + thread-bindings-policy | 二维 (chat × user × thread) |
| Edit 接口 | 基类上的 `edit_text` | 完整 (ChannelMessageLiveAdapterShape) | 内建 |
| 进度流 | dispatcher 驱动 send_text/edit_text | 三阶段，节流内置 | edit_message + overflow 自动分组 |
| Approval 机制 | 文本命令桥 (`_question_commands.py`) | reaction ✓/✗ + onApprove/onDecline/onTimeout | `/approve` 文本命令 |
| Debounce 合并 | 无 | 不详 | 0.35s delay + 1s hard cap |
| Retryable 信号 | `SendResult.retryable` | DurableMessageSendResult 含 backoff | `SendResult.retryable` |
| Health check | 无 | `health-check-adapter.ts` 启动 probe | 不详 |
| Receipt tracking | 无 | 有 (delivery confirmation) | 不详 |
| Structured replies | text only | embed/button/menu | 部分 |
| Attachment 缓存 | 字段已 parse，未消费 | 有 | UUID-前缀 + 24h 清理 |
| 出站 API | `outbound.send` 共用 `_transport` | 走 adapter 实例 | `DeliveryRouter(adapters: dict)` |
| 进程模型假设 | 多部署形态 (lib + worker + script) | 单 daemon 进程 | 单 gateway 进程 |
| Chunking 实现 | `_transport._chunk`，另有 adapter 本地副本 | 平台 plugin 内统一 | 统一 (`truncate_message`) |
| Platform 注册 | 内置 dict + entry-point plugin | Plugin SDK (manifest + dynamic loader) | hybrid (built-in + registry) |
| 语言 | Python | TypeScript | Python |

---

## 4. 设计边界

### 4.1 为什么 `run` 是唯一的抽象方法

把 `send` / `edit` / `react` 定为抽象方法，会迫使每个 adapter 实现自己平台可能
根本没有的能力。取而代之的是基类基于 `_transport` 提供可用实现，adapter 只在能做得
更好时 override。一个除 `run` 外什么都不实现的 adapter，在发送和编辑上依然完整可用。

### 4.2 为什么 WeChat 是最难的一个

iLink API 不支持编辑已发出的消息。`MessageHandle.editable` 编码了这一点：WeChat 的
handle 带空 `message_id`，`editable == False`，而 `edit_text_full` 返回
`SendResult.fail("not_supported", ...)`，既不抛异常，也不用“删旧发新”伪造编辑。

这正是把编辑能力表达为 handle 的属性、而非平台必须实现的抽象方法的原因。不能编辑的
平台通过每个调用方本就要处理的同一个返回类型来说明这件事，任何调用方都不必对平台
名字做特判。

### 4.3 拒绝跨平台编辑

`edit_text_full` 检查 `handle.platform != self.platform_id`，不匹配则返回
`SendResult.fail("bad_target", ...)`。跨 adapter 协调是调用方的事；基类守住的是
一个 adapter 只编辑本平台的消息。

### 4.4 `_conversation.py` 的职责已拆分

路由、session_key 计算、session 持久化、dispatcher 调用、webui 广播分别在独立模块：
`_session_routing.py`、`_session_store.py`、`_broadcast.py`，`_conversation.py` 保留
端到端的 `dispatch_inbound` 流程。这符合仓库对层次化代码结构的偏好。

---

## 5. 设计理由

**A. Progress streaming 是接线，不是新功能**

dispatcher 早已 emit `tool_use` / `stream_event` / `tool_result` envelope
（见 `agent/_event_parsing.py`），`dispatch_inbound._on_event` 也早已在订阅。
让 streaming 成为可能的是抽象层：一个返回 `message_id` 的 send，和一个能作用于它的
`edit_text`。有了 `MessageHandle` 和 `_transport.patch_message`，
`progress_stream=True` 不过是 dispatcher 消费一条本就存在的事件流。

**B. 只给各 adapter 加方法而不共享实现，成本会成倍增长**

不做共享 transport 而直接给 4 个 adapter 各加 `edit`，就是 4 份 send 实现加 4 份
edit 实现加 4 份 react 实现，再被 outbound 路径翻一倍。两个入口都走 `_transport`，
才使得新增一种操作只需一份实现而不是八份。

**C. hermes 的高级机制是有意推迟的**

debounce 合并、快速命令绕路、attachment 缓存，都是 hermes 跑过生产量级流量后得出的
优化。OpenProgram 当前的请求速率并不需要它们。顺序是先把抽象做对，等问题出现再加。

**D. 为什么不整体照搬 OpenClaw**

三条原因，由浅到深。

*没有 Python 实现可以照抄。* OpenClaw 整体是 TypeScript/Node.js
（`pnpm-workspaces` + `tsdown` build）；`src/bindings/` 只有 1 个 TS 文件，
`packages/sdk/` 和 `packages/plugin-sdk/` 全是 TS。仅有的 5 个 `.py` 是 CI 脚本和
skill 工具，与 channel 无关。OpenClaw 既不提供 Python binding 也不提供 Python SDK，
所以复用意味着重新实现其设计，而不是 import 进来。

不过语言本身不构成借鉴障碍：TS interface 对应 Python `Protocol` 或 `abc.ABC`，
TS dataclass 对应 `@dataclass`，TS async 对应 asyncio，TS plugin manifest 对应
`plugin.json`（`openprogram/plugins/` 已经在做）。设计模式跨语言通用。

*静态类型与动态类型之别，改变了 50+ contract 文件的价值。* 在 TS 里编译器能强制
plugin 实现全部接口，IDE 提示也准。同样的拆分写成 Python `Protocol`，运行期不强制，
提示也弱，因为 mypy 并非默认开启。所以这种粒度的拆分在 Python 里收益打折。这影响的是
每个接口是否值得独立成文件（不值得），而不是接口形状是否值得学（值得）。

*async-first 与 sync-with-threading 之别。* OpenClaw 全套异步
（`send/edit/typing/handoff`），dispatch 是流式 pipeline；hermes 同样 async-first。
我们的 channel 层是同步加 threading——每个 adapter 一个线程，`dispatch_inbound`
阻塞返回。整体照搬异步设计就要重写 dispatch 流程：把 `dispatch_inbound` 改成 async
generator，并把 4 个 adapter 的事件循环重新接入 asyncio。这是真实的迁移成本，
不是抽象层换个名字。

**E. 从两个项目各学什么**

```
                          学 OpenClaw       学 hermes
─────────────────────────────────────────────────────
接口设计 (what)
  send/edit/typing/approve  ✓ (更全)         ✓
  SendResult 含 retry        ✓                ✓
  Streaming lifecycle        ✓ (三态)         ✓ (单次 edit)
  Approval lifecycle         ✓ (完整)         ✓ (/approve 命令)
  Health check / probe       ✓                —

代码组织 (how)
  Plugin SDK 50+ contracts  ✗ 过度            —
  每 platform 70+ files     ✗ 过度            —
  单文件 base + adapter      —                ✓ 匹配
  async-first dispatch       ✓                ✓
```

两个项目在不同层面各自值得学，且两个层面不冲突。OpenClaw 的接口形状更完整、更系统，
其方法签名、lifecycle、返回值结构值得沿用。hermes 的代码组织规模跟我们匹配——base
ABC 一个文件、每 platform 一个文件、不搞 plugin manifest。取 OpenClaw 的方法签名，
落到 hermes 规模的文件组织里，正是本设计采用的组合。

**F. 为什么要有无状态的 outbound 入口**

OpenProgram 内部并存两条范式：

```
范式 A: agentic programming
  Python 主控 → if/else/for/while 控制流
  @agentic_function 创建 Context 节点
  Runtime.exec 在被显式调用时才请求 LLM
  入口: 程序员写的 Python 代码

范式 B: agent loop（channel/webui chat 走的路径）
  LLM 决定调什么工具、何时调
  process_user_turn → agent_loop → tool streaming
  入口: 外部 message
```

Channel 挂在范式 B 上。范式 A 同样需要发送：一个 cron 驱动的 `@agentic_function`
要给用户问好，不需要 adapter 实例、不需要订阅 stream、也不需要绑定 session lifecycle。

OpenClaw 的“统一走 adapter”和 hermes 的 `DeliveryRouter(adapters: dict)`，在单
daemon 进程模型下都是合理设计——cron scheduler、platform adapter、agent runtime 共处
一个进程，cron job 能通过依赖注入拿到 adapter dict。OpenProgram 的多部署形态打破了
这个假设：

```
部署场景                                    adapter instance 在哪
────────────────────────────────────────────────────────────────
openprogram worker 跑                        worker 进程里
用户写 Python 脚本 import @agentic_function  没有
cron 跑在 worker 外的另一个进程               没有
Jupyter notebook 实验                        没有
pytest 测试                                  没有
```

范式 A 设计上就是 library 模式：用户在自己的脚本里 import 使用，不假设 worker 进程
存在。所以 `outbound.send` 是范式分工的要求，而不是 adapter 路径的重复。不该重复的是
它们下面的实现，这正是两者都走 `_transport` 的原因。

由此有两条推论。将来若改为 async-first 的 base，必须在模块顶层保留同步包装，让
`@agentic_function` 不必接触 asyncio 也能发送。以及 streaming edit 应当对范式 A 保持
可达：一个上报中间进展的 `@agentic_function` 应该能持有 `MessageHandle` 自己编辑，
而不是把这个能力绑死在 dispatcher 的 pipeline 上。

---

## 6. 附录 —— 实现状态

已就位：带 `MessageHandle` 与具体 `send_text` / `edit_text` 的 `Channel` ABC；
作为两个入口共享实现层的 `_transport`；`SendResult` 错误分类；`ChannelMessage`
中性入站结构；基于 entry-point 的 platform 注册；`progress_stream=True` 的入站分发；
以及 `_conversation.py` 拆分出的路由、session-store、broadcast 模块。

尚未完成：

- **Chunking 仍有重复。** `_transport` 有 `_chunk`，但 `discord.py`、`slack.py`、
  `wechat.py` 各自保留本地 `_chunk` 副本，4 个 adapter 也各自定义 `MAX_MSG_CHARS`。
  仍直接用 platform SDK 的 adapter 回复路径尚未迁到 `send_text`。
- **Session 隔离仍是一维。** `peer_id` 把 chat 和 user 拼成一个字符串，
  `session_scope` 只有 4 个枚举值（main / per-peer / per-channel-peer /
  per-account-channel-peer），无法表达 hermes 默认开启的线程内共享模式。
  `ChannelMessage.thread_id` 已为此预先 parse。
- **`account_id` 传两次**，一次给 adapter 构造函数，一次给 `dispatch_inbound`。
  这挡住了一个进程内一个 adapter 服务多个账号的做法。
- **富消息字段已 parse 但未消费。** `reply_to_id`、`thread_id`、`attachments` 已进入
  `ChannelMessage`；`dispatch_inbound` 不读取它们，也没有 attachment 缓存。
- **没有 health check 和 receipt tracking。** 启动时不 probe adapter 可用性，
  发送后也不确认送达。
