# Channel 子系统设计审计

记录 OpenProgram channel 子系统的现状、与 hermes (主要对标) 的设计差距、以及当前结构性缺陷。**只描述事实和判断，不写实施方案**——方案在后续讨论里定。

## 1. 我们现在的设计

### 1.1 文件布局

```
openprogram/channels/        2500 行 / 9 个 py 文件
├── base.py            21 行  Channel ABC, 仅 run(stop) 一个抽象方法
├── _conversation.py  483 行  dispatch_inbound + session 路由 + 持久化 + webui 广播
├── outbound.py       196 行  跨进程 send(channel, account, user, text) API
├── _heartbeats.py     44 行
├── accounts.py       278 行  per-platform 凭据存储
├── bindings.py       274 行  (channel, account, peer) → agent_id 路由
├── setup.py          289 行  setup wizard
├── worker.py          26 行  shim → openprogram.worker
├── discord.py        111 行  DiscordChannel adapter
├── slack.py          119 行  SlackChannel adapter
├── telegram.py       111 行  TelegramChannel adapter
└── wechat.py         454 行  WechatChannel adapter (含 QR 登录 / cursor 持久化)
```

### 1.2 抽象层（base.py）

```python
class Channel(abc.ABC):
    platform_id: str = ""

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None: ...
```

**就这些**。Channel 只规定了"必须能 run 起来直到 stop 被 set"——怎么读消息、怎么发消息、出错怎么处理、能不能 edit / react，base 完全不管。所有 platform-specific 行为下放到各 adapter 自由发挥。

### 1.3 入站消息处理（adapter 内）

每个 adapter 在自己的 `run()` 里跑 platform 原生 SDK 的事件循环，拿到 message → 抽出 `(chat_id, user, text)` → 调 `dispatch_inbound(...)` → 拿到完整 reply string → 用 SDK 把 reply 发回去。

| Platform | 入站读 | 出站发回 |
|---|---|---|
| Discord | `discord.py` SDK `on_message` | `msg.channel.send(chunk)` |
| Slack | `slack_sdk` Socket Mode | `web.chat_postMessage()` |
| Telegram | 原始 HTTP `getUpdates` 长轮询 | 调 `outbound.send()` |
| WeChat | iLink HTTP `getupdates` 长轮询 | 调 internal `_send` |

注意 Telegram 和 WeChat 出站不走自己的 adapter 代码，而是间接调 `outbound.send()`——这本身已经是不一致。

### 1.4 dispatch_inbound（_conversation.py）

签名：`dispatch_inbound(channel, account_id, peer_kind, peer_id, user_text, user_display) -> str`

一次性阻塞调用，返回完整 reply。流程：

1. 查 `session_aliases` / `bindings` → 决定 agent_id
2. 按 `agent.session_scope` 计算 `session_key`（per-account-channel-peer / per-peer / main / 等）
3. 应用 `daily_reset` / `idle_minutes` reset policy
4. `_load_or_init_session` 写 SessionDB
5. 构造 `TurnRequest` 调 `process_user_turn` → 完整 turn
6. 把 reply append 到 SessionDB
7. broadcast `channel_turn` envelope 到 webui

里面有一个 `_on_event(env)` callback 已经在订阅 dispatcher 的 stream envelope：

```python
def _on_event(env: dict) -> None:
    srv._broadcast(json.dumps(env, default=str))   # 给 webui 看
    if env.get("type") == "chat_ack":              # 抓 user_msg_id
        captured_user_id.append(...)
```

但这个 callback 只对 webui 广播。channel 自己拿不到流式事件。

### 1.5 outbound.py 跨进程发送

```python
outbound.send(channel, account_id, user_id, text) -> bool
```

不走 adapter 实例。`_SENDERS` 是一个 4 项的 dict，每项独立用 raw HTTP（requests 库）调 platform API：

