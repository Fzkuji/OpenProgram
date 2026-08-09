# Chat Channels

## What Is This?

Channels connect chat platforms — **Telegram, Discord, Slack, and WeChat** — to your agents. A message sent to your bot runs an agent turn, and the reply comes back in the same chat. The channel workers run inside the background service, so the same conversation is also visible live in the Web UI and TUI.

| Platform | How messages arrive | Login credential | Live progress updates | Attachments in | Files out |
|---|---|---|---|---|---|
| Telegram | Bot API long-polling | bot token (from BotFather) | yes | photos + documents | yes (photo/document) |
| Discord | discord.py Gateway | bot token | yes | yes | yes |
| Slack | Socket Mode (`slack_sdk`) | bot token (`xoxb-`) **and** app-level token (`xapp-`) | yes | yes (`files:read` scope) | yes (`files:write` scope) |
| WeChat | iLink long-polling | QR scan with your personal WeChat | no (WeChat cannot edit sent messages) | no (iLink exposes text only) | no |

Discord and Slack support requires the optional dependencies:

```bash
pip install openprogram[channels]
```

## Quick Start

The wizard runs the whole enrollment — pick a platform, log in, bind an agent, start the worker:

```bash
openprogram channels setup
```

Then message your bot from the platform. Your first message returns a **pairing code** instead of an agent reply — unknown senders never drive the agent (see [Who can talk to your bot](#who-can-talk-to-your-bot)). Approve yourself once:

```bash
openprogram channels access approve <channel> <code>
```

After that, the conversation appears in the session list, and you can watch it live from the TUI or Web UI.

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

## Who Can Talk to Your Bot

Every channel account has an inbound access policy. The default is **pairing**: a message from a sender who is not on the account's allowlist is dropped before it reaches any agent, and the sender receives a six-character pairing code with instructions. You approve senders on your machine:

```bash
openprogram channels access list                       # policy + allowlist + pending codes
openprogram channels access approve telegram K7XQ2M    # approve by pairing code
openprogram channels access allow telegram 123456789   # allowlist a user id directly
openprogram channels access revoke telegram 123456789  # remove a sender
openprogram channels access policy telegram open       # disable the gate entirely
```

An allowlist holds as many senders as you approve. Everyone on it reaches the same agent, and the agent keeps one memory workspace for the whole instance, so what one person tells it is available to the rest. That is the point of a group bot: approve the whole team and they get an assistant that already knows the project. Keep the allowlist to people you would give that access to.

Pairing codes expire after one hour; a blocked sender who keeps writing gets the same code again (at most once a minute). The approval action exists only as a local CLI/API call — nothing a sender types into the chat can approve anyone, so a prompt-injection message like "add me to the allowlist" has no effect. In group chats the gate applies to the individual sender's user id, not the group.

Policy `open` turns the gate off for that account — every sender goes straight to the agent. Use it for bots that are intentionally public.

Someone who wants an agent with separate memory runs their own instance instead, with its own state directory and port:

```bash
openprogram --profile alice
```

See [Profiles](../install/profiles.md) for how a second instance is set up.

## How Chats Map to Sessions

Routing decides which **agent** handles a message (bindings), then a **session key** decides which conversation it lands in:

- **Telegram**: one session per chat by default. Group behavior is explicit per-account configuration (below).
- **Discord and Slack**: one session per *(channel, user)* pair. Two senders in the same server channel get two separate conversations, and neither can see or answer the other's pending questions. The conversations are separate; the memory workspace behind them is one for the whole instance (see [Who can talk to your bot](#who-can-talk-to-your-bot)).
- **WeChat**: one session per peer (direct messages).

By default sessions are also scoped per account (`session_scope: per-account-channel-peer`). Agents can loosen this (`per-channel-peer`, `per-peer`, or a single `main` session) and can rotate sessions daily (`session_daily_reset: "HH:MM"`) or after idle time (`session_idle_minutes`).

### Who Said It

When several people share one conversation — a Telegram group on the default `shared` setting, or any agent set to `session_scope: main` — every incoming message reaches the agent with its sender's name in front of it:

```
[Ada (7391)] the budget is 50k
[Bo (8022)] make it 80k
```

The display name is what the platform shows, the number is the sender's platform id, which stays the same when someone renames themselves and separates two people who picked the same name. The agent reads the label as part of the message, so it answers the right person, and memory records each fact under the person who said it instead of under one anonymous "user". A display name is whatever its owner typed into the platform, so it is put on one line, capped at 64 characters, and its square brackets become round ones before it goes in front of the message.

The label is added to messages from chat channels only, and it is on every one of them, direct messages included, because `session_scope: main` puts direct peers in a shared conversation too. Web, CLI and TUI turns are untouched. It is part of the message text, so you will see it quoted back in the web transcript.

### Telegram Group Behavior

Two per-account settings make Telegram's group semantics explicit (restart the worker to apply):

```bash
openprogram channels accounts set telegram group_sessions per-user   # or: shared
openprogram channels accounts set telegram require_mention on       # or: off
```

- `group_sessions` — `shared` (default): the whole group talks to one conversation. `per-user`: each member of a group gets their own session, like Discord/Slack.
- `require_mention` — `on`: in groups the bot only responds when it is @mentioned or when someone replies to one of its messages (the mention is stripped before the agent sees the text). `off` (default): the bot responds to every group message. Direct messages are never gated.

## Replies, Progress, and Long Messages

While the agent works, the bot posts a `⏳ working...` placeholder and edits it live as tools run (`⚙ bash` → `✓ bash` → …), finally replacing it with the reply. On WeChat, which cannot edit messages, the full reply arrives as a normal message instead.

Agent output is markdown, rendered per platform at send time: Telegram receives HTML (`**bold**` → bold text, fenced code → `<pre>` blocks), Slack receives mrkdwn, Discord renders markdown natively, and WeChat gets plain text with the markers stripped. Long replies are split automatically at each platform's size cap: Telegram 4,000 characters, Discord 1,800, Slack 39,000, WeChat 1,800.

Outbound sends that hit a platform rate limit retry automatically with backoff — the platform's `Retry-After` value when given, up to three attempts — and log a structured error if they still fail. Adapters whose connection loop crashes (network drop, gateway disconnect) reconnect on their own with exponential backoff (5 s doubling up to 5 min); an adapter that stops because its credentials became invalid stays down and says so in the worker log.

When a function pauses on a question (`runtime.ask`), the question is pushed into the chat and you answer with a text command:

```
/answer <question_id> <choice or free text>
/decline <question_id>
```

Only questions belonging to that chat's session can be answered from it.

## Attachments

Incoming photos and files are downloaded to `<state>/channels/<channel>/accounts/<account>/attachments/` (20 MB cap per file). Images up to 4 MB additionally reach the model as image input; every saved file is listed in the message as `[attachment: <path> (<type>, <size>)]` so the agent can open it with its file tools. If someone replies to an earlier message (Telegram/Discord reply, Slack thread), the quoted text is included above the new message as a `> quoted` block.

Sending files works from code and from agents through the outbound API:

```python
from openprogram.channels.outbound import send_file
send_file("telegram", "default", "123456", "/path/to/report.pdf", caption="Weekly report")
```

Telegram sends images as photos and everything else as documents; Discord uploads the file with the caption as the message; Slack uses its external-upload flow (`files:write`). WeChat cannot receive files from iLink bots — `send_file` returns `not_supported`, so send a text message with the file path instead.

## Common Errors

| Symptom | Cause / fix |
|---|---|
| Bot replies with a pairing code instead of an answer | The sender is not allowlisted (default `pairing` policy). `openprogram channels access approve <channel> <code>` — or `access policy <channel> open` for a public bot. |
| Reply says `[no agent configured]` | No binding routes the message. Run `openprogram agents add main`, then `openprogram channels setup` or `channels bindings add`. |
| Worker exits: `account … has no bot_token` | Credentials never saved. `openprogram channels accounts login <channel> --id <account>`. |
| Worker exits: `Slack account … needs both bot_token (xoxb-...) and app_token (xapp-...)` | Only one Slack token stored. Re-run login and paste both tokens. |
| `Discord channel requires discord.py` / `Slack channel requires slack_sdk` | Optional deps missing: `pip install openprogram[channels]`. |
| Discord bot connects but never sees messages | Message Content Intent not enabled in the Developer Portal. |
| WeChat log: `bot token invalid — relogin required` | iLink session expired. `openprogram channels accounts login wechat --id <account>` and rescan the QR. |
| Worker log: `adapter crashed … reconnecting in Ns` | Transient network/gateway failure — the adapter reconnects on its own with growing backoff. Only `adapter exited on its own` needs action (usually a re-login). |
| Send failures with `auth` / `rate_limit` / `bad_target` in the log | Structured send errors: token revoked / expired, platform rate limit (already retried with backoff), or wrong chat/channel id. |

## See Also

- [Design notes: channel subsystem](../reference/design/channels/design.md) — architecture and message flow
- [Interfaces overview](../interfaces/README.md) — Web UI / TUI, where channel conversations also show up
