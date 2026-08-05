# Channel Subsystem — Requirements and Invariants

This document states what the channel layer must hold to: the interface each
platform adapter presents, how the two send paths relate, how sessions are keyed,
and where the design boundaries sit. The comparison sections record what
OpenClaw and hermes do at each of these points and why our answer differs.

For the layer's structure and message flow, see
[`design.md`](design.md).

## 1. The abstraction

### 1.1 Channel ABC

`openprogram/channels/base.py` defines the contract every adapter satisfies:

```python
@dataclass(frozen=True)
class MessageHandle:
    platform: str
    account_id: str
    target: str
    message_id: str

    @property
    def editable(self) -> bool: ...


class Channel(abc.ABC):
    platform_id: str = ""

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None: ...

    def send_text(self, target, text) -> Optional[MessageHandle]: ...
    def send_text_full(self, target, text) -> SendResult: ...
    def edit_text(self, handle, new_text) -> bool: ...
    def edit_text_full(self, handle, new_text) -> SendResult: ...
```

`run(stop)` is the sole abstract method because inbound event loops differ too
much to unify: discord.py's Gateway, Slack Socket Mode, and the Telegram and
WeChat long polls have no common shape. Everything outbound does have a common
shape, so `send_text` / `edit_text` ship as concrete defaults on the base class,
implemented over `_transport`. An adapter overrides them only when a
platform-native SDK buys something raw HTTP cannot — mention parsing, attachment
upload.

`MessageHandle` is the unit of "a message I sent and may later edit". All four
fields are strings so a handle survives serialization: one process can send and
another can edit, which is what lets a cron-driven caller and the dispatcher
address the same message.

### 1.2 Two entry points, one implementation

Outbound traffic has two entry points by design, serving two different callers:

```
Entry A (stateless, cron-friendly)   outbound.send(channel, account, user, text)
Entry B (stateful, keeps message_id) adapter.send_text(target, text) -> handle
                                     adapter.edit_text(handle, text)

                     ↓ both call

implementation layer                 _transport.post_message / patch_message
                                     HTTP call + chunking + credential loading
```

Keeping both entry points is a requirement, not an accident. Entry B needs
adapter state because progress streaming has to remember which message to edit.
Entry A must work with no adapter instance in the process at all — the reason is
in §5.F.

The invariant is that they share one implementation. `_transport` owns the HTTP
call, credential loading, and chunking; neither entry point reimplements them.

### 1.3 Structured send results

`_transport.post_message` and `patch_message` return a `SendResult` carrying
`ok` / `message_id` / `error_kind` / `error_detail` / `retryable`. `error_kind`
is one of `auth` / `rate_limit` / `bad_target` / `network` / `not_supported` /
`unknown`, inferred from HTTP status via `_classify_http_status` and from
platform-specific error descriptions via `_telegram_kind_from_description` and
`_slack_kind_from_error`.

A `bool` return cannot distinguish a transient network failure from a permanent
auth failure, so it cannot support intelligent retry or an accurate UI message.
The `bool`-returning forms (`outbound.send`, `Channel.send_text`,
`Channel.edit_text`) remain for callers that only need success or failure; the
`_full` variants expose the structured result.

### 1.4 Neutral inbound message

`_message.py:ChannelMessage` is the platform-neutral inbound structure. It is a
frozen dataclass with `text` / `chat_id` / `user_id` / `user_display` /
`chat_type` / `ts` / `reply_to_id` / `quoted_text` / `thread_id` /
`attachments` (a tuple of `Attachment` download descriptors). Each of the
four adapters parses its platform-native object into a `ChannelMessage` at the
entry point.

The base pipeline consumes `quoted_text` (prepended as a `>` quoted block) and
`attachments` (downloaded via `_attachments`, small images forwarded as vision
input). `thread_id` is parsed but not yet folded into the session key.

### 1.5 Inbound dispatch

`dispatch_inbound(channel, account_id, peer_kind, peer_id, user_text,
user_display, progress_stream=False) -> Optional[str]` handles a message
end to end:

1. Look up `session_aliases` / `bindings` → decide agent_id
2. Compute `session_key` per `agent.session_scope`
3. Apply the `daily_reset` / `idle_minutes` reset policy
4. `_load_or_init_session` writes SessionDB
5. Build a `TurnRequest` and call `process_user_turn`
6. Append the reply to SessionDB
7. Broadcast a `channel_turn` envelope to webui