- `_send_telegram` → `POST /bot{token}/sendMessage`
- `_send_discord`  → `POST /api/v10/channels/{ch}/messages`
- `_send_slack`    → `POST /api/chat.postMessage`
- `_send_wechat`   → `POST /ilink/bot/sendmessage`

每个 sender 自己 load credentials、自己拼 headers、自己处理 chunking。

### 1.6 消息分块

`MAX_MSG_CHARS` 在 **5 个文件**里独立定义：

```
discord.py    1800
slack.py      3900
telegram.py   4000
wechat.py     1800
outbound.py   1800   (跟 discord/wechat 重复，跟 slack/telegram 不一致)
```

`_chunk(text, limit)` 同样的实现复制了 **5 次**。

### 1.7 message 中性结构

**没有**。每个 adapter 直接处理 platform-native object：

- Discord：`discord.Message` 对象 → 抽 `msg.content / msg.author.id / msg.channel.id`
- Slack：events_api dict → 抽 `event["text"] / event["user"] / event["channel"]`
- Telegram：update dict → 抽 `msg["text"] / msg["chat"]["id"]`
- WeChat：iLink msg dict → 字段路径自定义

没有任何 `ChannelMessage` / `MessageEvent` 抽象。要支持 reply / quote / attachment 没有共同 schema。

### 1.8 反向依赖

`outbound._send_wechat` 里：

```python
from openprogram.channels.wechat import _make_wechat_uin
```

outbound（应该是底层）反向 import wechat adapter（应该是叶子）的私有函数。

---

## 2. 其他项目的设计

可比的就两个：**OpenClaw**（我们 fork 来源，TS 写的）和 **hermes**（chat-bot 专门项目，Python）。opencode 和 claude-code 都没有 channel 子系统——它们的 surface 是 CLI/TUI/Web/IDE，对接的是坐在前端的人类用户，不接 Discord/Slack 群里。

### 2.1 OpenClaw（fork 来源）

来源：`references/openclaw/src/channels/` + `references/openclaw/extensions/{discord,slack,telegram}/`。TS/Node.js，企业级模块化设计。

**布局**：核心 `src/channels/` 一堆细粒度文件（routing / account / approval / typing / draft-stream / health-check / thread-bindings-policy …），每个 platform 在 `extensions/{name}/` 独立目录，单 discord 70+ 文件、slack 40+、telegram 35+。

**Plugin SDK** (`src/plugin-sdk/channel-*.ts` 50+ contract 文件) 完全隔离 core 和 platform 实现。Core 只看到抽象接口：

```typescript
ChannelMessageSendAdapter        // 发送能力
ChannelMessageLiveAdapterShape   // 实时消息编辑 (draft → live-preview → final)
ChannelApprovalAdapter           // reaction ✓/✗ 确认 + timeout/retry
ChannelMessageActionAdapter      // button/menu action handler
ChannelOutboundAdapter           // 跨进程 send 也走 adapter, 不旁路
```

**Streaming edit** (`src/plugin-sdk/channel-streaming.ts` + `extensions/discord/src/draft-stream.*`)：消息生命周期三态：

```
draft → live-preview (节流 edit) → final
```

发出 draft → tool 跑过程中持续 edit message → 最终 finalize。节流策略内置在 pipeline。

**Reaction approval** (`src/channels/ack-reactions.ts` + `extensions/discord/src/approval-native.ts`)：

```typescript
type ChannelApprovalAdapter {
    onApprove, onDecline, onTimeout
}
```

dangerous tool 触发时 bot 加 ✓/✗ emoji reaction → 用户点反应 → adapter 通知 dispatcher。完整 lifecycle（timeout / retry / cancel）。

**DurableMessageSendResult**：send 返回值含 message_id、edited_ids、retry 策略——支持 receipt tracking + delivery confirmation。我们这边 send 只返回 `bool`。

**Health check** (`health-check-adapter.ts`)：启动时 probe 每个 adapter 可用性，失败 graceful degradation——不让一个挂掉的 platform 拖垮整个 worker。

