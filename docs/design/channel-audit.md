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
| 出站 API | `outbound.send` 走 raw HTTP | 走 adapter 实例 (ChannelOutboundAdapter) | `DeliveryRouter` 走 adapter |
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

第二项—— `outbound.py`——值得单独说：我之前 audit 第 2 节把它标为"两套发送代码路径"的元凶。从 fork 视角看，它是我们**主动加的**。OpenClaw 的设计要求 cross-process send 也走 adapter（统一一条路径），我们当时选了 raw HTTP 旁路，理由可能是简化或避免多进程共享 adapter 状态——代价就是现在两套维护、5 处 chunking 重复。

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

**D. OpenClaw 的 Plugin SDK 那套对我们过度**

50+ contract 文件 + 每 platform 70+ files 是企业级 TS 的玩法，跟我们 Python codebase 规模和团队规模不匹配。可以借鉴**接口形状**（ChannelMessageLiveAdapterShape 这种 send/edit/finalize lifecycle），但**实现层级**应该跟 hermes 学——一个 base + 一个 adapter 文件，不搞 plugin manifest 那一套。

**E. 我们应该参照的是 hermes 不是 OpenClaw**

虽然代码 fork 来源是 OpenClaw，但 OpenClaw 现在做得太重。Hermes 是 Python 写的、规模和我们对得上、设计哲学也合（base ABC + Python dataclass + async + 中性 MessageEvent）。重构方向应当对齐 hermes，而不是把 OpenClaw 整套搬回来。

**F. WeChat 抽象适配最难**

iLink API 看起来不支持 edit_message（消息发出去不能改）。这意味着 base.py 加 `edit_message` 抽象后，wechat adapter 要么实现假的 `edit_message`（删旧发新）要么 raise NotImplementedError——前者改变语义，后者破坏统一接口。Hermes 怎么处理这种限制需要再调研（IRC 也有类似问题）。

---

## 6. 待定的问题

下一步讨论时要决的事：

1. 要不要做 base.py 抽象重构？工作量 3-4h，没新 feature，但是后续所有 feature 的地基
2. 重构粒度：抽到 hermes 那样（async send/edit/typing/draft 5+ 方法）还是先做最小（send 返回 message_id + edit_message）？
3. 中性 ChannelMessage 结构现在加还是等 reply/quote 需求出现再加？
4. `outbound.send` 是删了改走 adapter，还是保留作为"快路径" fallback？OpenClaw 当初是统一走 adapter 的，我们 fork 后引入的 raw HTTP 路是错位
5. Approval 机制走 hermes 的 `/approve` 命令（简单稳）还是 OpenClaw 的 reaction lifecycle（UX 直观但状态复杂）？
6. WeChat 不支持 edit 怎么处理——`edit_message` raise vs 假实现 vs base 接口本身做成 optional？
7. session_scope 要不要扩成 hermes 的二维（chat × user × thread）？OpenClaw 的 thread-bindings-policy 提供另一种思路

这份文档先到这里。具体方案等你看完反馈后再定。