With `progress_stream=False` it returns the complete reply string and the
adapter sends it. With `progress_stream=True` the dispatcher drives the channel
directly through `send_text` / `edit_text`, so tool events reach the user while
the turn is still running.

### 1.6 Platform registration

`channels/__init__.py` splits registration into `_BUILTIN_CHANNEL_CLASSES` (the
four built-ins, always present) and `_PLUGIN_CHANNEL_CLASSES` (externally
registered). A plugin registers either by declaring
`[project.entry-points."openprogram.channels"]` in `pyproject.toml`, scanned at
startup through `importlib.metadata.entry_points`, or by calling
`register_channel(name, cls)` from a plugin hook.

Built-ins take priority: a plugin claiming a built-in name is ignored rather
than allowed to override it. `CHANNEL_CLASSES` remains as a dict-like proxy over
both, so existing callers are unaffected.

---

## 2. How other projects solve this

There are two comparables: **OpenClaw** (the source we forked from, TypeScript)
and **hermes** (a dedicated chat-bot project, Python). Neither opencode nor
claude-code has a channel subsystem — their surfaces are CLI/TUI/Web/IDE,
addressing a human sitting at the front end rather than plugging into a
Discord or Slack group.

### 2.1 OpenClaw

Source: `references/openclaw/src/channels/` plus
`references/openclaw/extensions/{discord,slack,telegram}/`.

**Layout**: the core `src/channels/` holds many fine-grained files (routing /
account / approval / typing / draft-stream / health-check /
thread-bindings-policy), and each platform gets its own directory under
`extensions/{name}/` — discord alone has 70+ files, slack 40+, telegram 35+.

**Plugin SDK** (`src/plugin-sdk/channel-*.ts`, 50+ contract files) isolates the
core from platform implementations completely. The core sees only abstract
interfaces:

```typescript
ChannelMessageSendAdapter        // send capability
ChannelMessageLiveAdapterShape   // live message editing (draft → live-preview → final)
ChannelApprovalAdapter           // reaction ✓/✗ confirmation + timeout/retry
ChannelMessageActionAdapter      // button/menu action handler
ChannelOutboundAdapter           // cross-process send also goes through the adapter
```

**Streaming edit** (`src/plugin-sdk/channel-streaming.ts` +
`extensions/discord/src/draft-stream.*`): a message has three lifecycle states.

```
draft → live-preview (throttled edit) → final
```

A draft goes out, the message is edited continuously while the tool runs, and
the pipeline finalizes at the end. Throttling is built into the pipeline.

**Reaction approval** (`src/channels/ack-reactions.ts` +
`extensions/discord/src/approval-native.ts`):

```typescript
type ChannelApprovalAdapter {
    onApprove, onDecline, onTimeout
}
```

When a dangerous tool fires the bot adds a ✓/✗ emoji reaction, the user clicks
it, and the adapter notifies the dispatcher. The full lifecycle covers timeout,
retry, and cancel.

**DurableMessageSendResult**: the send return value carries message_id,
edited_ids, and a retry policy, supporting receipt tracking and delivery
confirmation.

**Health check** (`health-check-adapter.ts`): probes each adapter's availability
at startup and degrades gracefully on failure, so one dead platform does not
take down the whole worker.

**Registration**: a plugin manifest — each extension's `openclaw.plugin.json`
declares its `channels` capabilities — with the core loader scanning
`extensions/*/` or npm packages, dynamically loading and lazily instantiating.

### 2.2 Hermes

Python, interfacing with 14+ platforms. Its design philosophy is simpler than
OpenClaw's: no Plugin SDK layer, and a single file holds a complete adapter
(base is 1500 lines).

**BasePlatformAdapter ABC** (`gateway/platforms/base.py`):

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

Five-plus async abstract methods, all returning a `SendResult` dataclass with
`message_id` / `retryable`.

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

The adapter translates platform-native → `MessageEvent`; the dispatcher sees
only `MessageEvent`.

**Two-dimensional session key isolation**
(`build_session_key(source, group_sessions_per_user, thread_sessions_per_user)`):

```
DM:    agent:main:{platform}:dm:{chat_id}[:{thread_id}]
Group: agent:main:{platform}:group:{chat_id}[:{thread_id}][:{user_id}]
```

Threads share across users by default, groups isolate per user by default, and
per-channel configuration overrides both.

**Progress streaming** (`gateway/run.py:_edit_progress_message()`):

