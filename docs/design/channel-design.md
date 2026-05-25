# Channel 子系统设计

外部 chat 平台 (Telegram / Discord / Slack / WeChat) 通过这个子系统跟 OpenProgram 双向通讯：用户在 platform 上发消息触发 agent，agent 回复通过同一条 channel 发回去。

本文档描述**实施完成后的当前形态**。设计演化历史 + 修复过的缺陷列表见 [`channel-audit.md`](./channel-audit.md)。

## 1. 整体形态

```
┌─────────────────────┐       ┌──────────────────────┐
│  外部用户/Telegram   │       │  你自己写的 Python   │
│  Discord/Slack/WX   │       │  脚本/cron/jupyter   │
└──────────┬──────────┘       └──────────┬───────────┘
           │ 用户发消息进来                │ 想给某人发消息
           ▼                              ▼
  ┌──────────────────┐            ┌────────────────────┐
  │ telegram.py 等   │            │   outbound.py      │  ← 入口 A
  │ 4 个 adapter     │            │   send(...)        │     一次性发, 不需要长跑进程
  │ - 长轮询/事件循环│            └─────────┬──────────┘
  │ - parse 出统一   │                      │
  │   ChannelMessage │                      │
  └────────┬─────────┘                      │
           │                                │
           ▼                                │
  ┌───────────────────────────┐             │
  │   dispatch_inbound        │             │
  │   (流量中枢, 串起所有事)  │             │
  │                           │             │
  │   ① 路由: 决定哪个 agent  │             │
  │   ② 算 session_key        │             │
  │   ③ 加载 session 状态     │             │
  │   ④ 调 agent 跑这一回合   │             │
  │   ⑤ progress streaming    │             │
  │   ⑥ 推 webui WS           │             │
  └────────┬──────────────────┘             │
           │                                │
           │ 边跑边 edit 占位/最终 reply    │
           ▼                                ▼
  ┌─────────────────────────────────────────────────┐
  │           _transport.py (统一底层)              │  ← 唯一往外发字节的地方
  │                                                 │
  │   post_message(平台, 账号, 收信人, 文本)        │
  │   patch_message(平台, 账号, 收信人, msg_id, 文本)│
  │                                                 │
  │   返回 SendResult {                             │
  │     ok, message_id, error_kind, retryable       │
  │   }                                             │
  └────────┬────────────────────────────────────────┘
           │ HTTPS POST/PATCH
           ▼
  Telegram API / Discord API / Slack API / WeChat iLink API
```

## 2. 端到端用例：用户发消息进来 → bot 回复

**示例**：你在 Telegram 给 bot 发"帮我看下当前目录有什么 Python 文件"。

```
1. Telegram 服务器把消息推给 bot
   → openprogram/channels/telegram.py 在长轮询, 收到 update dict

2. _handle_update(update) 内部:
   a. 抽 text = "帮我看下当前目录有什么 Python 文件"
   b. 构造 ChannelMessage {
        text=..., chat_id="123", user_id="456",
        user_display="zhangsan", chat_type="direct",
        ts=1716000000, reply_to_id="", thread_id="",
      }
   c. 调 dispatch_inbound(channel="telegram", account_id="default",
                          peer_kind="direct", peer_id="123",
                          user_text=text, user_display="zhangsan",
                          progress_stream=True)

3. dispatch_inbound 内部 (在 _conversation.py):
   a. 查 bindings → 决定用 "main" agent
   b. 算 session_key = "default_direct_123" (在 _session_routing.py)
   c. 加载 / 创建 session (在 _session_store.py 调 SessionDB)
   d. 发占位消息: _transport.post_message("telegram", "default", "123",
                                          "⏳ working...")
      返回 SendResult{ok=True, message_id="9001"}
      → MessageHandle{platform="telegram", account="default",
                      target="123", message_id="9001"}
   e. 调 process_user_turn(req, on_event=_on_event) 跑 agent

4. Agent 内部决定调 bash tool 跑 `ls *.py`:
   a. dispatcher emit tool_use envelope → _on_event 拿到
   b. _on_event 看到 tool_use → progress_lines = ["⚙ bash"]
   c. 节流满足 (距上次 edit >1s) → _transport.patch_message(
        "telegram", "default", "123", "9001", "⚙ bash")
      → Telegram 上那条 "⏳ working..." 变成 "⚙ bash"

5. bash 跑完返回 "a.py b.py c.py":
   a. dispatcher emit tool_result envelope → _on_event 拿到
   b. progress_lines = ["✓ bash"]  (把 ⚙ 换成 ✓)
   c. 节流满足 → patch_message edit 成 "✓ bash"

6. Agent 综合 bash 输出写出最终回复 "找到 3 个 Python 文件: a.py / b.py / c.py":
   a. process_user_turn 返回, result.final_text = 这段话
   b. dispatch_inbound 强制 edit (跳节流): _transport.patch_message
      把 "9001" 改成完整回复
   c. 持久化到 SessionDB, broadcast 给 webui
   d. dispatch_inbound 返回 None

7. telegram.py 拿到 None → 不发任何 reply (因为已经 edit 进去了)
   用户在 Telegram 看到那条占位 "⏳..." 已经长成完整回复
```

