# Channel Subsystem Design Audit

Records the current state of the OpenProgram channel subsystem, the design gaps versus hermes (the primary benchmark), and the present structural defects. **Describes only facts and judgments, no implementation plans**—plans will be settled in later discussions.

## 1. Our current design

### 1.1 File layout

```
openprogram/channels/        2500 lines / 9 py files
├── base.py            21 lines  Channel ABC, only one abstract method run(stop)
├── _conversation.py  483 lines  dispatch_inbound + session routing + persistence + webui broadcast
├── outbound.py       196 lines  cross-process send(channel, account, user, text) API
├── _heartbeats.py     44 lines
├── accounts.py       278 lines  per-platform credential storage
├── bindings.py       274 lines  (channel, account, peer) → agent_id routing
├── setup.py          289 lines  setup wizard
├── worker.py          26 lines  shim → openprogram.worker
├── discord.py        111 lines  DiscordChannel adapter
├── slack.py          119 lines  SlackChannel adapter
├── telegram.py       111 lines  TelegramChannel adapter
└── wechat.py         454 lines  WechatChannel adapter (with QR login / cursor persistence)
```

### 1.2 Abstraction layer (base.py)

```python
class Channel(abc.ABC):
    platform_id: str = ""

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None: ...
```

**That's all of it**. Channel only mandates "must be able to run until stop is set"—how to read messages, how to send messages, how to handle errors, whether edit / react is possible, base does not care at all. All platform-specific behavior is pushed down to each adapter to do as it pleases.

### 1.3 Inbound message handling (inside the adapter)

Each adapter runs the platform's native SDK event loop inside its own `run()`, gets a message → extracts `(chat_id, user, text)` → calls `dispatch_inbound(...)` → gets back a complete reply string → uses the SDK to send the reply back.

| Platform | Inbound read | Outbound send-back |
|---|---|---|
| Discord | `discord.py` SDK `on_message` | `msg.channel.send(chunk)` |
| Slack | `slack_sdk` Socket Mode | `web.chat_postMessage()` |
| Telegram | raw HTTP `getUpdates` long polling | calls `outbound.send()` |
| WeChat | iLink HTTP `getupdates` long polling | calls internal `_send` |

Note that Telegram and WeChat outbound do not go through their own adapter code, but instead indirectly call `outbound.send()`—this in itself is already an inconsistency.

### 1.4 dispatch_inbound (_conversation.py)

Signature: `dispatch_inbound(channel, account_id, peer_kind, peer_id, user_text, user_display) -> str`

A one-shot blocking call that returns the complete reply. Flow:

1. Look up `session_aliases` / `bindings` → decide agent_id
2. Compute `session_key` per `agent.session_scope` (per-account-channel-peer / per-peer / main / etc.)
3. Apply `daily_reset` / `idle_minutes` reset policy
4. `_load_or_init_session` writes SessionDB
5. Build `TurnRequest` and call `process_user_turn` → complete turn
6. Append the reply to SessionDB
7. Broadcast `channel_turn` envelope to webui

Inside there is an `_on_event(env)` callback that already subscribes to the dispatcher's stream envelopes:

```python
def _on_event(env: dict) -> None:
    srv._broadcast(json.dumps(env, default=str))   # for webui to see
    if env.get("type") == "chat_ack":              # capture user_msg_id
        captured_user_id.append(...)
```

But this callback only broadcasts to webui. The channel itself cannot get the streaming events.

### 1.5 outbound.py cross-process send

```python
outbound.send(channel, account_id, user_id, text) -> bool
```

Does not go through an adapter instance. `_SENDERS` is a 4-entry dict, each entry independently using raw HTTP (the requests library) to call the platform API:

- `_send_telegram` → `POST /bot{token}/sendMessage`
- `_send_discord`  → `POST /api/v10/channels/{ch}/messages`
- `_send_slack`    → `POST /api/chat.postMessage`
- `_send_wechat`   → `POST /ilink/bot/sendmessage`

Each sender loads credentials itself, assembles headers itself, and handles chunking itself.

### 1.6 Message chunking

`MAX_MSG_CHARS` is defined independently in **5 files**:

