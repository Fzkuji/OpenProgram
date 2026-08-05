# Channel Subsystem Design

External chat platforms (Telegram / Discord / Slack / WeChat) communicate bidirectionally with OpenProgram through this subsystem: a user sends a message on a platform to trigger the agent, and the agent's reply is sent back through the same channel.

This document describes the structure and message flow. For the requirements and invariants the layer holds to, and the comparison with OpenClaw and hermes, see [`audit.md`](./audit.md).

## 1. Overall Shape

```
┌─────────────────────┐       ┌──────────────────────┐
│  External user/Telegram │   │  Your own Python     │
│  Discord/Slack/WX   │       │  script/cron/jupyter │
└──────────┬──────────┘       └──────────┬───────────┘
           │ user message comes in         │ want to send someone a message
           ▼                              ▼
  ┌──────────────────────┐        ┌────────────────────┐
  │ implementations/*.py │        │   outbound.py      │  ← entry A
  │ 4 adapters           │        │   send(...)        │     one-shot send, no long-running process needed
  │ - long poll/event loop│       │   send_file(...)   │
  │ - parse into unified  │       └─────────┬──────────┘
  │   ChannelMessage      │                 │
  │ (worker runs them via │                 │
  │  base.run_forever —   │                 │
  │  crash → backoff      │                 │
  │  reconnect)           │                 │
  └────────┬─────────────┘                  │
           │ base.Channel.handle_inbound    │
           │ (per-message thread →          │
           │  _access gate → attachment     │
           │  download → quoted block →     │
           │  dispatch; degraded-path       │
           │  resend)                       │
           ▼                                │
  ┌───────────────────────────┐             │
  │   dispatch_inbound        │             │
  │   (traffic hub, ties everything together) │
  │                           │             │
  │   ① route: decide which agent │         │
  │   ② compute session_key   │             │
  │   ③ load session state    │             │
  │   ④ call agent to run this turn │        │
  │   ⑤ progress streaming    │             │
  │   ⑥ push to webui WS      │             │
  └────────┬──────────────────┘             │
           │                                │
           │ edit placeholder / final reply as it runs │
           ▼                                ▼
  ┌─────────────────────────────────────────────────┐
  │           _transport.py (unified low level)     │  ← the only place that sends bytes outward
  │                                                 │
  │   post_message(platform, account, recipient, text) │
  │   patch_message(platform, account, recipient, msg_id, text) │
  │   post_file(platform, account, recipient, path, caption) │
  │                                                 │
  │   pipeline per chunk: MAX_CHARS chunk →         │
  │   _format.render (platform wire format) →       │
  │   HARD_CAPS re-split → rate-limit retry         │
  │   (Retry-After first, 3 attempts)               │
  │                                                 │
  │   returns SendResult {                          │
  │     ok, message_id, error_kind, retryable,      │
  │     retry_after                                 │
  │   }                                             │
  └────────┬────────────────────────────────────────┘
           │ HTTPS POST/PATCH
           ▼
  Telegram API / Discord API / Slack API / WeChat iLink API
```

## 2. End-to-End Use Case: User Message Comes In → Bot Replies

**Example**: On Telegram you send the bot "help me check what Python files are in the current directory".