**注册**：plugin manifest（每个 extension 的 `openclaw.plugin.json` 声明 `channels` 能力），core loader 扫 `extensions/*/` 或 npm packages，动态加载 + lazy instantiation。

### 2.2 Hermes（chat-bot 专门项目）

Python 写的，对接 14+ 平台。设计哲学比 OpenClaw 简单——没有 Plugin SDK 那一层抽象，但单文件能塞下完整 adapter（base 1500 行）。

**BasePlatformAdapter ABC**

`gateway/platforms/base.py`：

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

5+ 个 async 抽象方法。统一返回 `SendResult` dataclass（含 `message_id` / `retryable` 标志）。

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

`MessageEvent` 是所有平台 message 的中性结构，adapter 负责 platform-native → MessageEvent 的翻译。dispatcher 只看 MessageEvent。

**Session key 二维隔离**

`build_session_key(source, group_sessions_per_user, thread_sessions_per_user)`：

```
DM:    agent:main:{platform}:dm:{chat_id}[:{thread_id}]
Group: agent:main:{platform}:group:{chat_id}[:{thread_id}][:{user_id}]
```

线程默认共享所有用户、组默认隔离每用户，可被 per-channel 配置覆盖。

**Progress Streaming**

`gateway/run.py:_edit_progress_message()`：

```python
async def _edit_progress_message(message_id: str, content: str):
    result = await adapter.edit_message(
        chat_id=source.chat_id,
        message_id=message_id,
        content=content,
    )
```

工具开始 → adapter.send 占位消息 → 拿到 `message_id` → 工具 stream 事件触发 `_edit_progress_message(message_id, latest_text)` → 最终用 `finalize=True` 收尾。

**Overflow 处理**：`_roll_progress_overflow_if_needed()`——当 progress 行超过 platform 字符限制时自动分组，第一组 edit 当前 bubble，后续组发新 bubble。

**高级机制**（这部分 hermes 真领先）

**Debounce 合并快速文本** (`base.py:2812-2876`)：

```python
class TextDebounceState:
    event: MessageEvent
    task: asyncio.Task | None
    first_ts, last_ts: float

async def _queue_text_debounce(session_key, event):
    """连续到达的同 session 文本合并成一条, delay 0.35s, hard cap 1.0s"""
```

用户连续发 3 条消息（"hi"、"你在吗"、"问个问题"），agent 收到的是合并后的一次 turn，不会触发 3 次 agent run。

**快速命令绕路** (`base.py:3205-3219`)：

```python
if should_bypass_active_session(cmd):   # /stop, /new, /reset, /approve
    await self._dispatch_active_session_command(...)
```

`/stop` `/approve` 这类命令直接走快速路径，不进 session 队列、不等 agent 当前任务结束。

**Retryable 错误分类**：

```python
@dataclass
class SendResult:
    message_id: Optional[str]
    retryable: bool = False
```

adapter 区分 transient（网络 / timeout，可重试）vs permanent（auth / permission，不重试），统一信号交 dispatcher 处理。

**Attachment 本地缓存**：

```python
def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """同步写到 cache_dir, 文件名 doc_{uuid12}_{原名}"""

def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """删除 24h+ 的缓存"""
```

Telegram URL 1 小时过期前下载到本地 → 后续 agent 能反复读 → 24h 后清理。

**DeliveryRouter（跨进程发送）**

`gateway/delivery.py`：

```python
class DeliveryTarget:
    """origin | local | telegram:123 | slack:..."""
    platform: Platform
    chat_id: Optional[str] = None

class DeliveryRouter:
    async def deliver(content, targets, ...) -> Dict:
        """Route to all targets via adapter instances."""
```

`outbound.send`-equivalent 也走 adapter 实例，不另写 raw HTTP path。

**Approval flow**

不用 reaction，用**文本命令**：

```python
async def _handle_slash_approve(self, event):
    """Handle /approve — unblock waiting agent thread(s)."""

_pending_approvals: Dict[str, Dict[str, Any]]   # session → pending
# tool 线程: Event.wait() 阻塞
# /approve 命令: Event.set() 唤醒
```