```
discord.py    1800
slack.py      3900
telegram.py   4000
wechat.py     1800
outbound.py   1800   (duplicates discord/wechat, inconsistent with slack/telegram)
```

The same `_chunk(text, limit)` implementation is copied **5 times**.

### 1.7 Neutral message structure

**None**. Each adapter directly handles the platform-native object:

- Discord: `discord.Message` object → extract `msg.content / msg.author.id / msg.channel.id`
- Slack: events_api dict → extract `event["text"] / event["user"] / event["channel"]`
- Telegram: update dict → extract `msg["text"] / msg["chat"]["id"]`
- WeChat: iLink msg dict → custom field paths

There is no `ChannelMessage` / `MessageEvent` abstraction at all. There is no shared schema for supporting reply / quote / attachment.

### 1.8 Reverse dependency

In `outbound._send_wechat`:

```python
from openprogram.channels.wechat import _make_wechat_uin
```

outbound (which should be the lower layer) reverse-imports a private function of the wechat adapter (which should be a leaf).

---

## 2. Other projects' designs

There are only two comparables: **OpenClaw** (the source we forked from, written in TS) and **hermes** (a dedicated chat-bot project, Python). Neither opencode nor claude-code has a channel subsystem—their surfaces are CLI/TUI/Web/IDE, interfacing with a human user sitting at the front end, not plugging into Discord/Slack groups.

### 2.1 OpenClaw (fork source)

Source: `references/openclaw/src/channels/` + `references/openclaw/extensions/{discord,slack,telegram}/`. TS/Node.js, enterprise-grade modular design.

**Layout**: the core `src/channels/` has a pile of fine-grained files (routing / account / approval / typing / draft-stream / health-check / thread-bindings-policy …), and each platform lives in its own directory under `extensions/{name}/`—discord alone has 70+ files, slack 40+, telegram 35+.

**Plugin SDK** (`src/plugin-sdk/channel-*.ts`, 50+ contract files) fully isolates the core from the platform implementations. Core only sees abstract interfaces:

```typescript
ChannelMessageSendAdapter        // send capability
ChannelMessageLiveAdapterShape   // live message editing (draft → live-preview → final)
ChannelApprovalAdapter           // reaction ✓/✗ confirmation + timeout/retry
ChannelMessageActionAdapter      // button/menu action handler
ChannelOutboundAdapter           // cross-process send also goes through the adapter, not bypassed
```

**Streaming edit** (`src/plugin-sdk/channel-streaming.ts` + `extensions/discord/src/draft-stream.*`): the message lifecycle has three states:

```
draft → live-preview (throttled edit) → final
```

Emit a draft → continuously edit the message while the tool runs → finalize at the end. The throttling policy is built into the pipeline.

**Reaction approval** (`src/channels/ack-reactions.ts` + `extensions/discord/src/approval-native.ts`):

```typescript
type ChannelApprovalAdapter {
    onApprove, onDecline, onTimeout
}
```

When a dangerous tool fires, the bot adds a ✓/✗ emoji reaction → the user clicks the reaction → the adapter notifies the dispatcher. Full lifecycle (timeout / retry / cancel).

**DurableMessageSendResult**: the send return value contains message_id, edited_ids, and a retry policy—supporting receipt tracking + delivery confirmation. On our side send returns only a `bool`.

**Health check** (`health-check-adapter.ts`): probes each adapter's availability at startup, with graceful degradation on failure—so one dead platform does not drag down the whole worker.