```python
async def _edit_progress_message(message_id: str, content: str):
    result = await adapter.edit_message(
        chat_id=source.chat_id,
        message_id=message_id,
        content=content,
    )
```

A tool starts, `adapter.send` posts a placeholder, the `message_id` comes back,
tool stream events trigger `_edit_progress_message(message_id, latest_text)`,
and `finalize=True` closes it out. `_roll_progress_overflow_if_needed()` handles
the case where progress lines exceed the platform character limit: the first
group edits the current bubble, later groups become new bubbles.

**Debounce merging** (`base.py:2812-2876`):

```python
class TextDebounceState:
    event: MessageEvent
    task: asyncio.Task | None
    first_ts, last_ts: float

async def _queue_text_debounce(session_key, event):
    """merge consecutively arriving texts of the same session into one, delay 0.35s, hard cap 1.0s"""
```

Three messages sent in a row ("hi", "you there", "got a question") reach the
agent as one merged turn rather than three agent runs.

**Quick-command bypass** (`base.py:3205-3219`):

```python
if should_bypass_active_session(cmd):   # /stop, /new, /reset, /approve
    await self._dispatch_active_session_command(...)
```

`/stop` and `/approve` take a fast path, skipping the session queue and not
waiting for the agent's current task.

**Attachment local caching**:

```python
def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """synchronously write to cache_dir, filename doc_{uuid12}_{original name}"""

def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """delete caches older than 24h"""
```

Telegram URLs are downloaded locally before their one-hour expiry so the agent
can read them repeatedly, with cleanup after 24h.

**DeliveryRouter** (`gateway/delivery.py`):

```python
class DeliveryTarget:
    """origin | local | telegram:123 | slack:..."""
    platform: Platform
    chat_id: Optional[str] = None

class DeliveryRouter:
    async def deliver(content, targets, ...) -> Dict:
        """Route to all targets via adapter instances."""
```

The `outbound.send` equivalent also goes through adapter instances rather than a
separate raw HTTP path.

**Approval flow**: text commands rather than reactions.

```python
async def _handle_slash_approve(self, event):
    """Handle /approve — unblock waiting agent thread(s)."""

_pending_approvals: Dict[str, Dict[str, Any]]   # session → pending
# tool thread: Event.wait() blocks
# /approve command: Event.set() wakes it up
```

Simple and stable. The adapter layer does implement `send_reaction`, but
reactions are not on the approval critical path.

**Platform registration** (`gateway/platform_registry.py`):

```python
@dataclass
class PlatformEntry:
    name, label, adapter_factory, check_fn,
    validate_config, install_hint

platform_registry.register(PlatformEntry(...))
adapter = platform_registry.create_adapter("slack", config)
```

Built-ins take a hardcoded fast path; plugin platforms self-register through the
registry.

---

## 3. Three-way comparison

| Aspect | OpenProgram | OpenClaw (fork source) | Hermes |
|---|---|---|---|
| Base abstract methods | 1 (`run`) + concrete send/edit defaults | 5+ (SendAdapter / LiveAdapter / ApprovalAdapter etc.) | 5+ (`send/edit/draft/typing/handoff`) |
| Neutral message structure | `ChannelMessage` dataclass | `ChannelMeta` with media/richtext/components | `MessageEvent` + `SessionSource` dataclass |
| Send return value | `SendResult` (ok/message_id/error_kind/retryable) | `DurableMessageSendResult` (message_id/edited_ids/retry policy) | `SendResult` (message_id + retryable) |
| Dispatch signature | sync → str, or `progress_stream=True` | async streaming pipeline (draft → live → final) | async → streaming events |
| Session isolation | `session_scope` 4 enums | `dmScope` hardcoded + thread-bindings-policy | two-dimensional (chat × user × thread) |
| Edit interface | `edit_text` on the base class | complete (ChannelMessageLiveAdapterShape) | built-in |
| Progress stream | dispatcher drives send_text/edit_text | three stages, throttling built in | edit_message + automatic overflow splitting |
| Approval mechanism | text-command bridge (`_question_commands.py`) | reaction ✓/✗ + onApprove/onDecline/onTimeout | `/approve` text command |
| Debounce merging | none | unknown | 0.35s delay + 1s hard cap |
| Retryable signal | `SendResult.retryable` | DurableMessageSendResult with backoff | `SendResult.retryable` |
| Health check | none | `health-check-adapter.ts` startup probe | unknown |
| Receipt tracking | none | yes (delivery confirmation) | unknown |
| Structured replies | text only | embed/button/menu | partial |
| Attachment handling | download to state dir + vision input (`_attachments`) | cached | UUID-prefix + 24h cleanup |
| Outbound API | `outbound.send` shares `_transport` | goes through adapter instances | `DeliveryRouter(adapters: dict)` |
| Process model assumption | multiple deployment forms (lib + worker + script) | single daemon process | single gateway process |
| Chunking implementation | `_transport._chunk`, plus adapter-local copies | unified within the platform plugin | unified (`truncate_message`) |
| Platform registration | built-in dict + entry-point plugins | Plugin SDK (manifest + dynamic loader) | hybrid (built-in + registry) |
| Language | Python | TypeScript | Python |