```
1. The Telegram server pushes the message to the bot
   → openprogram/channels/implementations/telegram.py is long-polling,
     receives the update dict

2. Inside _handle_update(update):
   a. extract text = "help me check what Python files are in the current directory"
      (plus attachments → Attachment entries, reply_to_message text →
      quoted_text; in groups, the require_mention setting can gate and
      strip an @bot mention here)
   b. construct ChannelMessage {
        text=..., chat_id="123", user_id="456",
        user_display="zhangsan", chat_type="direct",
        ts=1716000000, reply_to_id="", quoted_text="", thread_id="",
        attachments=(),
      }
   c. hand it to base.Channel.handle_inbound(ch_msg) — the base class
      spawns a per-message daemon thread (a turn paused on runtime.ask
      must not block the poll loop) and in it:
        c1. _access.check_inbound(platform, account, user_id) — the
            allowlist/pairing gate. Unknown sender → the message is
            dropped and a pairing code goes back; approval happens only
            via the local CLI (`channels access approve`), never from
            channel text (injection boundary).
        c2. _attachments.download_inbound(...) — files land under the
            account state dir; small images become TurnRequest image
            blocks, every file appends an [attachment: path] note to
            user_text.
        c3. quoted_text (if any) is prepended as a "> quoted" block.
        c4. dispatch_inbound(channel="telegram", account_id="default",
                             peer_kind="direct", peer_id="123",
                             user_text=text, user_display="zhangsan",
                             progress_stream=True, attachments=[...])
      (peer_id comes from Channel.peer_id_for: chat_id on telegram —
       "{chat_id}_{user_id}" in groups when the account setting
       group_sessions=per-user — and wechat; "{chat_id}_{user_id}" on
       discord/slack)

3. Inside dispatch_inbound (in _conversation.py):
   a. look up bindings → decide to use the "main" agent
   b. compute session_key = "default_direct_123" (in _session_routing.py)
   b2. acquire the per-session lock — concurrent turns for the SAME
       session queue up (the dispatcher has no lock of its own and
       interleaved turns would corrupt the history); different sessions
       run in parallel. /answer · /decline commands are handled before
       the lock, so answering a question never deadlocks against the
       turn that is waiting on it.
   c. load / create session (in _session_store.py, calling SessionDB)
   d. send placeholder message: _transport.post_message("telegram", "default", "123",
                                          "⏳ working...")
      returns SendResult{ok=True, message_id="9001"}
      → MessageHandle{platform="telegram", account="default",
                      target="123", message_id="9001"}
   e. call process_user_turn(req, on_event=_on_event) to run the agent

4. The agent decides internally to call the bash tool to run `ls *.py`:
   a. dispatcher emits a tool_use envelope → _on_event receives it
   b. _on_event sees tool_use → progress_lines = ["⚙ bash"]
   c. throttle satisfied (>1s since last edit) → _transport.patch_message(
        "telegram", "default", "123", "9001", "⚙ bash")
      → on Telegram that "⏳ working..." becomes "⚙ bash"

5. bash finishes and returns "a.py b.py c.py":
   a. dispatcher emits a tool_result envelope → _on_event receives it
   b. progress_lines = ["✓ bash"]  (swap ⚙ for ✓)
   c. throttle satisfied → patch_message edits to "✓ bash"

6. The agent combines the bash output and writes the final reply "Found 3 Python files: a.py / b.py / c.py":
   a. process_user_turn returns, result.final_text = this text
   b. dispatch_inbound forces an edit (bypassing the throttle): _transport.patch_message
      changes "9001" to the full reply
   c. persist to SessionDB, broadcast to webui
   d. dispatch_inbound returns None

7. handle_inbound gets None → does not send any reply (because it was
   already edited in). In Telegram the user sees that "⏳..." placeholder
   has grown into the full reply.
   On any degraded path (placeholder failed to send / platform cannot
   edit / WeChat) dispatch_inbound returns the reply string instead, and
   handle_inbound sends it via Channel.send_text → _transport (chunked
   at the per-platform MAX_CHARS cap). Adapters hold no SDK send path
   of their own.
```

## 3. Use Case B: cron / @agentic_function Proactively Sends a Message

```python
from openprogram.channels.outbound import send

# In any Python script, no worker needs to be running
send("telegram", "default", "1234", "早上好")
```

What happens:

```
1. outbound.send calls _transport.post_message
2. _transport.post_message fetches credentials → HTTPS POST sendMessage
3. SendResult returns → outbound.send returns True/False
4. the script continues
```

**There is no**: adapter instance, worker process, session, agent call, or webui broadcast. A single call fires and forgets.

This is why outbound.send is a separate entry point instead of going through an adapter — a cron script simply has no adapter instance running.

## 4. Six Core Design Principles

### 4.1 Two Entry Points, One Implementation

| Entry point | Purpose | State | Caller |
|---|---|---|---|
| `outbound.send` | one-shot send, no long-running process needed | stateless | cron script / jupyter / @agentic_function / webui (reply) |
| `Channel.send_text` + `edit_text` | holds message_id for subsequent edits | stateful | dispatch_inbound progress streaming |

Both call the same `_transport.post_message` / `patch_message` underneath. There is only one copy of the HTTP call / credential loading / chunking code.

Why not merge the entry points: a cron script / jupyter ad-hoc call has no worker process running and needs a stateless raw HTTP interface; progress streaming needs to hold a message_id in order to edit and so needs a stateful interface. The two kinds of needs differ, but the low level is shared.

### 4.2 dispatch_inbound Is the Traffic Hub

Every message coming in from outside goes through it. It does no concrete work itself; it only ties the flow together:

```python
def dispatch_inbound(*, channel, account_id, peer_kind, peer_id,
                    user_text, user_display="", progress_stream=False) -> Optional[str]:
    # delegate to independent modules
    agent_id = bindings.route(...) or session_aliases.lookup(...)
    session_key = _session_routing.session_key_for_agent(...) + apply_reset_policy(...)

    # everything below runs under the per-session lock: same-session
    # turns serialize, different sessions run in parallel
    meta, _ = _session_store.load_or_init_session(...)

    # optional: send placeholder + subscribe to stream → progress edit
    if progress_stream:
        placeholder_handle = _transport.post_message(... "⏳ working...")

    # run the agent
    result = process_user_turn(req, on_event=...)

    # persist + broadcast
    _broadcast.broadcast_channel_turn(...)

    return result.final_text  # or None (progress mode)
```

`_conversation.py` holds only this flow; routing, session storage, and broadcast each live in their own module.

### 4.3 Platform Differences Sealed in the Low Level

`_transport.py` is the **only** place that calls HTTP to send outward. Telegram's `editMessageText`, Discord's `PATCH /messages/{id}`, Slack's `chat.update`, WeChat's iLink protocol, and the per-platform file uploads (`post_file`: Telegram sendPhoto/sendDocument, Discord multipart, Slack external-upload; WeChat absent → `not_supported`) — they all live here. The per-platform message-size caps (`MAX_CHARS`), chunking, markdown rendering (via `_format`), rendered hard-cap re-split (`HARD_CAPS`), and rate-limit retry are single-sourced in the same pipeline.

`_format.py` is the outbound formatting single point: agent markdown → Telegram HTML (everything escaped, so any input is valid; the Telegram poster falls back to tag-stripped plain text if the API still rejects the entities), Slack mrkdwn, Discord passthrough, WeChat plain text. Code fences and inline code are placeholder-protected so inline transforms never touch code bodies.

The adapter classes (`implementations/telegram.py` etc.) are responsible only for: (a) the event loop that connects to the server, and (b) parsing platform-native objects into `ChannelMessage` (text, attachments metadata, quoted text). Everything after the parse — per-message thread dispatch, the `_access` gate, attachment download, the quoted block, calling `dispatch_inbound`, and re-sending the reply on the degraded (non-streaming) path — lives once in `base.Channel.handle_inbound`. **Adapters do not send messages** — outbound traffic goes through `_transport` via `send_text` / `send_file`.

The worker runs each adapter through `base.Channel.run_forever(stop)`: a `run()` that raises is reconnected with exponential backoff (5 s doubling, capped at 300 s, reset after a run that survived 60 s); a `run()` that returns cleanly means "permanently stopped, operator action needed" (e.g. WeChat token invalid) and is not restarted.

### 4.4 Structured Error Signals

`_transport.post_message` returns `SendResult`:

```python
@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error_kind: str = ""          # auth / rate_limit / bad_target / network / not_supported / format / unknown
    error_detail: str = ""        # human-readable one line
    retryable: bool = False       # transient retryable vs permanent failure
    retry_after: float = 0.0      # platform-stated wait (Retry-After header / body), 0 = unstated

    def __bool__(self): return self.ok
```

The caller can distinguish between "token expired, please log in again" vs "wrong chat_id" vs "retry later". `rate_limit` failures are retried inside `_transport` itself (`Retry-After` first, fallback 1 s / 3 s, sleep capped at 30 s, 3 attempts total); what escapes to the caller is already the post-retry verdict, logged with its error kind.

`outbound.send` keeps its bool signature (for compatibility with old callers), and `outbound.send_full()` exposes the full SendResult. `Channel.send_text` / `edit_text` work the same way, each with a `_full` variant; `Channel.send_file` / `outbound.send_file` return the SendResult directly.

### 4.5 Inbound Access Control

`_access.py` holds one `access.json` per (channel, account): `policy` (`pairing` default / `open`), an `allowlist` keyed by platform user id, and `pending` pairing codes (1 h TTL). `base._dispatch_and_reply` calls `check_inbound` before routing; a sender not on the allowlist never reaches `dispatch_inbound` — they receive a 6-character pairing code (re-notified at most once per minute). The mutating operations (`approve` / `approve_user` / `revoke` / `set_policy`) are exposed only through the local CLI (`openprogram channels access …`); the inbound path can only read the allowlist and mint pending codes, so no channel message can approve anyone.

### 4.6 Plugin Extension Point

Adding a new platform (say WhatsApp) requires no source changes:

**Option A** — `pyproject.toml` entry_point (recommended):

```toml
[project.entry-points."openprogram.channels"]
whatsapp = "my_pkg.whatsapp:WhatsAppChannel"
```

At startup `importlib.metadata.entry_points(group="openprogram.channels")` scans automatically.

**Option B** — `register_channel` imperative call:

```python
from openprogram.channels import register_channel
from my_pkg.whatsapp import WhatsAppChannel

register_channel("whatsapp", WhatsAppChannel)
```

Suitable for a temporary mount in jupyter or dynamic registration in plugin hooks.

The 4 built-in platforms take priority; a same-named plugin is silently ignored.

## 5. Module Inventory

```
openprogram/channels/
├── base.py              Channel ABC + MessageHandle + handle_inbound
│                        (per-message thread → access gate → attachments
│                        → quoted block → dispatch; degraded-path resend)
│                        + run_forever (crash backoff-reconnect)
│                        + send_text/edit_text(_full) + send_file
├── _transport.py        SendResult + MAX_CHARS/HARD_CAPS + chunk →
│                        render → retry pipeline + 4 platforms' HTTP
│                        post/patch/file-upload (unified low level)
├── _format.py           outbound markdown → telegram HTML / slack
│                        mrkdwn / discord passthrough / wechat plain
├── _access.py           inbound allowlist + pairing gate (access.json;
│                        approval is local-CLI-only)
├── _attachments.py      inbound attachment download + turn-input
│                        conversion (image blocks + path notes)
├── _message.py          ChannelMessage + Attachment neutral dataclasses
├── outbound.py          entry A: send / send_full / send_file (thin wrapper)
├── _conversation.py     dispatch_inbound main flow + per-session lock
│                        + progress streaming
├── _session_store.py    session path / create / load / save
├── _session_routing.py  session_key + reset policy
├── _broadcast.py        WS push via event bus (emit_ws_frame → ws.frame;
│                        webui subscribes and fans out — channels never
│                        import webui)
├── _question_bridge.py  push a pending runtime.ask question into the chat
├── _question_commands.py /answer · /decline text-command handling
├── _heartbeats.py       adapter heartbeat registry
├── __init__.py          CHANNEL_CLASSES proxy + register_channel + entry_points
├── implementations/
│   ├── telegram.py      Telegram bot long-poll inbound (group_sessions /
│   │                    require_mention account settings)
│   ├── discord.py       Discord bot Gateway inbound
│   ├── slack.py         Slack Socket Mode inbound
│   └── wechat.py        WeChat iLink long-poll inbound (incl. QR login)
├── setup.py             `openprogram channels setup` wizard
├── worker.py            backward-compatible worker import shim
├── accounts.py          credential storage + account behavior settings
└── bindings.py          (channel, account, peer) → agent routing table
```

How to read it: each module deals only with the callers it declares; there are no circular dependencies.

| Module | Responsibility | Typical caller |
|---|---|---|
| `_transport.py` | the only place that sends bytes outward: chunk → render → hard-cap split → rate-limit retry, 4 platforms' HTTP (text/edit/file) | outbound + base.send_text/send_file |
| `_format.py` | markdown → per-platform wire format | _transport |
| `_access.py` | allowlist + pairing gate (`access.json`) | base.handle_inbound (check), CLI `channels access` (mutation) |
| `_attachments.py` | inbound attachment download + turn-input conversion | base.handle_inbound |
| `_message.py` | ChannelMessage + Attachment neutral structures | adapter entry |
| `base.py` | Channel ABC + MessageHandle + handle_inbound + run_forever | adapter subclasses, worker, dispatch_inbound |
| `outbound.py` | entry A (one-shot send / send_file) | cron script, jupyter, @agentic_function |
| `_conversation.py` | dispatch_inbound main flow + per-session lock | base.handle_inbound |
| `_session_store.py` | session load/save | dispatch_inbound |
| `_session_routing.py` | session_key computation | dispatch_inbound |
| `_broadcast.py` | WS frames onto the event bus (`ws.frame`) | dispatch_inbound |
| `implementations/*.py` | inbound event loop + parse (incl. attachments/quotes, telegram group settings) | instantiated at worker startup via run_forever |
| `__init__.py` | CHANNEL_CLASSES + plugin registration | webui list_status / worker |
| `accounts.py` | credential storage + behavior settings (ACCOUNT_SETTINGS) | all _transport functions, adapters |
| `bindings.py` | inbound routing | dispatch_inbound |

