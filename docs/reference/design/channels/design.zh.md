# Channel 子系统设计

外部 chat 平台 (Telegram / Discord / Slack / WeChat) 通过这个子系统跟 OpenProgram 双向通讯：用户在 platform 上发消息触发 agent，agent 回复通过同一条 channel 发回去。

本文档描述该层的结构与消息流转。该层守住的要求与不变量，以及跟 OpenClaw / hermes 的对比，见 [`audit.md`](./audit.md)。

## 1. 整体形态

```
┌─────────────────────┐       ┌──────────────────────┐
│  外部用户/Telegram   │       │  你自己写的 Python   │
│  Discord/Slack/WX   │       │  脚本/cron/jupyter   │
└──────────┬──────────┘       └──────────┬───────────┘
           │ 用户发消息进来                │ 想给某人发消息
           ▼                              ▼
  ┌──────────────────────┐        ┌────────────────────┐
  │ implementations/*.py │        │   outbound.py      │  ← 入口 A
  │ 4 个 adapter         │        │   send(...)        │     一次性发, 不需要长跑进程
  │ - 长轮询/事件循环    │        │   send_file(...)   │
  │ - parse 出统一       │        └─────────┬──────────┘
  │   ChannelMessage     │                  │
  │ (worker 经 base.     │                  │
  │  run_forever 跑 —    │                  │
  │  崩溃退避自动重连)   │                  │
  └────────┬─────────────┘                  │
           │ base.Channel.handle_inbound    │
           │ (每消息一线程 → _access 门禁   │
           │  → 附件下载 → quoted 块 →      │
           │  dispatch; 降级回发)           │
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
  │   post_file(平台, 账号, 收信人, 路径, caption)  │
  │                                                 │
  │   每 chunk 流水线: MAX_CHARS 切分 →             │
  │   _format.render (平台线上格式) →               │
  │   HARD_CAPS 二次切分 → 限流退避重试             │
  │   (优先 Retry-After, 共 3 次尝试)               │
  │                                                 │
  │   返回 SendResult {                             │
  │     ok, message_id, error_kind, retryable,      │
  │     retry_after                                 │
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
   → openprogram/channels/implementations/telegram.py 在长轮询,
     收到 update dict

2. _handle_update(update) 内部:
   a. 抽 text = "帮我看下当前目录有什么 Python 文件"
      (另抽附件 → Attachment 条目、reply_to_message 文本 → quoted_text;
      群聊里 require_mention 设置可以在这里做 @提及 门槛并剥掉提及)
   b. 构造 ChannelMessage {
        text=..., chat_id="123", user_id="456",
        user_display="zhangsan", chat_type="direct",
        ts=1716000000, reply_to_id="", quoted_text="", thread_id="",
        attachments=(),
      }
   c. 交给 base.Channel.handle_inbound(ch_msg) — base 起一个 per-message
      daemon 线程 (turn 停在 runtime.ask 时不能堵 poll loop), 线程里:
        c1. _access.decide_inbound_sender(平台, 账号, user_id) — allowlist/配对
            门禁. 未知发信人 → 消息丢弃、回一个配对码; 批准只能走本机
            CLI (`channels access approve`) 或本地 Web UI，永远不由 channel
            文本触发（注入边界）。
        c2. _attachments.download_inbound(...) — 文件落到账号状态目录;
            小图片转成 TurnRequest image block, 每个文件在 user_text
            里追加一行 [attachment: 路径].
        c3. quoted_text (若有) 组装成 "> 引用" 块放在最前.
        c4. dispatch_inbound(channel="telegram", account_id="default",
                             peer_kind="direct", peer_id="123",
                             user_text=text, user_display="zhangsan",
                             progress_stream=True, attachments=[...])
      (peer_id 来自 Channel.peer_id_for: telegram 用 chat_id — 账号设置
       group_sessions=per-user 时群聊变 "{chat_id}_{user_id}" — wechat
       用 chat_id, discord/slack 用 "{chat_id}_{user_id}")

3. dispatch_inbound 内部 (在 _conversation.py):
   a. 查 bindings → 决定用 "main" agent
   b. 算 session_key = "default_direct_123" (在 _session_routing.py)
   b2. 拿 per-session 锁 — 同一 session 的并发 turn 排队 (dispatcher
       本身无锁, 交错跑会写坏历史); 不同 session 并行. /answer ·
       /decline 命令在锁之前处理, 答问题不会跟等答案的 turn 互相死锁.
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

7. handle_inbound 拿到 None → 不发任何 reply (因为已经 edit 进去了).
   用户在 Telegram 看到那条占位 "⏳..." 已经长成完整回复.
   任何降级路径 (占位发不出 / 平台不能 edit / WeChat) 下
   dispatch_inbound 返回 reply 字符串, handle_inbound 经
   Channel.send_text → _transport 发回 (按 per-platform MAX_CHARS
   上限 chunk). adapter 不再持有任何 SDK 直发路径.
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

## 4. 六条核心设计原则

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

    # 以下都在 per-session 锁内跑: 同 session turn 串行, 不同 session 并行
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

`_conversation.py` 只保留这条流程；路由、session 存储、broadcast 各自在独立模块里。

### 4.3 平台差异封到底层

`_transport.py` 是**唯一**调 HTTP 往外发的地方。Telegram 的 `editMessageText`、Discord 的 `PATCH /messages/{id}`、Slack 的 `chat.update`、WeChat 的 iLink 协议、各平台文件上传（`post_file`：Telegram sendPhoto/sendDocument、Discord multipart、Slack external-upload；WeChat 缺席 → `not_supported`）——全都在这里。per-platform 消息长度上限（`MAX_CHARS`）、chunking、markdown 渲染（经 `_format`）、渲染后硬上限二次切分（`HARD_CAPS`）、限流重试都单点收敛在同一条流水线里。

`_format.py` 是出站格式化单点：agent markdown → Telegram HTML（全量转义，任意输入都合法；API 仍拒绝时 Telegram poster 剥标签降级纯文本重发）、Slack mrkdwn、Discord 透传、WeChat 纯文本。代码围栏与 inline code 用占位符保护，行内转换永远不碰代码体。

adapter 类 (`implementations/telegram.py` 等) 只负责：(a) 连服务器的事件循环、(b) 把 platform-native 对象 parse 成 `ChannelMessage`（文本、附件元数据、被引用文本）。parse 之后的一切——per-message 线程派发、`_access` 门禁、附件下载、quoted 块、调 `dispatch_inbound`、降级（非 streaming）路径的回发——统一在 `base.Channel.handle_inbound` 里只写一遍。**adapter 不发消息**——出站一律经 `send_text` / `send_file` 走 `_transport`。

worker 经 `base.Channel.run_forever(stop)` 跑每个 adapter：`run()` 抛异常 → 指数退避重连（5 秒翻倍、封顶 300 秒、存活满 60 秒后重置）；`run()` 正常 return 表示"永久停止, 需人工处理"（如 WeChat token 失效），不再重启。

### 4.4 错误信号结构化

`_transport.post_message` 返回 `SendResult`：

```python
@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error_kind: str = ""          # auth / rate_limit / bad_target / network / not_supported / format / unknown
    error_detail: str = ""        # human-readable 一行
    retryable: bool = False       # 瞬态可重试 vs 永久失败
    retry_after: float = 0.0      # 平台明示的等待秒数 (Retry-After / body), 0 = 没说

    def __bool__(self): return self.ok