---

## 4. Design boundaries

### 4.1 Why `run` stays the only abstract method

Mandating `send` / `edit` / `react` as abstract would force every adapter to
implement capabilities its platform may not have. Instead the base class
provides working implementations over `_transport` and lets an adapter override
what it can improve. An adapter that implements nothing beyond `run` is still
fully functional for send and edit.

### 4.2 Why WeChat is the hard case

The iLink API does not support editing a sent message. `MessageHandle.editable`
encodes this: WeChat handles carry an empty `message_id` and report
`editable == False`, and `edit_text_full` returns
`SendResult.fail("not_supported", ...)` rather than raising or faking the edit
by deleting and reposting.

This is why edit capability is expressed as a property of the handle rather than
as an abstract method a platform must implement. A platform that cannot edit
reports so through the same return type every caller already handles, and no
caller has to special-case a platform name.

### 4.3 Cross-platform edits are refused

`edit_text_full` checks `handle.platform != self.platform_id` and returns
`SendResult.fail("bad_target", ...)`. Coordinating across adapters is the
caller's concern; the base class holds the line that one adapter edits only its
own platform's messages.

### 4.4 `_conversation.py` responsibilities are split

Routing, session-key computation, session persistence, dispatcher invocation,
and webui broadcast live in separate modules: `_session_routing.py`,
`_session_store.py`, `_broadcast.py`, with `_conversation.py` retaining the
end-to-end `dispatch_inbound` flow. This follows the repository's preference for
hierarchical code structure.

---

## 5. Rationale

**A. Progress streaming is wiring, not a new feature**

The dispatcher already emits `tool_use` / `stream_event` / `tool_result`
envelopes (see `agent/_event_parsing.py`), and `dispatch_inbound._on_event`
already subscribes. What made streaming possible was the abstraction: a `send`
that returns a `message_id` and an `edit_text` that can act on it. With
`MessageHandle` and `_transport.patch_message` in place,
`progress_stream=True` is the dispatcher consuming an event stream that was
already there.

**B. Adding per-adapter methods without a shared implementation multiplies cost**

Adding `edit` directly to each of the four adapters without a shared transport
would mean four send implementations plus four edit implementations plus four
react implementations, doubled again by the outbound path. Routing both entry
points through `_transport` is what keeps a new operation to one implementation
rather than eight.

**C. Hermes's advanced mechanisms are deferred deliberately**

Debounce merging, quick-command bypass, and attachment caching are optimizations
hermes arrived at after running production traffic volumes. They are not
required at OpenProgram's current request rate. The order is to get the
abstraction right first and add these when the problems appear.

**D. Why OpenClaw is not copied wholesale**

Three reasons, from shallowest to deepest.

*There is no Python implementation to copy.* OpenClaw is entirely
TypeScript/Node.js (`pnpm-workspaces` + `tsdown` build); `src/bindings/` holds
one TS file, and `packages/sdk/` and `packages/plugin-sdk/` are all TS. The only
five `.py` files are CI scripts and skill tooling, unrelated to channels.
OpenClaw provides neither a Python binding nor a Python SDK, so reuse means
re-implementing the design rather than importing it.

Language alone is no barrier to borrowing, though: a TS interface maps to a
Python `Protocol` or `abc.ABC`, a TS dataclass to `@dataclass`, TS async to
asyncio, and a TS plugin manifest to `plugin.json` (already done in
`openprogram/plugins/`). Design patterns carry across languages.

*Static versus dynamic typing changes what 50+ contract files are worth.* In TS
the compiler enforces that a plugin implements every interface, and IDE hints
are accurate. The same split written as Python `Protocol`s is not enforced at
runtime and gives weaker hints, since mypy is not on by default. So a split at
that granularity returns less in Python. This affects whether each interface
deserves its own file (it does not), not whether the interface shapes are worth
learning (they are).

