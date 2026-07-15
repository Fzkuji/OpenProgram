# Channel Subsystem Design

External chat platforms (Telegram / Discord / Slack / WeChat) communicate bidirectionally with OpenProgram through this subsystem: a user sends a message on a platform to trigger the agent, and the agent's reply is sent back through the same channel.

This document describes **the current shape after implementation is complete**. For the design evolution history and the list of fixed defects, see [`channel-audit.md`](./channel-audit.md).

## 1. Overall Shape

```
┌─────────────────────┐       ┌──────────────────────┐
│  External user/Telegram │   │  Your own Python     │
│  Discord/Slack/WX   │       │  script/cron/jupyter │
└──────────┬──────────┘       └──────────┬───────────┘
           │ user message comes in         │ want to send someone a message
           ▼                              ▼
  ┌──────────────────┐            ┌────────────────────┐
  │ telegram.py etc. │            │   outbound.py      │  ← entry A
  │ 4 adapters       │            │   send(...)        │     one-shot send, no long-running process needed
  │ - long poll/event loop│       └─────────┬──────────┘
  │ - parse into unified  │                 │
  │   ChannelMessage │                      │
  └────────┬─────────┘                      │
           │                                │
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
  │                                                 │
  │   returns SendResult {                          │
  │     ok, message_id, error_kind, retryable       │
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
   → openprogram/channels/telegram.py is long-polling, receives the update dict

2. Inside _handle_update(update):
   a. extract text = "help me check what Python files are in the current directory"
   b. construct ChannelMessage {
        text=..., chat_id="123", user_id="456",
        user_display="zhangsan", chat_type="direct",
        ts=1716000000, reply_to_id="", thread_id="",
      }
   c. call dispatch_inbound(channel="telegram", account_id="default",
                          peer_kind="direct", peer_id="123",
                          user_text=text, user_display="zhangsan",
                          progress_stream=True)

3. Inside dispatch_inbound (in _conversation.py):
   a. look up bindings → decide to use the "main" agent
   b. compute session_key = "default_direct_123" (in _session_routing.py)
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

7. telegram.py gets None → does not send any reply (because it was already edited in)
   In Telegram the user sees that "⏳..." placeholder has grown into the full reply
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

## 4. Five Core Design Principles

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

`_conversation.py` itself is only 283 lines (previously a single file of 588 lines with 5 responsibilities).

### 4.3 Platform Differences Sealed in the Low Level

`_transport.py` is the **only** place that calls HTTP to send outward. Telegram's `editMessageText`, Discord's `PATCH /messages/{id}`, Slack's `chat.update`, WeChat's iLink protocol — they all live here.

The adapter classes (`telegram.py` etc.) are responsible only for: (a) the event loop that connects to the server, and (b) parsing platform-native objects into `ChannelMessage`. **They are not responsible for sending messages** — sending is done by dispatch_inbound through `_transport`.

### 4.4 Structured Error Signals

`_transport.post_message` returns `SendResult`:

```python
@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error_kind: str = ""          # auth / rate_limit / bad_target / network / not_supported / unknown
    error_detail: str = ""        # human-readable one line
    retryable: bool = False       # transient retryable vs permanent failure

    def __bool__(self): return self.ok
```

The caller can distinguish between "token expired, please log in again" vs "wrong chat_id" vs "retry later".

`outbound.send` keeps its bool signature (for compatibility with old callers), and `outbound.send_full()` exposes the full SendResult. `Channel.send_text` / `edit_text` work the same way, each with a `_full` variant.

### 4.5 Plugin Extension Point

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
openprogram/channels/   14 files
├── base.py              Channel ABC + MessageHandle + send_text/edit_text(_full)
├── _transport.py        SendResult + 4 platforms' HTTP post/patch (unified low level)
├── _message.py          ChannelMessage inbound neutral-structure dataclass
├── outbound.py          entry A: send / send_full (thin wrapper)
├── _conversation.py     dispatch_inbound main flow + progress streaming
├── _session_store.py    session path / create / load / save
├── _session_routing.py  session_key + reset policy
├── _broadcast.py        webui WS push (channel_turn / session_updated)
├── __init__.py          CHANNEL_CLASSES proxy + register_channel + entry_points
├── telegram.py          Telegram bot long-poll inbound
├── discord.py           Discord bot Gateway inbound
├── slack.py             Slack Socket Mode inbound
├── wechat.py            WeChat iLink long-poll inbound (incl. QR login)
├── accounts.py          credential storage
└── bindings.py          (channel, account, peer) → agent routing table
```