## 3. 用例 B：cron / @agentic_function 主动发消息

```python
from openprogram.channels.outbound import send

# 在任何 Python 脚本里, 不需要 worker 在跑
send("telegram", "default", "1234", "早上好")
```

发生的事：

```
1. outbound.send 调 _transport.post_message
2. _transport.post_message 拿凭据 → HTTPS POST sendMessage
3. SendResult 返回 → outbound.send 返回 True/False
4. 脚本继续
```

**没有**：adapter 实例、worker 进程、session、agent 调用、webui broadcast。一行调用即发即走。

这就是为什么 outbound.send 是单独入口而不是走 adapter——cron 脚本根本没有 adapter 实例在跑。

## 4. 五条核心设计原则

### 4.1 两个入口、一份实现

| 入口 | 用途 | 状态 | 谁调 |
|---|---|---|---|
| `outbound.send` | 一次性发, 不需要长跑进程 | 无状态 | cron 脚本 / jupyter / @agentic_function / webui (回复) |
| `Channel.send_text` + `edit_text` | 持有 message_id 后续 edit | 有状态 | dispatch_inbound progress streaming |

底下都调同一个 `_transport.post_message` / `patch_message`。HTTP 调用 / 凭据加载 / chunking 只有一份代码。

为什么不合并入口：cron 脚本 / jupyter 临时调用没有 worker 进程在跑，需要无状态的 raw HTTP 接口；progress streaming 需要持有 message_id 才能 edit，需要 stateful 接口。两类需求不同，但底层共享。

### 4.2 dispatch_inbound 是流量中枢

所有从外部进来的消息都走它。它本身不做具体活儿，只串流程：

```python
def dispatch_inbound(*, channel, account_id, peer_kind, peer_id,
                    user_text, user_display="", progress_stream=False) -> Optional[str]:
    # 委托给独立模块
    agent_id = bindings.route(...) or session_aliases.lookup(...)
    session_key = _session_routing.session_key_for_agent(...) + apply_reset_policy(...)
    meta, _ = _session_store.load_or_init_session(...)

    # 可选: 发占位 + 订阅 stream → progress edit
    if progress_stream:
        placeholder_handle = _transport.post_message(... "⏳ working...")

    # 跑 agent
    result = process_user_turn(req, on_event=...)

    # 持久化 + broadcast
    _broadcast.broadcast_channel_turn(...)

    return result.final_text  # 或 None (progress 模式)
```

`_conversation.py` 自己只有 283 行（之前一个文件 588 行 5 职责）。

### 4.3 平台差异封到底层

`_transport.py` 是**唯一**调 HTTP 往外发的地方。Telegram 的 `editMessageText`、Discord 的 `PATCH /messages/{id}`、Slack 的 `chat.update`、WeChat 的 iLink 协议——全都在这里。

