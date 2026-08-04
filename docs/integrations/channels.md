# Chat Channels

## What Is This?

Channels connect chat platforms — **Telegram, Discord, Slack, and WeChat** — to your agents. A message sent to your bot runs an agent turn, and the reply comes back in the same chat. The channel workers run inside the background service, so the same conversation is also visible live in the Web UI and TUI.

| Platform | How messages arrive | Login credential | Live progress updates |
|---|---|---|---|
| Telegram | Bot API long-polling | bot token (from BotFather) | yes |
| Discord | discord.py Gateway | bot token | yes |
| Slack | Socket Mode (`slack_sdk`) | bot token (`xoxb-`) **and** app-level token (`xapp-`) | yes |
| WeChat | iLink long-polling | QR scan with your personal WeChat | no (WeChat cannot edit sent messages) |

Discord and Slack support requires the optional dependencies:

```bash
pip install openprogram[channels]
```

## Quick Start

The wizard runs the whole enrollment — pick a platform, log in, bind an agent, start the worker:

```bash
openprogram channels setup
```

Then message your bot from the platform. The conversation appears in the session list, and you can watch it live from the TUI or Web UI.

You need at least one agent configured first (`openprogram agents add main`) plus a working model provider.

## Per-Platform Prerequisites

### Telegram

1. Open [@BotFather](https://t.me/BotFather) in Telegram, run `/newbot`, and copy the token.
2. Paste the token when the wizard (or `openprogram channels accounts login telegram`) asks for it.

No webhook or public IP is needed — OpenProgram long-polls the Bot API.

### Discord

1. Create an application and bot at the [Discord Developer Portal](https://discord.com/developers/applications).
2. On the **Bot** page, enable the **Message Content Intent** — the adapter subscribes to message content (`intents.message_content`), and the gateway rejects the connection without it.
3. Invite the bot to your server, then paste the bot token during setup.

### Slack

Slack needs **two tokens** — the channel cannot start with only one:

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) and install it to your workspace.
2. Enable **Socket Mode** (no public URL needed).
3. **Bot token** (`xoxb-…`): grant OAuth scopes — `chat:write` to send replies, `app_mentions:read` for mentions, plus the history scopes Slack requires for the message events you subscribe to (for example `im:history` for DMs).
4. **App-level token** (`xapp-…`): create it under *Basic Information → App-Level Tokens* with the `connections:write` scope — this is what Socket Mode connects with.
5. Subscribe the app to the `message` and `app_mention` events. The adapter handles exactly these two.

### WeChat

Personal WeChat, no Official Account or enterprise registration:

1. Run the wizard (or `openprogram channels accounts login wechat`).
2. A QR code renders in the terminal — scan it with your phone's WeChat and confirm on the device.
3. Credentials persist; re-login is only needed when the token expires (the worker log then says `bot token invalid — relogin required`).

WeChat goes through Tencent's iLink bot backend. Its terms are personal-use only.

## Two Ways to Configure

**Wizard** (recommended) — one interactive flow:

```bash
openprogram channels setup
```

**CLI** — the same steps as individual commands:

```bash
openprogram channels list                              # status of every account
openprogram channels accounts add telegram --id work   # create an account slot
openprogram channels accounts login telegram --id work # enter credentials (QR for wechat)
openprogram channels accounts rm telegram work         # delete account + its bindings

openprogram channels bindings add main --channel telegram            # catch-all → agent "main"
openprogram channels bindings add main --channel telegram \
    --account work --peer 123456 --peer-kind direct                  # one peer only
openprogram channels bindings list
openprogram channels bindings rm <binding_id>
```

Accounts are multi-tenant: each `--id` is one bot login of that platform, with its own credentials and bindings.

From the TUI there are equivalent slash commands: `/login <channel>` (enroll + wire to the current agent), `/attach <channel> <peer>` (route one peer's messages into the current session), `/detach`, and `/connections`.

Channels run inside the background service — starting the TUI (`openprogram`) starts it, and the wizard offers to spawn it.

## How Chats Map to Sessions

Routing decides which **agent** handles a message (bindings), then a **session key** decides which conversation it lands in:

- **Telegram**: one session per chat. A group chat is a single session shared by everyone in the group — the whole group talks to one conversation.
- **Discord and Slack**: one session per *(channel, user)* pair. Two people in the same server channel get two separate conversations, and users cannot see or answer each other's pending questions.
- **WeChat**: one session per peer (direct messages).

By default sessions are also scoped per account (`session_scope: per-account-channel-peer`). Agents can loosen this (`per-channel-peer`, `per-peer`, or a single `main` session) and can rotate sessions daily (`session_daily_reset: "HH:MM"`) or after idle time (`session_idle_minutes`).

## Replies, Progress, and Long Messages

While the agent works, the bot posts a `⏳ working...` placeholder and edits it live as tools run (`⚙ bash` → `✓ bash` → …), finally replacing it with the reply. On WeChat, which cannot edit messages, the full reply arrives as a normal message instead.

Long replies are split automatically at each platform's size cap: Telegram 4,000 characters, Discord 1,800, Slack 39,000, WeChat 1,800.

When a function pauses on a question (`runtime.ask`), the question is pushed into the chat and you answer with a text command:

```
/answer <question_id> <choice or free text>
/decline <question_id>
```

Only questions belonging to that chat's session can be answered from it.

## Common Errors

| Symptom | Cause / fix |
|---|---|
| Reply says `[no agent configured]` | No binding routes the message. Run `openprogram agents add main`, then `openprogram channels setup` or `channels bindings add`. |
| Worker exits: `account … has no bot_token` | Credentials never saved. `openprogram channels accounts login <channel> --id <account>`. |
| Worker exits: `Slack account … needs both bot_token (xoxb-...) and app_token (xapp-...)` | Only one Slack token stored. Re-run login and paste both tokens. |
| `Discord channel requires discord.py` / `Slack channel requires slack_sdk` | Optional deps missing: `pip install openprogram[channels]`. |
| Discord bot connects but never sees messages | Message Content Intent not enabled in the Developer Portal. |
| WeChat log: `bot token invalid — relogin required` | iLink session expired. `openprogram channels accounts login wechat --id <account>` and rescan the QR. |
| Send failures with `auth` / `rate_limit` / `bad_target` in the log | Structured send errors: token revoked / expired, platform rate limit (transient — send again), or wrong chat/channel id. |

## See Also

- [Design notes: channel subsystem](../reference/design/channels/design.md) — architecture and message flow
- [Interfaces overview](../interfaces/README.md) — Web UI / TUI, where channel conversations also show up