*Async-first versus sync-with-threading.* OpenClaw is async throughout
(`send/edit/typing/handoff`) with dispatch as a streaming pipeline; hermes is
async-first as well. Our channel layer is synchronous plus threading — one
thread per adapter, `dispatch_inbound` returning blockingly. Adopting the async
design wholesale would mean rewriting the dispatch flow: turning
`dispatch_inbound` into an async generator and re-wiring all four adapters'
event loops into asyncio. That is a real migration cost, not a rename at the
abstraction layer.

**E. What to learn from each project**

```
                          from OpenClaw       from hermes
─────────────────────────────────────────────────────────
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

The two are learned from at different levels, and the levels do not conflict.
OpenClaw's interface shapes are more complete and more systematic, so its method
signatures, lifecycles, and return-value structures are worth following.
Hermes's code-organization scale matches ours — one file for the base ABC, one
per platform, no plugin manifest. Taking OpenClaw's method signatures and
landing them in a hermes-scale file organization is the combination this design
uses.

**F. Why the stateless outbound entry point exists**

OpenProgram runs two paradigms:

```
Paradigm A: agentic programming
  Python drives → if/else/for/while control flow
  @agentic_function creates a Context node
  Runtime.exec requests the LLM only when explicitly called
  entry point: Python code written by the programmer

Paradigm B: agent loop (the path channel/webui chat takes)
  the LLM decides what tools to call and when
  process_user_turn → agent_loop → tool streaming
  entry point: an external message
```

Channels attach to Paradigm B. Paradigm A still needs to send: a cron-driven
`@agentic_function` that greets the user needs no adapter instance, no stream
subscription, and no session lifecycle binding.

OpenClaw's "everything goes through the adapter" and hermes's
`DeliveryRouter(adapters: dict)` are both sound designs under a single-daemon
process model, where the cron scheduler, platform adapter, and agent runtime
share a process and a cron job can obtain the adapter dict by dependency
injection. OpenProgram's deployment forms break that assumption:

```
Deployment scenario                                where is the adapter instance
────────────────────────────────────────────────────────────────────────────────
openprogram worker running                         in the worker process
user script importing @agentic_function            nowhere
cron in a separate process outside the worker      nowhere
Jupyter notebook experiment                        nowhere
pytest test                                        nowhere
```

Paradigm A is library mode by design: the user imports it into their own script
and no worker process is assumed. So `outbound.send` is a requirement of the
paradigm split, not a duplicate of the adapter path. What must not be duplicated
is the implementation beneath them, which is why both route through
`_transport`.

Two consequences follow. Any future move to an async-first base must keep a
synchronous wrapper at module top level so an `@agentic_function` can send
without dealing with asyncio. And streaming edit should stay reachable from
Paradigm A: an `@agentic_function` reporting intermediate progress should be
able to hold a `MessageHandle` and edit it, rather than the capability being
locked to the dispatcher's pipeline.

---

## 6. Appendix — implementation status

In place: the `Channel` ABC with `MessageHandle` and concrete
`send_text` / `edit_text`; `_transport` as the shared implementation layer for
both entry points; `SendResult` error classification; the `ChannelMessage`
neutral inbound structure; entry-point-based platform registration;
`progress_stream=True` inbound dispatch; and the `_conversation.py` split into
routing, session-store, and broadcast modules.

Not yet done:

- **Chunking is still duplicated.** `_transport` has `_chunk`, but
  `discord.py`, `slack.py`, and `wechat.py` keep local `_chunk` copies and each
  of the four adapters defines its own `MAX_MSG_CHARS`. Adapter reply paths that
  still use the platform SDK directly have not moved to `send_text`.
- **Session isolation stays one-dimensional.** `peer_id` joins chat and user
  into a single string, and `session_scope` has four enum values
  (main / per-peer / per-channel-peer / per-account-channel-peer). The
  thread-shared mode hermes enables by default is not expressible.
  `ChannelMessage.thread_id` is parsed in anticipation of this.
- **`account_id` is passed twice**, once to the adapter constructor and once to
  `dispatch_inbound`. This blocks one adapter serving multiple accounts in a
  process.
- **`thread_id` is parsed but not consumed.** Quoted text and attachments flow
  into the turn; thread-scoped session isolation is still open.
- **No health check or receipt tracking.** Adapter availability is not probed at
  startup, and delivery is not confirmed after send.