adapter 类 (`telegram.py` 等) 只负责：(a) 连服务器的事件循环、(b) 把 platform-native 对象 parse 成 `ChannelMessage`。**不负责发消息**——发消息是 dispatch_inbound 通过 `_transport` 干的。

### 4.4 错误信号结构化

`_transport.post_message` 返回 `SendResult`：

```python
@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error_kind: str = ""          # auth / rate_limit / bad_target / network / not_supported / unknown
    error_detail: str = ""        # human-readable 一行
    retryable: bool = False       # 瞬态可重试 vs 永久失败

    def __bool__(self): return self.ok
```

调用方可以做"token 失效请重新登录" vs "chat_id 错误" vs "稍后重试"的区分。

`outbound.send` 保留 bool 签名（兼容旧 caller），`outbound.send_full()` 暴露完整 SendResult。`Channel.send_text` / `edit_text` 同理，有 `_full` 变体。

### 4.5 plugin 扩展点

要加新平台（比如 WhatsApp）不用改源码：

**方式 A** — `pyproject.toml` entry_point（推荐）：

```toml
[project.entry-points."openprogram.channels"]
whatsapp = "my_pkg.whatsapp:WhatsAppChannel"
```

启动时 `importlib.metadata.entry_points(group="openprogram.channels")` 自动扫描。

**方式 B** — `register_channel` imperative 调用：

```python
from openprogram.channels import register_channel
from my_pkg.whatsapp import WhatsAppChannel

register_channel("whatsapp", WhatsAppChannel)
```

适合 jupyter 临时挂或 plugin hooks 里动态注册。

内置 4 个平台优先，同名 plugin 被无声忽略。

## 5. 模块清单

```
openprogram/channels/   14 文件
├── base.py              Channel ABC + MessageHandle + send_text/edit_text(_full)
├── _transport.py        SendResult + 4 个平台 HTTP post/patch (统一底层)
├── _message.py          ChannelMessage 入站中性结构 dataclass
├── outbound.py          入口 A: send / send_full (薄包装)
├── _conversation.py     dispatch_inbound 主流程 + progress streaming
├── _session_store.py    session 路径 / 创建 / 加载 / 保存
├── _session_routing.py  session_key + reset policy
├── _broadcast.py        webui WS push (channel_turn / session_updated)
├── __init__.py          CHANNEL_CLASSES proxy + register_channel + entry_points
├── telegram.py          Telegram bot 长轮询入站
├── discord.py           Discord bot Gateway 入站
├── slack.py             Slack Socket Mode 入站
├── wechat.py            WeChat iLink 长轮询入站 (含 QR 登录)
├── accounts.py          凭据存储
└── bindings.py          (channel, account, peer) → agent 路由表
```

读法：每个模块只跟它声明的 caller 打交道，不存在循环依赖。

| 模块 | 职责 | 典型 caller |
|---|---|---|
| `_transport.py` | 唯一往外发字节, 4 个平台 HTTP | outbound + base.send_text |
| `_message.py` | ChannelMessage parse 中性结构 | adapter 入口 |
| `base.py` | Channel ABC + MessageHandle | adapter 子类、dispatch_inbound |
| `outbound.py` | 入口 A (一次性发) | cron 脚本、jupyter、@agentic_function |
| `_conversation.py` | dispatch_inbound 主流程 | 4 个 adapter 的 on_message |
| `_session_store.py` | session 加载/保存 | dispatch_inbound |
| `_session_routing.py` | session_key 计算 | dispatch_inbound |
| `_broadcast.py` | webui WS push | dispatch_inbound |
| `telegram.py` 等 | 入站事件循环 + parse | worker 启动时实例化 |
| `__init__.py` | CHANNEL_CLASSES + plugin 注册 | webui list_status / worker |
| `accounts.py` | 凭据存储 | 所有 _transport 函数 |
| `bindings.py` | inbound 路由 | dispatch_inbound |

## 6. 支持的平台