简单稳定。reaction 在 adapter 层有 `send_reaction` 实现但不是 approval 关键路径。

**Platform 注册**

`gateway/platform_registry.py`：

```python
@dataclass
class PlatformEntry:
    name, label, adapter_factory, check_fn,
    validate_config, install_hint

platform_registry.register(PlatformEntry(...))
adapter = platform_registry.create_adapter("slack", config)
```

built-in 走硬编码 fast path，plugin platform 通过 registry 自注册。

---

## 3. 三方对比

### 3.1 抽象层面

| 方面 | OpenProgram | OpenClaw (fork 来源) | Hermes |
|---|---|---|---|
| Base abstract method 数 | 1 (`run`) | 5+ (SendAdapter / LiveAdapter / ApprovalAdapter 等多接口) | 5+ (`send/edit/draft/typing/handoff`) |
| 中性消息结构 | 无 (平台原生 obj) | `ChannelMeta` 含 media/richtext/components | `MessageEvent` + `SessionSource` dataclass |
| Send 返回值 | bool | `DurableMessageSendResult` (含 message_id/edited_ids/retry 策略) | `SendResult` (含 message_id + retryable) |
| Dispatch signature | 同步 → str | 异步 streaming pipeline (draft → live → final) | 异步 → 流式事件 |
| Session 隔离 | `session_scope` 4 枚举 | `dmScope` hardcode + thread-bindings-policy | 二维 (chat × user × thread) |
| Edit/Reaction 接口 | 无 | 完整 (ChannelMessageLiveAdapterShape + ApprovalAdapter) | 内建 |
| 进度流 | 无 | 三阶段 (draft → live-preview → final, 节流内置) | edit_message + overflow 自动分组 |
| Approval 机制 | 无 | reaction ✓/✗ + onApprove/onDecline/onTimeout lifecycle | `/approve` 文本命令 |
| Debounce 合并 | 无 | 不详 | 0.35s delay + 1s hard cap |
| Retryable 信号 | 无 | DurableMessageSendResult 含 backoff 策略 | `SendResult.retryable` |
| Health check | 无 | `health-check-adapter.ts` 启动 probe | 不详 |
| Receipt tracking | 无 | 有 (DurableMessageSendResult 含 delivery confirmation) | 不详 |
| Structured replies | text only | embed/button/menu (ChannelMessageActionAdapter) | 部分 |
| Attachment 缓存 | 无 | 有 | UUID-前缀 + 24h 清理 |
| 出站 API | `outbound.send` 走 raw HTTP | 走 adapter 实例 (ChannelOutboundAdapter) | `DeliveryRouter(adapters: dict)` 走 adapter |
| 进程模型假设 | 多部署形态 (lib + worker + script) | 单 daemon 进程 | 单 gateway 进程 |
| Chunking 实现 | 5 份重复 | 平台 plugin 内统一 | 平台内统一 (`truncate_message`) |
| Platform 注册 | 硬编码 dict | Plugin SDK (manifest + dynamic loader) | hybrid (built-in + registry) |
| 语言 | Python | TypeScript | Python |

### 3.2 直接后果

| 我们想做的 feature | OpenProgram 改的范围 | hermes / OpenClaw 改的范围 |
|---|---|---|
| Progress streaming | 改 base + 4 adapter + outbound + `_conversation` = 6 处 | dispatcher 调 `adapter.edit_message` 一处 |
| Reaction approval | 4 adapter 各加 listener + adapter ↔ approval bridge | hermes: 一个 `/approve` slash handler; OpenClaw: ApprovalAdapter lifecycle 现成 |
| Edit message | base + 4 adapter + outbound = 6 处 | `adapter.edit_message` 一处 (已有) |
| 加新 platform (whatsapp) | adapter + `outbound._send_xx` + chunk + bindings + accounts | hermes: adapter + registry.register; OpenClaw: 新 extension 目录 + plugin.json |
| 修 chunking bug | 5 个文件同步改 | 1 个工具函数 |