```

调用方可以做"token 失效请重新登录" vs "chat_id 错误" vs "稍后重试"的区分。`rate_limit` 失败在 `_transport` 内部就地重试（优先 Retry-After，兜底 1s / 3s，睡眠封顶 30s，共 3 次尝试）；逃逸到调用方的已经是重试后的最终结论，并带 error kind 落日志。

`outbound.send` 保留 bool 签名（兼容旧 caller），`outbound.send_full()` 暴露完整 SendResult。`Channel.send_text` / `edit_text` 同理，有 `_full` 变体；`Channel.send_file` / `outbound.send_file` 直接返回 SendResult。

### 4.5 入站 access 门禁

`_access.py` 给每个 (channel, account) 维护一份 `access.json`。准入策略固定为 `pairing`：文件只包含按平台稳定 user id 索引的 `allowlist`，以及最多 3 个 `pending` 请求；旧文件中的 `policy: "open"` 会被忽略。新请求生成排除 `0O1I` 的 8 位大写码，1 小时过期；同一发信人在有效期内不重复提示，超过 pending 上限的请求静默忽略。`base._dispatch_and_reply` 在路由之前调 `decide_inbound_sender`，因此未配对发信人永远到不了 `dispatch_inbound`。写操作（`approve` / `approve_user` / `revoke`）只暴露给本机 CLI 和 loopback Web UI；入站路径只能读 allowlist、生成 pending 码，任何 channel 消息都无法批准任何人。

### 4.6 plugin 扩展点

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
openprogram/channels/
├── base.py              Channel ABC + MessageHandle + handle_inbound
│                        (per-message 线程 → 门禁 → 附件 → quoted 块 →
│                        dispatch; 降级回发) + run_forever (崩溃退避重连)
│                        + send_text/edit_text(_full) + send_file
├── _transport.py        SendResult + MAX_CHARS/HARD_CAPS + chunk →
│                        render → retry 流水线 + 4 个平台 HTTP
│                        post/patch/文件上传 (统一底层)
├── _format.py           出站 markdown → telegram HTML / slack mrkdwn /
│                        discord 透传 / wechat 纯文本
├── _access.py           入站 allowlist + 配对门禁 (access.json;
│                        批准只走本地 owner 界面)
├── _attachments.py      入站附件下载 + turn 输入转换
│                        (image block + 路径注记)
├── _message.py          ChannelMessage + Attachment 中性结构 dataclass
├── outbound.py          入口 A: send / send_full / send_file (薄包装)
├── _conversation.py     dispatch_inbound 主流程 + per-session 锁
│                        + progress streaming
├── _session_store.py    session 路径 / 创建 / 加载 / 保存
├── _session_routing.py  session_key + reset policy
├── _broadcast.py        WS 帧走事件总线 (emit_ws_frame → ws.frame;
│                        webui 订阅后原样广播 — channels 不 import webui)
├── _question_bridge.py  把待答的 runtime.ask 问题推进聊天
├── _question_commands.py /answer · /decline 文本命令处理
├── _heartbeats.py       adapter 心跳注册表
├── __init__.py          CHANNEL_CLASSES proxy + register_channel + entry_points
├── implementations/
│   ├── telegram.py      Telegram bot 长轮询入站 (group_sessions /
│   │                    require_mention 账号设置)
│   ├── discord.py       Discord bot Gateway 入站
│   ├── slack.py         Slack Socket Mode 入站
│   └── wechat.py        WeChat iLink 长轮询入站 (含 QR 登录)
├── setup.py             `openprogram channels setup` 向导
├── worker.py            向后兼容的 worker import shim
├── accounts.py          凭据存储 + 账号行为设置
└── bindings.py          (channel, account, peer) → agent 路由表
```