| 平台 | 入站机制 | 出站机制 | progress streaming | 备注 |
|---|---|---|---|---|
| **Telegram** | 长轮询 `getUpdates` (无 webhook 依赖) | bot API `sendMessage` / `editMessageText` | ✓ | bot token, public Bot API |
| **Discord** | discord.py Gateway WS | REST `POST /messages` / `PATCH /messages/{id}` | ✓ | bot token, intents.message_content |
| **Slack** | Socket Mode (slack_sdk) | `chat.postMessage` / `chat.update` | ✓ | bot_token (xoxb-) + app_token (xapp-) |
| **WeChat** | iLink `getupdates` 长轮询 | iLink `sendmessage` | ✗ (iLink 不支持 edit) | 个微扫码登录, 无企业认证门槛 |

详见 [`channel-platforms.md`](./channel-platforms.md) (如有，否则各 adapter 顶部 docstring)。

## 7. 用户入口

### 7.1 CLI

完整的命令树 (`openprogram channels`)：

```
openprogram channels list                          显示每个 platform/account 状态
openprogram channels setup                         交互式 setup wizard

openprogram channels accounts
  ├── list                                         列所有账号
  ├── add <channel> --id <name>                    新建一个账号 slot
  ├── login <channel> --id <name>                  交互式录入凭据
  │     - telegram/discord/slack: getpass 粘贴 token
  │     - wechat: 启动 iLink QR 扫码流程
  └── rm <channel> <account_id>                    删账号 + 关联 bindings

openprogram channels bindings
  ├── list                                         列所有路由规则
  ├── add <agent_id> --channel <ch> [--account <acct>] [--peer <peer> --peer-kind <kind>]
  │                                                  把 (channel, account, peer) 路由到 agent
  └── rm <binding_id>                              删一条路由
```

### 7.2 TUI

| 入口 | 实现 | 行数 |
|---|---|---|
| `/channel` slash command | `cli/src/commands/handler.ts` 触发 `pickers/channel.tsx` | 374 行 picker |
| Channel 实时活动 feed | `cli/src/components/ChannelActivityFeed.tsx` | 66 行 |
| WS handler 显示 channel turn | `cli/src/screens/repl/wsHandlers/handleChannelTurn.ts` | — |

`/channel` 工作流：选 channel → 选 account → 引导用户用 `/attach` 把当前对话绑到 channel peer。

### 7.3 Web UI

| 入口 | 实现 | 状态 |
|---|---|---|
| Topbar channel popover | `web/components/chat/top-bar/channel-menu.tsx` (168 行) | ✓ 完整 |
| Health badge status API | `/api/channels/{platform}/{account_id}/status` 返回 alive/stale/unknown | ✓ 完整 |
| 独立 settings 页 | — | **⚠ 缺失** |

Web 端目前**没有 `/settings/channels` 配置页**。所有账号 / bindings 管理只能走 CLI。后续要做 Web 端配置 UI 的话，对应 API 加在 `openprogram/webui/routes/channels.py` 里。

## 8. plugin / 扩展未来工作

| 当前状态 | 后续若需扩展 |
|---|---|
| 4 个内置平台 | 加 WhatsApp / Signal / Matrix / LINE 等 — 写 `Channel` 子类 + entry_point 注册 |
| ChannelMessage 已含 `reply_to_id` / `thread_id` / `attachments` 字段 | dispatch_inbound 暂不消费, 等真实需求出现 (reply quote / thread 隔离 / 附件读取) 再接 |
| Reaction approval (✓/✗ 确认 dangerous tool) | 未实现, hermes/OpenClaw 都有, 等用户提需求再做 |
| Token-level text streaming | 目前只在 tool 边界 edit, 没有 reply text delta 实时 edit (rate limit 风险) |

## 9. 参考

- [`channel-audit.md`](./channel-audit.md) — 设计演化历史 + 已修缺陷清单 + 跟 OpenClaw / Hermes 的对比
- 各 adapter 顶部 docstring — platform-specific 协议细节
