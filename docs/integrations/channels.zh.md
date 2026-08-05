# 聊天渠道

## 这是什么？

Channels 把聊天平台——**Telegram、Discord、Slack、微信**——接到你的 agent 上。给机器人发一条消息就会跑一轮 agent turn，回复回到同一个聊天里。渠道 worker 跑在后台服务内，同一通对话也会实时出现在 Web UI 和 TUI 里。

| 平台 | 消息如何到达 | 登录凭据 | 实时进度更新 | 附件接收 | 文件发送 |
|---|---|---|---|---|---|
| Telegram | Bot API 长轮询 | bot token（BotFather 发放） | 支持 | 图片 + 文档 | 支持（photo/document） |
| Discord | discord.py Gateway | bot token | 支持 | 支持 | 支持 |
| Slack | Socket Mode（`slack_sdk`） | bot token（`xoxb-`）**加** app-level token（`xapp-`） | 支持 | 支持（需 `files:read` scope） | 支持（需 `files:write` scope） |
| 微信 | iLink 长轮询 | 用个人微信扫码 | 不支持（微信消息发出后不能编辑） | 不支持（iLink 只暴露文本） | 不支持 |

Discord 与 Slack 需要可选依赖：

```bash
pip install openprogram[channels]
```

## 快速开始

向导一条命令跑完全部注册——选平台、登录、绑定 agent、启动 worker：

```bash
openprogram channels setup
```