## 6. Supported Platforms

| Platform | Inbound mechanism | Outbound mechanism | progress streaming | attachments in | files out | Notes |
|---|---|---|---|---|---|---|
| **Telegram** | long-poll `getUpdates` (no webhook dependency) | bot API `sendMessage` (HTML) / `editMessageText` / `sendPhoto`+`sendDocument` | ✓ | photo + document (file_id → getFile) | ✓ | bot token, public Bot API; group_sessions / require_mention settings |
| **Discord** | discord.py Gateway WS | REST `POST /messages` / `PATCH /messages/{id}` / multipart upload | ✓ | ✓ (CDN URLs) | ✓ | bot token, intents.message_content |
| **Slack** | Socket Mode (slack_sdk) | `chat.postMessage` (mrkdwn) / `chat.update` / external-upload | ✓ | ✓ (`url_private` + bearer) | ✓ | bot_token (xoxb-) + app_token (xapp-) |
| **WeChat** | iLink `getupdates` long-poll | iLink `sendmessage` (plain text) | ✗ (iLink does not support edit) | ✗ (text-only protocol) | ✗ (`not_supported`) | personal WeChat QR login, no enterprise-verification barrier |

Platform differences are governed by this table and the docstring at the top of each adapter.

## 7. User Entry Points

### 7.1 CLI

The full command tree (`openprogram channels`):

```
openprogram channels list                          show status of each platform/account
openprogram channels setup                         interactive setup wizard

openprogram channels accounts
  ├── list                                         list all accounts
  ├── add <channel> --id <name>                    create a new account slot
  ├── login <channel> --id <name>                  interactively enter credentials
  │     - telegram/discord/slack: getpass paste token
  │     - wechat: start iLink QR login flow
  ├── set <channel> <key> <value> --id <name>      behavior settings
  │     (telegram: group_sessions=shared|per-user, require_mention=on|off)
  └── rm <channel> <account_id>                    delete account + associated bindings

openprogram channels access
  ├── list [<channel>]                             policy + allowlist + pending codes
  ├── approve <channel> <code> --id <name>         approve by pairing code
  ├── allow <channel> <user_id> --id <name>        allowlist a user id directly
  ├── revoke <channel> <user_id> --id <name>       remove a sender
  └── policy <channel> pairing|open --id <name>    set the inbound policy

openprogram channels bindings
  ├── list                                         list all routing rules
  ├── add <agent_id> --channel <ch> [--account <acct>] [--peer <peer> --peer-kind <kind>]
  │                                                  route (channel, account, peer) to an agent
  └── rm <binding_id>                              delete one route
```

### 7.2 TUI

| Entry point | Implementation | Lines |
|---|---|---|
| `/channel` slash command | `cli/src/commands/handler.ts` triggers `pickers/channel.tsx` | 374-line picker |
| Channel real-time activity feed | `cli/src/components/ChannelActivityFeed.tsx` | 66 lines |
| WS handler that displays a channel turn | `cli/src/screens/repl/wsHandlers/handleChannelTurn.ts` | — |

`/channel` workflow: pick a channel → pick an account → guide the user to use `/attach` to bind the current conversation to a channel peer.

### 7.3 Web UI

| Entry point | Implementation |
|---|---|
| Topbar channel popover | `web/components/chat/top-bar/channel-menu.tsx` |
| Health badge status API | `/api/channels/{platform}/{account_id}/status` returns alive/stale/unknown |

Account and bindings management goes through the CLI. A Web config UI would add
its API in `openprogram/webui/routes/channels.py`.

## 8. Extension Points

| Extension | How it is done |
|---|---|
| A new platform (WhatsApp / Signal / Matrix / LINE) | write a `Channel` subclass + entry_point registration; parse into `ChannelMessage` and the base pipeline (access gate, attachments, quotes, retry, formatting) applies unchanged |
| Thread-scoped session isolation | `ChannelMessage.thread_id` is parsed; the work is folding it into the session key |
| Reaction approval (✓/✗ confirming a dangerous tool) | an adapter-side reaction listener plus a bridge to the approval path |
| Token-level text streaming | edits currently happen at tool boundaries; editing on reply-text deltas has to weigh the platform rate limit |
| webui access management | `_access` describe/approve/revoke are plain functions; a webui route can call them the same way the CLI does |

## 9. References

- [`audit.md`](./audit.md) — requirements, invariants, and the comparison with OpenClaw / Hermes
- the docstring at the top of each adapter — platform-specific protocol details