### 3.3 我们 fork OpenClaw 后做的"减法"和"加法"

**减法（漏掉的）**：

| OpenClaw 有 | 我们继承时丢了 |
|---|---|
| Plugin SDK (50+ contract 文件) | 全部丢 — base.py 退化到 21 行 |
| ChannelMessageLiveAdapterShape (streaming edit) | 丢 |
| ChannelApprovalAdapter (reaction ✓/✗) | 丢 |
| DurableMessageSendResult (含 message_id + retry) | 退化成 bool |
| health-check-adapter | 丢 |
| Reconnection + exponential backoff | 丢 |
| Receipt tracking | 丢 |
| Message actions (button/menu) | 丢 |
| Thread binding policy | 退化成 peer_kind 字符串 |
| Structured replies (embed) | 退化成 text only |

**加法（我们引入的）**：

| OpenProgram 有 | OpenClaw 对应物 |
|---|---|
| `session_scope` 4 枚举可配置 | `dmScope` 在 channel runtime 写死 |
| `outbound.py` 无状态 cross-process sender | 没有独立 outbound，走 adapter 实例 |
| `setup.py` 一键交互式 enrollment | descriptor-driven setup plugin seam |

第二项—— `outbound.py`——值得单独说：从 fork 视角看是我们**主动加的**，OpenClaw 当初设计是 cross-process send 也走 adapter。但 OpenProgram 是双范式系统（详见 5.F），`outbound.send` 这个无状态、cron-friendly 入口正好对应 agentic-programming 范式（Python 主控同步调用），不该删。真正的问题是**实现层重复**（chunking 5 份、HTTP 调用各写一遍）而不是**入口存在性**——重构方向应是"两入口共享实现层"，不是"删一条入口"。

---

## 4. 当前的结构性缺陷（按严重度排序）

### 缺陷 1：base.py 是空壳

只规定 `run(stop)`，不规定 send/edit/react/chunk。导致每个 adapter "自由发挥"，平台间没有可强制的统一接口。

**症状**：4 个 adapter 的代码风格、错误处理、credentials 加载方式都不一样；type checker 没法发现 adapter 漏实现某个能力。

### 缺陷 2：两套发消息代码路径

```
Path A (adapter on_message 回复路径)    Path B (跨进程 outbound.send)
─────────────────────────────────────────────────────────────
discord.py  → discord.py SDK            outbound.py → raw HTTP
slack.py    → slack_sdk SDK             outbound.py → raw HTTP
telegram.py → outbound.send (HTTP)      outbound.py → raw HTTP  ← 已经走 B
wechat.py   → 内部 _send                outbound.py → raw HTTP
```

`MAX_MSG_CHARS` 5 份、`_chunk` 函数 5 份、credentials 加载 8+ 份。任何"如何把字节送到 platform"的修改都要在两条路上各做一遍。

注意：**两条入口的存在是合理的**（详见 5.F——它们服务两种范式：adapter 路径给 dispatcher 用、outbound 路径给 agentic-programming 主控用），问题不在"有两条入口"而在"两份独立实现"。重构应该让两条入口共享一份实现层，不是合并入口。

### 缺陷 3：dispatch_inbound 同步签名堵死 streaming

`(...) -> str` 一次性返回完整 reply。adapter 拿不到中间事件，因此：

- 不可能做 progress streaming（adapter 不知道 tool 在跑）
- 不可能做 typing indicator（adapter 不知道 LLM 在思考）
- 不可能做实时 edit（adapter 拿不到 token stream）

讽刺的是 dispatcher 内部 `_on_event` 已经 emit `tool_use` / `stream_event` / `tool_result` envelope（见 `agent/_event_parsing.py`），只是没回流给 channel——只对 webui 广播。

### 缺陷 4：没有 ChannelMessage 中性结构