How to read it: each module deals only with the callers it declares; there are no circular dependencies.

| Module | Responsibility | Typical caller |
|---|---|---|
| `_transport.py` | the only place that sends bytes outward, 4 platforms' HTTP | outbound + base.send_text |
| `_message.py` | ChannelMessage parse neutral structure | adapter entry |
| `base.py` | Channel ABC + MessageHandle | adapter subclasses, dispatch_inbound |
| `outbound.py` | entry A (one-shot send) | cron script, jupyter, @agentic_function |
| `_conversation.py` | dispatch_inbound main flow | the 4 adapters' on_message |
| `_session_store.py` | session load/save | dispatch_inbound |
| `_session_routing.py` | session_key computation | dispatch_inbound |
| `_broadcast.py` | webui WS push | dispatch_inbound |
| `telegram.py` etc. | inbound event loop + parse | instantiated at worker startup |
| `__init__.py` | CHANNEL_CLASSES + plugin registration | webui list_status / worker |
| `accounts.py` | credential storage | all _transport functions |
| `bindings.py` | inbound routing | dispatch_inbound |

## 6. Supported Platforms

| Platform | Inbound mechanism | Outbound mechanism | progress streaming | Notes |
|---|---|---|---|---|
| **Telegram** | long-poll `getUpdates` (no webhook dependency) | bot API `sendMessage` / `editMessageText` | ✓ | bot token, public Bot API |
| **Discord** | discord.py Gateway WS | REST `POST /messages` / `PATCH /messages/{id}` | ✓ | bot token, intents.message_content |
| **Slack** | Socket Mode (slack_sdk) | `chat.postMessage` / `chat.update` | ✓ | bot_token (xoxb-) + app_token (xapp-) |
| **WeChat** | iLink `getupdates` long-poll | iLink `sendmessage` | ✗ (iLink does not support edit) | personal WeChat QR login, no enterprise-verification barrier |

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
  └── rm <channel> <account_id>                    delete account + associated bindings

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

| Entry point | Implementation | Status |
|---|---|---|
| Topbar channel popover | `web/components/chat/top-bar/channel-menu.tsx` (168 lines) | ✓ complete |
| Health badge status API | `/api/channels/{platform}/{account_id}/status` returns alive/stale/unknown | ✓ complete |
| Standalone settings page | — | **⚠ missing** |

The Web side currently **has no `/settings/channels` config page**. All account / bindings management can only go through the CLI. If a Web config UI is to be built later, the corresponding API should be added in `openprogram/webui/routes/channels.py`.

## 8. Plugin / Extension Future Work

| Current status | If extension is needed later |
|---|---|
| 4 built-in platforms | add WhatsApp / Signal / Matrix / LINE etc. — write a `Channel` subclass + entry_point registration |
| ChannelMessage already contains `reply_to_id` / `thread_id` / `attachments` fields | dispatch_inbound does not consume them yet; wire them up once a real need appears (reply quote / thread isolation / attachment reading) |
| Reaction approval (✓/✗ to confirm a dangerous tool) | not implemented; both hermes/OpenClaw have it; build it once a user asks |
| Token-level text streaming | currently only edits at tool boundaries; no real-time edit of reply text deltas (rate limit risk) |

## 9. References

- [`channel-audit.md`](./channel-audit.md) — design evolution history + fixed-defect list + comparison with OpenClaw / Hermes
- the docstring at the top of each adapter — platform-specific protocol details