读法：每个模块只跟它声明的 caller 打交道，不存在循环依赖。

| 模块 | 职责 | 典型 caller |
|---|---|---|
| `_transport.py` | 唯一往外发字节: chunk → render → 硬上限二次切分 → 限流重试, 4 个平台 HTTP (文本/编辑/文件) | outbound + base.send_text/send_file |
| `_format.py` | markdown → 各平台线上格式 | _transport |
| `_access.py` | allowlist + 配对门禁 (`access.json`) | base.handle_inbound (判定), CLI / loopback Web UI (写操作) |
| `_attachments.py` | 入站附件下载 + turn 输入转换 | base.handle_inbound |
| `_message.py` | ChannelMessage + Attachment 中性结构 | adapter 入口 |
| `base.py` | Channel ABC + MessageHandle + handle_inbound + run_forever | adapter 子类、worker、dispatch_inbound |
| `outbound.py` | 入口 A (一次性发 / send_file) | cron 脚本、jupyter、@agentic_function |
| `_conversation.py` | dispatch_inbound 主流程 + per-session 锁 | base.handle_inbound |
| `_session_store.py` | session 加载/保存 | dispatch_inbound |
| `_session_routing.py` | session_key 计算 | dispatch_inbound |
| `_broadcast.py` | WS 帧上事件总线 (`ws.frame`) | dispatch_inbound |
| `implementations/*.py` | 入站事件循环 + parse (含附件/引用、telegram 群设置) | worker 启动时经 run_forever 实例化 |
| `__init__.py` | CHANNEL_CLASSES + plugin 注册 | webui list_status / worker |
| `accounts.py` | 凭据存储 + 行为设置 (ACCOUNT_SETTINGS) | 所有 _transport 函数、adapter |
| `bindings.py` | inbound 路由 | dispatch_inbound |