每个 adapter 自己处理 platform-native object → 直接传 `(chat_id, user, text)` 三个字符串给 dispatch_inbound。要支持 reply / quote / thread / attachment 没有共同 schema 可挂。

如果将来 agent 想"引用之前那条消息"或"读图片附件"，每个 adapter 都要单独写一遍。

### 缺陷 5：_conversation.py 单文件 483 行 5 个职责

- 路由（binding + alias 查询）
- session_key 计算（scope + reset policy）
- session 创建 / 加载 / 持久化
- dispatcher 调用
- webui broadcast

按 OpenProgram 既定的 "hierarchical code structure" 偏好，这个量级该拆。但要等抽象层确定后再拆，不然拆完还要再返工。

### 缺陷 6：account_id 双重传递

```python
DiscordChannel(account_id="default")          # 构造参数
...
dispatch_inbound(..., account_id="default")    # 调用参数
```

同一个值在 adapter 实例和 dispatch 调用里各保管一份。如果以后想"一个进程一个 adapter 多个 account"，这设计是个 trip wire。

### 缺陷 7：反向依赖

`outbound._send_wechat` 反向 import `wechat._make_wechat_uin`。底层模块依赖叶子模块的私有函数——任何 wechat 内部重构都可能 break outbound。

### 缺陷 8：Session 隔离粒度单一

```python
peer_id = "{channel_id}_{user_id}"   # discord / slack
peer_id = "{chat_id}"                # telegram
```

把 chat 和 user 拼成一个字符串 peer_id，丢失了二维信息。`agent.session_scope` 只有 4 个枚举值（main / per-peer / per-channel-peer / per-account-channel-peer），不支持 "线程内共享" 这种 hermes 默认开启的模式。

### 缺陷 9：错误信号没分类

adapter `send` 返回 `bool`。失败原因（网络瞬时 vs auth 永久 vs rate limit）无法上传到 dispatcher。dispatcher 无法智能重试，也无法在 UI 上正确显示原因。

### 缺陷 10：Platform 注册硬编码

`channels/__init__.py:CHANNEL_CLASSES` 是硬编码 dict。要加新 platform 必须改 4 处（channels/、accounts/、bindings/、setup/）。无法做 plugin-provided platform。

---

## 5. 几个推论

**A. progress streaming 不是"加一个 feature"，是"把已经存在的事件流接到 channel"**

Dispatcher 已经 emit `tool_use` / `stream_event` envelope。`dispatch_inbound._on_event` 已经在订阅。差的只是：(1) channel 怎么订阅、(2) channel 怎么 edit 已发出去的消息。第二点要求 base.py 有 `edit_message`、adapter.send 要返回 message_id——这是 **抽象层重构**，不是 feature 加法。

**B. 直接给 4 个 adapter 各加 `edit_message` 会让缺陷 2 翻倍**

不重构 base 直接在 adapter 加方法：现在 4 个 send 实现 + 4 个 edit 实现 + 4 个 react 实现 = 12 份 platform code，再加 outbound 的 12 份 = 24 份。这是堆"两套代码"乘以"三种操作"的灾难。

**C. hermes 那些"高级"机制（debounce / 快速命令绕路 / retryable）不是必须现在做的**

它们是 hermes 跑大量产 traffic 后摸出来的优化。OpenProgram 现阶段没那个 QPS，可以先做对的抽象，等问题出现了再加。

**D. OpenClaw 为什么不能直接照搬——三层真实原因**

按重要性从低到高：

*第一层（最浅）：没有 Python 实现可以 copy-paste。* 整个 OpenClaw 是 TypeScript/Node.js（`pnpm-workspaces` + `tsdown` build），`src/bindings/` 只有 1 个 TS 文件，`packages/sdk/` 和 `packages/plugin-sdk/` 全是 TS。仅有的 5 个 `.py` 是 CI 脚本 / skill 工具，跟 channel 子系统无关。**OpenClaw 不提供 Python binding 也不提供 Python SDK。** 要复用 OpenClaw 必须把它的设计用 Python 重新实现一遍，不可能 import 进来。