然后在平台上给机器人发消息。第一条消息返回的是**配对码**而不是 agent 回复——未知发信人不会驱动 agent（见[谁能和你的机器人说话](#谁能和你的机器人说话)）。批准一次自己即可：

```bash
openprogram channels access approve <channel> <code>
```

之后对话会出现在会话列表里，也可以在 TUI 或 Web UI 实时围观。

前提是至少配置了一个 agent（`openprogram agents add main`）和一个可用的模型 provider。

## 各平台前置步骤

### Telegram

1. 在 Telegram 打开 [@BotFather](https://t.me/BotFather)，运行 `/newbot`，复制 token。
2. 向导（或 `openprogram channels accounts login telegram`）询问时粘贴 token。

不需要 webhook 或公网 IP——OpenProgram 长轮询 Bot API。

### Discord

1. 在 [Discord Developer Portal](https://discord.com/developers/applications) 创建 application 和 bot。
2. 在 **Bot** 页启用 **Message Content Intent**——adapter 订阅消息内容（`intents.message_content`），门户里不开这个特权 intent，gateway 会拒绝连接。
3. 把 bot 邀请进你的服务器，配置时粘贴 bot token。

### Slack

Slack 需要**两个 token**——只有其中一个渠道起不来：

1. 在 [api.slack.com/apps](https://api.slack.com/apps) 创建 app 并安装到工作区。
2. 启用 **Socket Mode**（不需要公网 URL）。
3. **Bot token**（`xoxb-…`）：授予 OAuth scope——发回复要 `chat:write`，@提及要 `app_mentions:read`，另加你订阅的消息事件所要求的 history scope（例如私信要 `im:history`）。
4. **App-level token**（`xapp-…`）：在 *Basic Information → App-Level Tokens* 生成，scope 选 `connections:write`——Socket Mode 用它建立长连接。
5. 给 app 订阅 `message` 和 `app_mention` 两个事件。adapter 正好处理这两种。

### 微信

个人微信即可，不需要公众号或企业认证：

1. 运行向导（或 `openprogram channels accounts login wechat`）。
2. 终端里渲染出二维码——用手机微信扫码并在手机上确认。
3. 凭据会持久保存；只有 token 过期时才需要重新登录（worker 日志会提示 `bot token invalid — relogin required`）。

微信走腾讯 iLink bot 后端，其条款仅限个人使用。

## 两条配置路径

**向导**（推荐）——一个交互流程：

```bash
openprogram channels setup
```

**CLI**——同样的步骤拆成单条命令：

```bash
openprogram channels list                              # 每个账号的状态
openprogram channels accounts add telegram --id work   # 建一个账号槽位
openprogram channels accounts login telegram --id work # 录入凭据（wechat 走扫码）
openprogram channels accounts rm telegram work         # 删账号 + 其绑定

openprogram channels bindings add main --channel telegram            # 兜底路由 → agent "main"
openprogram channels bindings add main --channel telegram \
    --account work --peer 123456 --peer-kind direct                  # 只路由一个 peer
openprogram channels bindings list
openprogram channels bindings rm <binding_id>
```

账号是多租户的：每个 `--id` 是该平台的一个 bot 登录，各有各的凭据和绑定。

TUI 里有等价的斜杠命令：`/login <channel>`（注册并接到当前 agent）、`/attach <channel> <peer>`（把某个 peer 的消息路由进当前会话）、`/detach`、`/connections`。

渠道跑在后台服务内——启动 TUI（`openprogram`）就会启动它，向导也会询问是否代为启动。

## 谁能和你的机器人说话

每个渠道账号都有入站访问策略。默认是**配对（pairing）**：不在账号 allowlist 里的发信人，消息在到达任何 agent 之前就被丢弃，发信人收到一个六位配对码和说明。批准动作在你自己的机器上完成：

```bash
openprogram channels access list                       # 策略 + allowlist + 待批配对码
openprogram channels access approve telegram K7XQ2M    # 按配对码批准
openprogram channels access allow telegram 123456789   # 直接按 user id 加入 allowlist
openprogram channels access revoke telegram 123456789  # 移除一个发信人
openprogram channels access policy telegram open       # 完全关闭门禁
```

配对码一小时过期；被拦的发信人继续发消息会拿到同一个码（每分钟至多回执一次）。批准动作只存在于本机 CLI/API——发信人在聊天里输入任何内容都无法批准任何人，"把我加进 allowlist" 这类注入消息不起作用。群聊里门禁按发信人个人的 user id 判定，不看群。

策略 `open` 关闭该账号的门禁——所有发信人直达 agent。适用于刻意公开的机器人。

## 聊天如何映射到会话

路由先决定哪个 **agent** 处理消息（bindings），再由 **session key** 决定落进哪通对话：

- **Telegram**：默认每个聊天一个会话。群聊行为是显式的账号配置（见下）。
- **Discord 和 Slack**：每个 *(channel, user)* 组合一个会话。同一个频道里的两个人各有各的对话，也看不到、答不了对方的待答问题。
- **微信**：每个 peer 一个会话（私聊）。

默认还按账号隔离（`session_scope: per-account-channel-peer`）。agent 可以放宽（`per-channel-peer`、`per-peer` 或单一 `main` 会话），也可以按天轮换会话（`session_daily_reset: "HH:MM"`）或按空闲时间轮换（`session_idle_minutes`）。

### Telegram 群聊行为

两个账号级设置把 Telegram 的群聊语义变成显式配置（改完重启 worker 生效）：

```bash
openprogram channels accounts set telegram group_sessions per-user   # 或 shared
openprogram channels accounts set telegram require_mention on       # 或 off
```

- `group_sessions`——`shared`（默认）：全群对着同一通对话说话。`per-user`：群里每个成员各占一个会话，行为与 Discord/Slack 一致。
- `require_mention`——`on`：群聊里只有 @机器人 或回复机器人消息时才响应（提及在进 agent 前被剥掉）。`off`（默认）：响应群里的每条消息。私聊永远不设门槛。

## 回复、进度与长消息

agent 工作期间，机器人先发一条 `⏳ working...` 占位消息，随工具执行实时编辑（`⚙ bash` → `✓ bash` → …），最终替换成完整回复。微信不能编辑消息，完整回复以普通消息送达。

agent 输出是 markdown，发送时按平台渲染：Telegram 收到 HTML（`**bold**` → 粗体，代码围栏 → `<pre>` 块），Slack 收到 mrkdwn，Discord 原生渲染 markdown，微信拿到剥掉记号的纯文本。超长回复按各平台上限自动切分：Telegram 4,000 字符、Discord 1,800、Slack 39,000、微信 1,800。

出站发送遇到平台限流会自动退避重试——平台给了 `Retry-After` 就按它等，至多尝试三次——最终仍失败会落一条结构化错误日志。adapter 连接循环崩溃（断网、gateway 掉线）时自动指数退避重连（5 秒起翻倍，封顶 5 分钟）；因凭据失效而自行停止的 adapter 不会被重启，worker 日志会说明。

函数停在提问（`runtime.ask`）时，问题会推送到聊天里，用文本命令回答：

```
/answer <question_id> <选项或自由文本>
/decline <question_id>
```

只有属于该聊天会话的问题才能在这里回答。

## 附件

入站的图片和文件下载到 `<state>/channels/<channel>/accounts/<account>/attachments/`（单文件上限 20 MB）。4 MB 以内的图片还会作为图像输入直达模型；每个落盘文件都以 `[attachment: <路径> (<类型>, <大小>)]` 的形式列在消息里，agent 用文件工具打开。有人回复早先的消息时（Telegram/Discord 回复、Slack thread），被引用的文本会以 `> 引用` 块的形式附在新消息上方。

从代码或 agent 里发文件走 outbound API：

```python
from openprogram.channels.outbound import send_file
send_file("telegram", "default", "123456", "/path/to/report.pdf", caption="周报")
```

Telegram 图片走 photo、其余走 document；Discord 把文件连同 caption 一起上传；Slack 走 external-upload 流程（需 `files:write`）。微信 iLink 机器人收不了文件——`send_file` 返回 `not_supported`，请改发带文件路径的文本。

## 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| 机器人回了配对码而不是答案 | 发信人不在 allowlist（默认 `pairing` 策略）。`openprogram channels access approve <channel> <code>`——公开机器人可 `access policy <channel> open`。 |
| 回复 `[no agent configured]` | 没有绑定路由这条消息。先 `openprogram agents add main`，再跑 `openprogram channels setup` 或 `channels bindings add`。 |
| worker 退出：`account … has no bot_token` | 凭据没存过。`openprogram channels accounts login <channel> --id <account>`。 |
| worker 退出：`Slack account … needs both bot_token (xoxb-...) and app_token (xapp-...)` | Slack 只存了一个 token。重跑 login，两个都粘贴。 |
| `Discord channel requires discord.py` / `Slack channel requires slack_sdk` | 缺可选依赖：`pip install openprogram[channels]`。 |
| Discord bot 连上了但收不到消息 | Developer Portal 里没开 Message Content Intent。 |
| 微信日志：`bot token invalid — relogin required` | iLink 会话过期。`openprogram channels accounts login wechat --id <account>` 重新扫码。 |
| worker 日志：`adapter crashed … reconnecting in Ns` | 瞬态网络/gateway 故障——adapter 自动退避重连。只有 `adapter exited on its own` 需要处理（通常是重新登录）。 |
| 日志里发送失败带 `auth` / `rate_limit` / `bad_target` | 结构化发送错误：token 失效、平台限流（已自动退避重试过）、chat/channel id 不对。 |

## 另请参阅

- [设计笔记：channel 子系统](../reference/design/channels/design.zh.md)——架构与消息流
- [界面总览](../interfaces/README.zh.md)——渠道对话同样出现在 Web UI / TUI