## 6. 支持的平台

| 平台 | 入站机制 | 出站机制 | progress streaming | 附件接收 | 文件发送 | 备注 |
|---|---|---|---|---|---|---|
| **Telegram** | 长轮询 `getUpdates` (无 webhook 依赖) | bot API `sendMessage` (HTML) / `editMessageText` / `sendPhoto`+`sendDocument` | ✓ | photo + document (file_id → getFile) | ✓ | bot token, public Bot API; group_sessions / require_mention 设置 |
| **Discord** | discord.py Gateway WS | REST `POST /messages` / `PATCH /messages/{id}` / multipart 上传 | ✓ | ✓ (CDN URL) | ✓ | bot token, intents.message_content |
| **Slack** | Socket Mode (slack_sdk) | `chat.postMessage` (mrkdwn) / `chat.update` / external-upload | ✓ | ✓ (`url_private` + bearer) | ✓ | bot_token (xoxb-) + app_token (xapp-) |
| **WeChat** | iLink `getupdates` 长轮询 | iLink `sendmessage` (纯文本) | ✗ (iLink 不支持 edit) | ✗ (协议只有文本) | ✗ (`not_supported`) | 个微扫码登录, 无企业认证门槛 |

平台差异以本表和各 adapter 顶部 docstring 为准。

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
  ├── set <channel> <key> <value> --id <name>      行为设置
  │     (telegram: group_sessions=shared|per-user, require_mention=on|off)
  └── rm <channel> <account_id>                    删账号 + 关联 bindings

openprogram channels access
  ├── list [<channel>]                             allowlist + 待批配对码
  ├── approve <channel> <code> --id <name>         按配对码批准
  ├── allow <channel> <user_id> --id <name>        直接按 user id 加 allowlist
  └── revoke <channel> <user_id> --id <name>       移除发信人

openprogram channels bindings
  ├── list                                         列所有路由规则
  ├── add <agent_id> --channel <ch> [--account <acct>] [--peer <peer> --peer-kind <kind>]
  │                                                  把 (channel, account, peer) 路由到 agent
  └── rm <binding_id>                              删一条路由
```

### 7.2 TUI

| 入口 | 实现 | 行数 |
|---|---|---|
| `/channel` slash command | `apps/cli/src/commands/handler.ts` 触发 `pickers/channel.tsx` | 374 行 picker |
| Channel 实时活动 feed | `apps/cli/src/components/ChannelActivityFeed.tsx` | 66 行 |
| WS handler 显示 channel turn | `apps/cli/src/screens/repl/wsHandlers/handleChannelTurn.ts` | — |

`/channel` 工作流：选 channel → 选 account → 引导用户用 `/attach` 把当前对话绑到 channel peer。

### 7.3 Web UI

| 入口 | 实现 |
|---|---|
| Topbar channel popover | `web/components/chat/top-bar/channel-menu.tsx` |
| Health badge status API | `/api/channels/{platform}/{account_id}/status` 返回 alive/stale/unknown |
| 本地 access 管理 | `web/components/settings/channels/access-list.tsx` 调用仅限 loopback 的 `/api/channels/access` 查询、批准和撤销路由 |

CLI 继续提供账号、binding 和 access 管理。

## 8. 扩展点

| 扩展 | 怎么做 |
|---|---|
| 新平台（WhatsApp / Signal / Matrix / LINE） | 写 `Channel` 子类 + entry_point 注册；parse 成 `ChannelMessage` 后，base 流水线（门禁、附件、引用、重试、格式化）原样生效 |
| thread 级会话隔离 | `ChannelMessage.thread_id` 已 parse，要做的是把它折进 session key |
| Reaction approval（✓/✗ 确认 dangerous tool） | adapter 侧的 reaction listener 加上接到 approval 路径的桥 |
| Token-level text streaming | 目前只在 tool 边界 edit；按 reply text delta 编辑要权衡平台 rate limit |

## 9. 参考

- [`audit.md`](./audit.md) — 要求、不变量，以及跟 OpenClaw / Hermes 的对比
- 各 adapter 顶部 docstring — platform-specific 协议细节