但语言本身不构成借鉴障碍——TS interface → Python `Protocol` / `abc.ABC`，TS dataclass → `@dataclass`，TS async → asyncio，TS plugin manifest → `plugin.json`（我们 `openprogram/plugins/` 已经在做了）。设计模式跨语言通用。

*第二层：静态类型 vs 动态类型，影响"50+ contract 文件"的价值。* OpenClaw 在 TS 里写 50+ 个 `channel-*.ts` interface 文件，编译期能强制 plugin 实现完整，IDE 提示也准。同样的拆分用 Python `Protocol` 写出来——运行期不强制、IDE 提示弱（mypy 不是默认开的）。所以"50 个 contract 文件那种粒度"的拆分在 Python 里收益打折。这不影响**接口形状是否值得学**（值得），只影响**是否把每个接口拆成独立文件**（不值得）。

*第三层（最深）：async-first vs sync-with-threading 范式。* OpenClaw 全套 `async send/edit/typing/handoff`，dispatch 是 streaming pipeline（draft → live-preview → final）。Hermes 也是 async-first。我们 channel 当前是同步 + threading（每 adapter 一个 thread，`dispatch_inbound(...) -> str` 阻塞返回）。如果照搬 OpenClaw 的 async 设计，dispatch 流程要重写——不只是 base.py 改方法签名，而是 `dispatch_inbound` 改成 async generator，4 个 adapter 的事件循环要重新接入 asyncio。这是个真实的迁移成本，不是抽象层简单换名。

**E. 学什么、不学什么的分割线**

```
                          学 OpenClaw   学 hermes
─────────────────────────────────────────────────
接口设计 (what)
  send/edit/typing/approve  ✓ (更全)    ✓
  SendResult 含 retry        ✓           ✓
  Streaming lifecycle        ✓ (三态)    ✓ (单次 edit)
  Approval lifecycle         ✓ (完整)    ✓ (/approve 命令)
  Health check / probe       ✓           —

代码组织 (how)
  Plugin SDK 50+ contracts  ✗ 过度       —
  每 platform 70+ files     ✗ 过度       —
  单文件 base + adapter      —           ✓ 匹配
  async-first dispatch       ✓           ✓
```

两个项目可以分别学不同层面。接口形状 OpenClaw 做得更完整、更系统，照搬 method 签名 / lifecycle / 返回值结构没问题。代码组织规模 hermes 跟我们匹配——base ABC 一个文件、每 platform 一个文件、不搞 plugin manifest。这两个不冲突：拿 OpenClaw 的 `ChannelApprovalAdapter` / `ChannelMessageLiveAdapterShape` 的方法签名，落到 hermes-style 的 "base + 4 adapter 文件" 组织里，是最合理的方案。

**F. 兼容性——channel 重构 vs OpenProgram 既有范式**

这是真正最大的设计风险。OpenProgram 内部已经有两条范式并存：

```
范式 A: agentic programming (主推, README 第一段就是这个)
  Python 主控 → if/else/for/while 控制流
  @agentic_function 创建 Context 节点
  Runtime.exec 在被显式调用时才请求 LLM
  入口: 程序员写的 Python 代码

范式 B: agent loop (channel/webui chat 的实际路径)
  LLM 决定调什么工具、何时调
  process_user_turn → agent_loop → tool streaming
  Channel adapter 收到 user message → dispatch_inbound → 这条路
  入口: 外部 message
```

Channel 目前**只挂在范式 B 上**。这意味着：