**Registration**: plugin manifest (each extension's `openclaw.plugin.json` declares `channels` capabilities), the core loader scans `extensions/*/` or npm packages, with dynamic loading + lazy instantiation.

### 2.2 Hermes (dedicated chat-bot project)

Written in Python, interfacing with 14+ platforms. Its design philosophy is simpler than OpenClaw's—no Plugin SDK abstraction layer, but a single file can hold a complete adapter (base is 1500 lines).

**BasePlatformAdapter ABC**

`gateway/platforms/base.py`:

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

5+ async abstract methods. Uniformly returns a `SendResult` dataclass (with `message_id` / `retryable` flags).

**Neutral message structure**

```python
@dataclass
class MessageEvent:
    text: str
    message_type: MessageType = MessageType.TEXT
    source: SessionSource         # platform, chat ID, user ID, thread_id
    media_urls: List[str] = []    # cache paths downloaded to local
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

`MessageEvent` is the neutral structure for all platforms' messages; the adapter is responsible for the platform-native → MessageEvent translation. The dispatcher only sees MessageEvent.

**Two-dimensional session key isolation**

`build_session_key(source, group_sessions_per_user, thread_sessions_per_user)`:

```
DM:    agent:main:{platform}:dm:{chat_id}[:{thread_id}]
Group: agent:main:{platform}:group:{chat_id}[:{thread_id}][:{user_id}]
```

Threads share all users by default, groups isolate per user by default, and both can be overridden by per-channel configuration.

**Progress Streaming**

`gateway/run.py:_edit_progress_message()`:

```python
async def _edit_progress_message(message_id: str, content: str):
    result = await adapter.edit_message(
        chat_id=source.chat_id,
        message_id=message_id,
        content=content,
    )
```

Tool starts → adapter.send a placeholder message → get the `message_id` → tool stream events trigger `_edit_progress_message(message_id, latest_text)` → finalize at the end with `finalize=True`.

**Overflow handling**: `_roll_progress_overflow_if_needed()`—when progress lines exceed the platform character limit, it automatically splits into groups: the first group edits the current bubble, subsequent groups send new bubbles.

**Advanced mechanisms** (this is where hermes genuinely leads)

**Debounce to merge rapid text** (`base.py:2812-2876`):

```python
class TextDebounceState:
    event: MessageEvent
    task: asyncio.Task | None
    first_ts, last_ts: float

async def _queue_text_debounce(session_key, event):
    """merge consecutively arriving texts of the same session into one, delay 0.35s, hard cap 1.0s"""
```

When a user sends 3 messages in a row ("hi", "you there", "got a question"), the agent receives a single merged turn instead of triggering 3 agent runs.

**Quick-command bypass** (`base.py:3205-3219`):

```python
if should_bypass_active_session(cmd):   # /stop, /new, /reset, /approve
    await self._dispatch_active_session_command(...)
```

Commands like `/stop` and `/approve` go straight down a fast path, not entering the session queue and not waiting for the agent's current task to finish.

**Retryable error classification**:

```python
@dataclass
class SendResult:
    message_id: Optional[str]
    retryable: bool = False
```

The adapter distinguishes transient (network / timeout, retryable) vs permanent (auth / permission, not retryable), handing a unified signal to the dispatcher to handle.

**Attachment local caching**:

```python
def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """synchronously write to cache_dir, filename doc_{uuid12}_{original name}"""

def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """delete caches older than 24h"""
```

Download Telegram URLs locally before their 1-hour expiry → the agent can read them repeatedly afterward → clean up after 24h.

**DeliveryRouter (cross-process send)**

`gateway/delivery.py`:

```python
class DeliveryTarget:
    """origin | local | telegram:123 | slack:..."""
    platform: Platform
    chat_id: Optional[str] = None

class DeliveryRouter:
    async def deliver(content, targets, ...) -> Dict:
        """Route to all targets via adapter instances."""
```

The `outbound.send`-equivalent also goes through adapter instances, without writing a separate raw HTTP path.

**Approval flow**

Does not use reactions, uses **text commands**:

```python
async def _handle_slash_approve(self, event):
    """Handle /approve — unblock waiting agent thread(s)."""

_pending_approvals: Dict[str, Dict[str, Any]]   # session → pending
# tool thread: Event.wait() blocks
# /approve command: Event.set() wakes it up
```

Simple and stable. Reactions do have a `send_reaction` implementation at the adapter layer but are not on the approval critical path.

**Platform registration**

`gateway/platform_registry.py`:

```python
@dataclass
class PlatformEntry:
    name, label, adapter_factory, check_fn,
    validate_config, install_hint

platform_registry.register(PlatformEntry(...))
adapter = platform_registry.create_adapter("slack", config)
```

Built-ins go down a hardcoded fast path, plugin platforms self-register through the registry.

---

## 3. Three-way comparison

### 3.1 Abstraction level

| Aspect | OpenProgram | OpenClaw (fork source) | Hermes |
|---|---|---|---|
| Number of base abstract methods | 1 (`run`) | 5+ (multiple interfaces: SendAdapter / LiveAdapter / ApprovalAdapter etc.) | 5+ (`send/edit/draft/typing/handoff`) |
| Neutral message structure | none (platform-native obj) | `ChannelMeta` with media/richtext/components | `MessageEvent` + `SessionSource` dataclass |
| Send return value | bool | `DurableMessageSendResult` (with message_id/edited_ids/retry policy) | `SendResult` (with message_id + retryable) |
| Dispatch signature | sync → str | async streaming pipeline (draft → live → final) | async → streaming events |
| Session isolation | `session_scope` 4 enums | `dmScope` hardcoded + thread-bindings-policy | two-dimensional (chat × user × thread) |
| Edit/Reaction interface | none | complete (ChannelMessageLiveAdapterShape + ApprovalAdapter) | built-in |
| Progress stream | none | three stages (draft → live-preview → final, throttling built in) | edit_message + automatic overflow splitting |
| Approval mechanism | none | reaction ✓/✗ + onApprove/onDecline/onTimeout lifecycle | `/approve` text command |
| Debounce merging | none | unknown | 0.35s delay + 1s hard cap |
| Retryable signal | none | DurableMessageSendResult with backoff policy | `SendResult.retryable` |
| Health check | none | `health-check-adapter.ts` startup probe | unknown |
| Receipt tracking | none | yes (DurableMessageSendResult with delivery confirmation) | unknown |
| Structured replies | text only | embed/button/menu (ChannelMessageActionAdapter) | partial |
| Attachment caching | none | yes | UUID-prefix + 24h cleanup |
| Outbound API | `outbound.send` uses raw HTTP | goes through adapter instances (ChannelOutboundAdapter) | `DeliveryRouter(adapters: dict)` goes through adapters |
| Process model assumption | multiple deployment forms (lib + worker + script) | single daemon process | single gateway process |
| Chunking implementation | 5 duplicates | unified within the platform plugin | unified within the platform (`truncate_message`) |
| Platform registration | hardcoded dict | Plugin SDK (manifest + dynamic loader) | hybrid (built-in + registry) |
| Language | Python | TypeScript | Python |

### 3.2 Direct consequences

| Feature we want to build | Scope of change in OpenProgram | Scope of change in hermes / OpenClaw |
|---|---|---|
| Progress streaming | change base + 4 adapters + outbound + `_conversation` = 6 places | dispatcher calls `adapter.edit_message` in one place |
| Reaction approval | each of the 4 adapters adds a listener + an adapter ↔ approval bridge | hermes: one `/approve` slash handler; OpenClaw: ApprovalAdapter lifecycle ready-made |
| Edit message | base + 4 adapters + outbound = 6 places | `adapter.edit_message` in one place (already exists) |
| Add a new platform (whatsapp) | adapter + `outbound._send_xx` + chunk + bindings + accounts | hermes: adapter + registry.register; OpenClaw: new extension directory + plugin.json |
| Fix a chunking bug | change 5 files in sync | 1 utility function |

### 3.3 The "subtractions" and "additions" we made after forking OpenClaw

**Subtractions (what we dropped)**:

| OpenClaw has | What we lost when inheriting |
|---|---|
| Plugin SDK (50+ contract files) | all dropped — base.py degenerated to 21 lines |
| ChannelMessageLiveAdapterShape (streaming edit) | dropped |
| ChannelApprovalAdapter (reaction ✓/✗) | dropped |
| DurableMessageSendResult (with message_id + retry) | degenerated to bool |
| health-check-adapter | dropped |
| Reconnection + exponential backoff | dropped |
| Receipt tracking | dropped |
| Message actions (button/menu) | dropped |
| Thread binding policy | degenerated to a peer_kind string |
| Structured replies (embed) | degenerated to text only |

**Additions (what we introduced)**:

| OpenProgram has | OpenClaw counterpart |
|---|---|
| `session_scope` 4 configurable enums | `dmScope` hardcoded in the channel runtime |
| `outbound.py` stateless cross-process sender | no separate outbound, goes through adapter instances |
| `setup.py` one-click interactive enrollment | descriptor-driven setup plugin seam |

The second item—`outbound.py`—deserves a separate note: from a fork perspective it is something **we actively added**, since OpenClaw's original design had cross-process send go through the adapter too. But OpenProgram is a dual-paradigm system (see 5.F), and `outbound.send`, a stateless, cron-friendly entry point, maps precisely to the agentic-programming paradigm (Python-driven synchronous calls), and should not be removed. The real problem is **implementation-layer duplication** (chunking duplicated 5 times, the HTTP call written out once per platform) rather than **the existence of the entry point**—the refactoring direction should be "two entry points sharing one implementation layer", not "remove one entry point".

---

## 4. Current structural defects (sorted by severity)

### Defect 1: base.py is an empty shell

It only mandates `run(stop)`, not send/edit/react/chunk. As a result each adapter "does as it pleases", and there is no enforceable unified interface across platforms.

**Symptoms**: the 4 adapters differ in code style, error handling, and how they load credentials; the type checker cannot detect when an adapter fails to implement some capability.

### Defect 2: two send-message code paths

```
Path A (adapter on_message reply path)    Path B (cross-process outbound.send)
─────────────────────────────────────────────────────────────
discord.py  → discord.py SDK            outbound.py → raw HTTP
slack.py    → slack_sdk SDK             outbound.py → raw HTTP
telegram.py → outbound.send (HTTP)      outbound.py → raw HTTP  ← already goes through B
wechat.py   → internal _send           outbound.py → raw HTTP
```

`MAX_MSG_CHARS` 5 copies, `_chunk` function 5 copies, credential loading 8+ copies. Any change to "how to get bytes to the platform" must be made twice, once on each path.

Note: **the existence of two entry points is reasonable** (see 5.F—they serve two paradigms: the adapter path for the dispatcher, the outbound path for the agentic-programming driver), the problem is not "having two entry points" but "two independent implementations". The refactoring should let the two entry points share one implementation layer, not merge the entry points.

### Defect 3: dispatch_inbound's synchronous signature blocks streaming

`(...) -> str` returns the complete reply in one shot. The adapter cannot get intermediate events, and therefore:

- Progress streaming is impossible (the adapter does not know a tool is running)
- A typing indicator is impossible (the adapter does not know the LLM is thinking)
- Real-time edit is impossible (the adapter cannot get the token stream)

Ironically, the dispatcher's internal `_on_event` already emits `tool_use` / `stream_event` / `tool_result` envelopes (see `agent/_event_parsing.py`), they just are not fed back to the channel—only broadcast to webui.

### Defect 4: no neutral ChannelMessage structure (✓ fixed, commit faaeb1ee)

Each adapter handles the platform-native object itself → passes the three strings `(chat_id, user, text)` straight to dispatch_inbound. There is no shared schema to hang reply / quote / thread / attachment support on.

If in the future the agent wants to "quote that earlier message" or "read an image attachment", each adapter would have to write it out separately.

**Fix**: add `_message.py:ChannelMessage`, a frozen dataclass with `text` / `chat_id` / `user_id` / `user_display` / `chat_type` / `ts` / `reply_to_id` / `thread_id` / `attachments` fields. All 4 adapter entry points parse out a ChannelMessage and then unpack it to pass to dispatch_inbound. The dispatch_inbound signature is unchanged (compatible with existing callers), and ChannelMessage is currently an adapter-internal tool—but `reply_to_id` / `thread_id` / `attachments` are already extracted in each adapter, so when dispatch_inbound consumes these fields in the future the parse step is already in place.

### Defect 5: _conversation.py is a single 483-line file with 5 responsibilities

- Routing (binding + alias lookup)
- session_key computation (scope + reset policy)
- session creation / loading / persistence
- dispatcher invocation
- webui broadcast

Per OpenProgram's established "hierarchical code structure" preference, something of this size should be split. But it should wait until the abstraction layer is settled, otherwise splitting it now means reworking it afterward.

### Defect 6: account_id passed twice

```python
DiscordChannel(account_id="default")          # constructor argument
...
dispatch_inbound(..., account_id="default")    # call argument
```

The same value is kept once in the adapter instance and once in the dispatch call. If we later want "one adapter, multiple accounts per process", this design is a trip wire.

### Defect 7: reverse dependency

`outbound._send_wechat` reverse-imports `wechat._make_wechat_uin`. A lower-layer module depending on a leaf module's private function—any internal wechat refactor can break outbound.

### Defect 8: single session-isolation granularity

```python
peer_id = "{channel_id}_{user_id}"   # discord / slack
peer_id = "{chat_id}"                # telegram
```

Joining chat and user into a single peer_id string loses the two-dimensional information. `agent.session_scope` has only 4 enum values (main / per-peer / per-channel-peer / per-account-channel-peer), and does not support the "share within a thread" mode that hermes enables by default.

### Defect 9: error signals not classified (✓ fixed, commit f4b7ca9f)

The adapter `send` returns a `bool`. The failure cause (transient network vs permanent auth vs rate limit) cannot be propagated up to the dispatcher. The dispatcher cannot retry intelligently, nor display the cause correctly in the UI.

**Fix**: add a `SendResult` dataclass (`_transport.py:SendResult`) with `ok` / `message_id` / `error_kind` / `error_detail` / `retryable` fields. `error_kind` enumerates `auth` / `rate_limit` / `bad_target` / `network` / `not_supported` / `unknown`. `_transport.post_message` / `patch_message` now return `SendResult`; `outbound.send` keeps the bool signature, with a new `outbound.send_full()` exposing the full result; `Channel.send_text` / `edit_text` likewise, adding `send_text_full` / `edit_text_full` variants. Telegram / Discord / Slack business-error descriptions have `_telegram_kind_from_description` / `_slack_kind_from_error` respectively to infer `error_kind`, and HTTP status codes go through `_classify_http_status`.

### Defect 10: platform registration hardcoded (✓ fixed, commit 0cac6004)

`channels/__init__.py:CHANNEL_CLASSES` is a hardcoded dict. Adding a new platform requires changing 4 places (channels/, accounts/, bindings/, setup/). Plugin-provided platforms are impossible.

**Fix**: split `CHANNEL_CLASSES` into `_BUILTIN_CHANNEL_CLASSES` (the 4 built-ins always present) + `_PLUGIN_CHANNEL_CLASSES` (externally registered). Plugins register in two ways: (1) declaring `[project.entry-points."openprogram.channels"]` in `pyproject.toml` (scanned at startup via `importlib.metadata.entry_points`); (2) an imperative `register_channel(name, cls)` call (used inside plugin hooks). Built-ins take priority—a plugin with the same name is silently ignored, overriding a built-in is not allowed. `CHANNEL_CLASSES` is retained as a dict-like proxy (preserving compatibility with all existing callers).

---

## 5. A few inferences

**A. Progress streaming is not "adding a feature", it's "wiring an already-existing event stream to the channel"**

The dispatcher already emits `tool_use` / `stream_event` envelopes. `dispatch_inbound._on_event` is already subscribing. The only things missing are: (1) how the channel subscribes, and (2) how the channel edits a message it has already sent. The second requires base.py to have `edit_message` and adapter.send to return a message_id—this is an **abstraction-layer refactor**, not a feature addition.

**B. Adding `edit_message` to each of the 4 adapters directly would double Defect 2**

Adding methods directly in the adapters without refactoring base: now 4 send implementations + 4 edit implementations + 4 react implementations = 12 copies of platform code, plus outbound's 12 = 24 copies. This is the disaster of stacking "two sets of code" times "three operations".

**C. Those "advanced" mechanisms in hermes (debounce / quick-command bypass / retryable) are not required to do now**

They are optimizations hermes figured out after running large volumes of production traffic. OpenProgram does not have that QPS at this stage, so we can build the right abstraction first and add them when the problems show up.

**D. Why OpenClaw cannot be copied wholesale—three real reasons, in layers**

From least to most important:

*Layer one (shallowest): there is no Python implementation to copy-paste.* The whole of OpenClaw is TypeScript/Node.js (`pnpm-workspaces` + `tsdown` build), `src/bindings/` has only 1 TS file, and `packages/sdk/` and `packages/plugin-sdk/` are all TS. The only 5 `.py` files are CI scripts / skill tooling, unrelated to the channel subsystem. **OpenClaw provides neither a Python binding nor a Python SDK.** To reuse OpenClaw we would have to re-implement its design in Python from scratch; importing it is impossible.

But language by itself is not a barrier to borrowing—TS interface → Python `Protocol` / `abc.ABC`, TS dataclass → `@dataclass`, TS async → asyncio, TS plugin manifest → `plugin.json` (which we are already doing in `openprogram/plugins/`). Design patterns are universal across languages.

*Layer two: static typing vs dynamic typing, which affects the value of "50+ contract files".* OpenClaw writes 50+ `channel-*.ts` interface files in TS, where the compiler can enforce that a plugin implements them all and IDE hints are accurate too. The same split written in Python `Protocol`—not enforced at runtime, weak IDE hints (mypy is not on by default). So a split at "the granularity of 50 contract files" yields diminished returns in Python. This does not affect **whether the interface shapes are worth learning** (they are), only **whether each interface should be split into a separate file** (it isn't worth it).

*Layer three (deepest): async-first vs sync-with-threading paradigm.* OpenClaw is fully `async send/edit/typing/handoff`, with dispatch being a streaming pipeline (draft → live-preview → final). Hermes is async-first too. Our channel is currently synchronous + threading (one thread per adapter, `dispatch_inbound(...) -> str` returning blockingly). If we copy OpenClaw's async design wholesale, the dispatch flow must be rewritten—not just changing method signatures in base.py, but turning `dispatch_inbound` into an async generator and re-wiring all 4 adapters' event loops into asyncio. This is a real migration cost, not a simple rename at the abstraction layer.

**E. The dividing line between what to learn and what not to learn**

```
                          learn from OpenClaw   learn from hermes
─────────────────────────────────────────────────
Interface design (what)
  send/edit/typing/approve  ✓ (more complete)   ✓
  SendResult with retry      ✓                   ✓
  Streaming lifecycle        ✓ (three-state)     ✓ (single edit)
  Approval lifecycle         ✓ (complete)        ✓ (/approve command)
  Health check / probe       ✓                   —

Code organization (how)
  Plugin SDK 50+ contracts  ✗ overkill           —
  70+ files per platform    ✗ overkill           —
  single-file base + adapter —                   ✓ matches
  async-first dispatch       ✓                   ✓
```

The two projects can be learned from at different levels. OpenClaw's interface shapes are more complete and more systematic, so copying its method signatures / lifecycles / return-value structures is fine. Hermes's code-organization scale matches ours—one file for the base ABC, one file per platform, no plugin manifest. These two do not conflict: taking the method signatures of OpenClaw's `ChannelApprovalAdapter` / `ChannelMessageLiveAdapterShape` and landing them in a hermes-style "base + 4 adapter files" organization is the most reasonable plan.

**F. Compatibility—channel refactor vs OpenProgram's existing paradigms**

This is the truly biggest design risk. OpenProgram already has two paradigms coexisting internally:

```
Paradigm A: agentic programming (the main pitch, the first paragraph of the README)
  Python-driven → if/else/for/while control flow
  @agentic_function creates a Context node
  Runtime.exec requests the LLM only when explicitly called
  entry point: Python code written by the programmer

Paradigm B: agent loop (the actual path for channel/webui chat)
  the LLM decides what tools to call and when
  process_user_turn → agent_loop → tool streaming
  Channel adapter receives a user message → dispatch_inbound → this path
  entry point: external message
```

The channel currently **hangs only off Paradigm B**. This means:

1. **`outbound.send` is not a "misplaced addition"**—it is exactly the path Paradigm A needs. A cron-driven @agentic_function that wants to send the user "good morning" does not need to spin up an adapter instance, does not need to subscribe to stream events, does not need to bind to a session lifecycle—just send it directly over raw HTTP. OpenClaw's "everything goes through the adapter" and hermes's `DeliveryRouter(adapters: dict)` are both reasonable designs **under a single-daemon-process model**—they assume the cron scheduler + platform adapter + agent runtime all run in the same process, so a cron job can get the adapter dict from dependency injection.

   In Section 4 of my earlier audit, Defect 2 labeled `outbound.send` as a "two sets of send code" problem—that judgment is correct for **implementation-layer duplication** (chunking copied 5 times, the HTTP call written out per platform, credential loading in multiple copies), but **keeping two entry points** is itself a reasonable result of the paradigm division of labor, and should not be removed.

   OpenProgram's multiple deployment forms break this process-model assumption:

   ```
   Deployment scenario                         where is the adapter instance
   ──────────────────────────────────────────────────────────
   openprogram worker running               yes (the worker process holds it)
   user writes a Python script importing @agentic     no
   cron running in a separate process outside worker  no
   Jupyter notebook experiment              no
   pytest test                              no
   ```

   Paradigm A is by design "library mode"—the user imports it in their own script, **not assuming a worker process exists**. So the hermes / OpenClaw style of "all sends go through an adapter instance" does not work under our multiple deployment forms. The outbound.send path must be kept.

2. **The correct refactoring shape is: two entry points, one implementation**

   ```
   Paradigm A entry: outbound.send_one_shot(channel, account, target, text)
   Paradigm B entry: adapter.send_text(target, text) -> msg_handle
                adapter.edit_text(msg_handle, text)

                       ↓ both call

   implementation layer: _post_message(channel, account, target, text, *, edit_of=None)
           HTTP call + chunking + credential loading, only one copy
   ```

   This way chunking is no longer in 5 copies and credentials are no longer loaded 8+ times, but the two entry-point paths are each retained, serving the two paradigms respectively.

3. **The base.py abstraction refactor must not "monopolize" the channel**

   If the channel is refactored into an async-first streaming pipeline the OpenClaw / hermes way, we must ensure **Paradigm A can still send messages synchronously**. Concretely: it is fine for base.py's abstract methods to be async, but a synchronous wrapper (`outbound.send_one_shot`) must be exposed at the module top level so that an agentic_function can call it without understanding asyncio.

4. **Streaming edit is compatible with agentic_function**

   In Paradigm A an @agentic_function may want to send the user intermediate progress ("observed the login page", "clicked the login button"). There is currently no API for it to do so. After the refactor this capability should be made available—not locked to the dispatcher's stream pipeline, but with an agentic_function also able to get a msg_handle and edit it itself.

**G. WeChat is the hardest to fit into the abstraction**

The iLink API appears not to support edit_message (a sent message cannot be changed). This means that after base.py adds an `edit_message` abstraction, the wechat adapter must either implement a fake `edit_message` (delete the old, send a new one) or raise NotImplementedError—the former changes the semantics, the latter breaks the unified interface. How hermes handles such a limitation needs further investigation (IRC has a similar problem).

---

## 6. Open questions

Things to decide at the next discussion:

1. Should we do the base.py abstraction refactor? It is 3-4h of work, no new feature, but it is the foundation for all subsequent features
2. Refactor granularity: abstract it to hermes's level (async send/edit/typing/draft, 5+ methods) or do the minimum first (send returns message_id + edit_message)?
3. Add the neutral ChannelMessage structure now, or wait until the reply/quote need arises?
4. `outbound.send` stays (it serves Paradigm A), but should the implementation layer be merged with the adapter's send into a single `_post_message` function, removing the 5 duplicates?
5. Should the approval mechanism use hermes's `/approve` command (simple and stable) or OpenClaw's reaction lifecycle (intuitive UX but complex state)?
6. How to handle WeChat not supporting edit—`edit_message` raise vs fake implementation vs making the base interface itself optional?
7. Should session_scope be expanded into hermes's two dimensions (chat × user × thread)? OpenClaw's thread-bindings-policy offers another line of thinking

This document stops here. The concrete plan will be settled after you read it and give feedback.
