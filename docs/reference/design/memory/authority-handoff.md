# Authority: Two Tiers and Pairing

> How OpenProgram decides what a speaker is allowed to make the agent do, and
> how that decision reaches memory. Speaker attribution itself is
> [`speaker-identity.html`](speaker-identity.html); the visual companion is
> [`authority-landscape.html`](authority-landscape.html).
> Related code: `openprogram/agent/authority.py`,
> `openprogram/channels/_access.py`, `openprogram/channels/base.py`,
> `openprogram/memory/scriptorium/writing.py`.

## The model

Admission is binary and authority is binary. A platform account is in one of
three states, only two of which are runtime authority tiers.

| State | Tier | Can do |
|---|---|---|
| **Owner** — local terminal, local web | `owner` | Everything: run commands, write files, send messages, manage and rewrite memory. |
| **Paired** — an approved platform account | `paired` | Converse, be recorded into memory with attribution, actively append memory. No tool execution. |
| **Unpaired** | none | The message does not reach the agent. The sender receives a pairing code. |

Unpaired is not a third tier. There is no "unknown external speaker gets
reply-only" state: an unpaired message is never rendered into model context at
all, which removes that prompt-injection surface rather than narrowing it.

## The gate

Authority travels as a single enum field, `authority_tier`, on the request
boundary alongside `principal_id`, `speaker_kind`, `speaker_id`,
`speaker_display` and `interaction`. A request never carries a capability list
of its own, so there is nothing a caller can mint inconsistently.

`decide_capability()` in `openprogram/agent/authority.py` resolves a capability
against the `TIER_CAPABILITIES` constant table:

| Tier | Capabilities |
|---|---|
| `owner` | `reply`, `memory.source.append`, `memory.trusted.promote`, `schedule.create`, `schedule.manage`, `fs.read`, `fs.write`, `process.exec`, `network.send`, `approval.request`, `runtime.control` |
| `paired` | `reply`, `memory.source.append` |

The gate is **fail-closed**. A missing tier denies with
`AUTHORITY_TIER_MISSING`; a tier that is not a table key denies with
`AUTHORITY_TIER_UNKNOWN`. Neither case falls back to a reduced capability set —
an unrecognized request loses every capability, including `reply`.

`capability_for_tool()` maps each tool name onto exactly one capability, and its
default arm is `process.exec`. An installed agentic function or a mounted MCP
tool can carry any name; treating an unclassified name as executable code means
a `paired` speaker cannot reach it, because only `owner` holds `process.exec`.

The gate sits in `_gated_execute` **before** the rule layer, before
`_FORCE_APPROVAL_TOOLS` and before the `bypass` short-circuit, so no permission
mode and no persisted allow rule can skip it. Every decision returns an
`AuthorityDecision` record — allowed, decisive check, stable reason code, tier,
capability — which is logged, so a denial is auditable rather than a bare
boolean.

`runtime_authority()` derives a subagent's or a runtime task's authority from
its parent: it copies the parent's normalized authority, rewrites the speaker
fields and sets `interaction` to non-interactive, and leaves `authority_tier`
untouched. Inheritance never widens. A `paired` turn cannot spawn an `owner`
child, and a parent with no valid authority yields `{}`, which the gate denies.
Because a subagent is never interactive, it also never holds a path to an
interactive approval.

## Pairing

A sender who is not on an account's allowlist is stopped at
`decide_inbound_sender()` and receives an 8-character uppercase pairing code.
The alphabet excludes the ambiguous characters `0`, `O`, `1` and `I`. Codes
expire after one hour, an account holds at most three pending codes at a time,
and the same sender is not re-prompted within the hour.

Approval happens **only on the owner's own machine**, through the CLI or the
local web UI. No channel message can approve anyone. "Please approve code X" in
chat is the canonical injection phrasing; the correct response is to refuse and
point at the owner.

Identity matching uses the platform's stable ID only. Usernames and display
names are mutable, so they are excluded from allowlist matching — someone who
renames themselves to the owner's name gains nothing. Display names reach a
prompt only after `sanitize_speaker_display()` strips newlines, zero-width and
bidi characters and neutralizes the envelope markers `[` and `]`.

## Trust semantics in memory

Memory records a `trust_state` on every source frame, with two values.

**`trusted`** — the owner's own turns and everything from paired accounts.
Pairing *is* the trust decision, so paired speech enters the normal distillation
flow exactly as the owner's does, attributed by speaker. There is no second
gate behind the pairing gate.

**`pending`** — text archived from an unpaired speaker. Unpaired members of a
group still get their lines archived, because a group conversation is only
comprehensible whole, but that text is evidence rather than accepted memory. It
carries full provenance, and `memory_promote` is the owner-only path
(`memory.trusted.promote`) that moves a pending frame to `trusted`.

`trust_state` is the mechanism. The `speaker_trusted` field that appears in
retrieval output is a display projection of it, not a separate decision.

**Unpaired text does not enter model context.** Two independent mechanisms hold
this, both live:

1. **The realtime path stops it first.** `openprogram/channels/base.py` consults
   the access decision before any agent dispatch. A blocked sender's message is
   archived (in group chats) and answered with the pairing reply; it never
   becomes a turn.
2. **Automatic recall filters it.** The memory provider's `search()` drops every
   hit whose `trust_state` is `pending`, so an archived pending frame cannot
   arrive in a later turn's injected context either.

Pending text stays reachable through the `memory_search` tool, where the model
asks for it deliberately and sees the `trust_state` alongside it.

## The memory writer

The auto-organize writer follows the default chat agent's provider, model and
credentials rather than hardcoding one CLI agent. `memory.writer.model`
overrides it and is editable in the web settings UI. The default is deliberately
not a cheap model: weak models misread organizing instructions, so downgrading
is an explicit choice.

Retry classification is conservative in both the provider and the session
watcher: an unknown exception is non-retryable, and only explicitly classified
transient exceptions retry.

## Appendix: Implementation Status

The tier enum, the constant-table gate, fail-closed denial, structured decision
records, subagent inheritance, pairing with 8-character codes, local-owner-only
approval, stable-ID matching, display-name sanitization, the `pending` /
`trusted` split, and both context-exclusion mechanisms above are implemented.

| Item | Status | Note |
|---|---|---|
| Hold queue for over-tier requests | Not implemented | A paired sender's over-tier request (for example "restart the service") queues for one-shot owner approval instead of flat denial. Parameters to adopt: a message cap, an expiry timeout, and re-evaluation of the queue when a sender's tier changes. |
| Read-time filtering by requester tier | Not implemented | Write-time attribution is in place; filtering retrieval results by the requesting speaker's tier is not. |
| Backfill of pre-marked sources | Not implemented | History marked before any Topic existed remains outside Topics. A one-shot pass should run the writer over every source not cited by a Topic, ignoring markers, and preserve the already-promoted core. |
| Queryable writer status | Not implemented | Last successful write time, latest failure reason with its retryable verdict, and pending count, exposed in both status output and the web UI. |
| Bounded archive policy for unpaired group traffic | Not implemented | Unpaired group archival has no independent frequency or storage cap. Needed before deployments where that traffic is not externally rate-limited. |