1. **`outbound.send` 不是"错位的加法"**——它正是范式 A 需要的路径。一个 cron-driven @agentic_function 想给用户发"早上好"，不需要起 adapter instance、不需要订阅 stream 事件、不需要绑定 session lifecycle——raw HTTP 直接发就对。OpenClaw 的"统一走 adapter"、hermes 的 `DeliveryRouter(adapters: dict)` 都是**单 daemon 进程模型**下的合理设计——它们假设 cron scheduler + platform adapter + agent runtime 全在同一进程里跑，cron job 能从 dependency injection 拿到 adapter dict。

   我前面 audit 第 4 节缺陷 2 把 `outbound.send` 标为"两套发送代码"问题——这个判断对**实现层重复**是对的（chunking 复制 5 次、HTTP 调用各写一遍、credentials 加载多份），但**保留两个入口**本身是范式分工的合理结果，不该删。

   OpenProgram 多部署形态把这个进程模型假设给打破了：

   ```
   部署场景                              adapter instance 在哪
   ──────────────────────────────────────────────────────────
   openprogram worker 跑                   有 (worker 进程持有)
   用户写 Python 脚本 import @agentic     无
   cron 跑在 worker 外的另一个进程         无
   Jupyter notebook 实验                  无
   pytest 测试                            无
   ```

   范式 A 设计上就是"library 模式"——用户在自己的脚本里 import 用，**不假设 worker 进程存在**。所以 hermes / OpenClaw 那种"所有发送都走 adapter 实例"的组织方式在我们的多部署形态下行不通。outbound.send 这条路必须保留。

2. **正确的重构形状是：两个入口、一份实现**

   ```
   范式 A 入口: outbound.send_one_shot(channel, account, target, text)
   范式 B 入口: adapter.send_text(target, text) -> msg_handle
                adapter.edit_text(msg_handle, text)

                       ↓ 都调

   实现层: _post_message(channel, account, target, text, *, edit_of=None)
           HTTP 调用 + chunking + credentials 加载, 只一份
   ```

   这样 chunking 不再有 5 份、credentials 不再加载 8+ 次，但两条入口路径各自保留，分别服务两种范式。

3. **base.py 抽象重构不能"独占" channel**

   如果按 OpenClaw / hermes 的方式把 channel 重构成 async-first streaming pipeline，要确保**范式 A 仍然能用同步的方式发消息**。具体：base.py 的抽象方法是 async 没问题，但要在模块顶层暴露同步包装（`outbound.send_one_shot`），让 agentic_function 不必懂 asyncio 也能调。

4. **streaming edit 跟 agentic_function 兼容**

   范式 A 里一个 @agentic_function 可能要给 user 发中间进展（"已观察到登录页"、"已点击登录按钮"）。当前没有 API 让它这么做。重构后应当让这个能力可用——不是绑死给 dispatcher 的 stream pipeline 用，而是 agentic_function 也能拿到 msg_handle 自己 edit。

**G. WeChat 抽象适配最难**

iLink API 看起来不支持 edit_message（消息发出去不能改）。这意味着 base.py 加 `edit_message` 抽象后，wechat adapter 要么实现假的 `edit_message`（删旧发新）要么 raise NotImplementedError——前者改变语义，后者破坏统一接口。Hermes 怎么处理这种限制需要再调研（IRC 也有类似问题）。

---

## 6. 待定的问题

下一步讨论时要决的事：

1. 要不要做 base.py 抽象重构？工作量 3-4h，没新 feature，但是后续所有 feature 的地基
2. 重构粒度：抽到 hermes 那样（async send/edit/typing/draft 5+ 方法）还是先做最小（send 返回 message_id + edit_message）？
3. 中性 ChannelMessage 结构现在加还是等 reply/quote 需求出现再加？
4. `outbound.send` 不删（它服务范式 A），但实现层要不要跟 adapter 的 send 合并到一个 `_post_message` 函数，去掉 5 处重复？
5. Approval 机制走 hermes 的 `/approve` 命令（简单稳）还是 OpenClaw 的 reaction lifecycle（UX 直观但状态复杂）？
6. WeChat 不支持 edit 怎么处理——`edit_message` raise vs 假实现 vs base 接口本身做成 optional？
7. session_scope 要不要扩成 hermes 的二维（chat × user × thread）？OpenClaw 的 thread-bindings-policy 提供另一种思路

这份文档先到这里。具体方案等你看完反馈后再定。
