# Authority: Two Tiers and Pairing

> How OpenProgram decides what a speaker is allowed to make the agent do, and
> how that decision reaches memory. Speaker attribution itself is
> [`speaker-identity.html`](speaker-identity.html); the visual companion is
> [`authority-landscape.html`](authority-landscape.html).
> Related code: `openprogram/agent/authority.py`,
> `openprogram/channels/_access.py`, `openprogram/channels/base.py`,
> `openprogram/memory/writing.py`.

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

## Read scoping: stamp on write, filter on read

The policy follows LangGraph's store model: visibility is decided once, when
the content is written, and recorded on the content itself. A read carries the
requester's tier and keeps only what that tier may see. No read path recomputes
authority from conversation state, and no read path consults the pairing store.

**Visibility label.** Every Topic block carries one `visibility` value:

| Value | Written when | Readable by |
|---|---|---|
| `shared` | Every Source the block cites is `authority_tier=paired`, or the owner marked the block shared | owner, paired |
| `owner` | Any cited Source is `authority_tier=owner`, or the block cites no Source | owner |

Two values, not a per-speaker scope. A paired requester reads all `shared`
Topics rather than only its own Source-derived memory: the memory is one shared
workspace by product decision, speakers are already attributed inside it, and a
per-speaker scope would need a second identity index on every read for a
distinction the product does not make. Pending Sources have no visibility label
because they are not readable content at all; they are reachable only through
the owner's explicit review path.

**Where the label lives.** In the Topic block's own definition line, beside
`Time:` and `Sources:`, so it travels with the block through the existing
Markdown parse and needs no side table that could drift from the prose. The
default for an unlabeled block is `owner`, which makes the migration a no-op:
today's entire workspace stays owner-only until a write relabels a block.

**Derivation, not declaration.** The writer never asks the model for a
visibility value. `install_state` computes it from the trust metadata of the
block's cited Sources, which task 2 now guarantees is Runtime-owned. A model
that could name its own visibility could publish owner content to a paired
reader by writing one word.

**Mixed-authority blocks.** A block whose cited Sources span both tiers is
`owner`. The alternative — splitting the prose — would hand a paired reader a
sentence fragment whose meaning depends on the removed half. The block is the
smallest unit of meaning the format has, so it is the smallest unit visibility
can act on. Where a mixed block is genuinely wanted in shared scope, the owner
splits it into two blocks; that is a memory-organization act, not a filter.

**Core injection.** `core.md` is rendered from `topics/core.md` on every write.
Rendering gains a second output: `core.md` (owner) and `core.shared.md`
(shared blocks only). `system_prompt()` picks the file by the session's tier.
Rendering both at write time rather than filtering at injection time keeps the
per-turn path free of a parse, and keeps the two views impossible to skew.

**Caches and indexes.** The lexical and embedding indexes keep indexing
everything and filter at query time on the label, rather than maintaining one
index per tier. Two indexes would double both the build cost and the ways they
can disagree. The filter is applied inside `MemoryBM25Index.search` and
`MemoryEmbeddingIndex.search`, below every caller, so a new caller cannot
forget it. Persisted index files stay a single artifact; the label is a stored
field on each row.

**Owner review of pending evidence.** Unchanged by this design and explicitly
exempt from the filter: the owner's explicit review path returns pending Source
records with `speaker_id`, `speaker_display`, `principal_id`, `trust_state` and
`authority_tier` intact, because deciding whether to promote is exactly the act
of reading who said it. Pending text never enters automatic context on any
tier, which remote `9b47a45e` already enforces at the recall boundary.

**Acceptance matrix.** Each row names the Runtime entry point that enforces
the policy and the case its test has to cover.

| # | Read surface | Enforcing Runtime entry point | Case | Test level |
|---|---|---|---|---|
| 1 | Core system prompt | `LocalMemoryBackend.system_prompt` | A paired session receives `core.shared.md`; no owner block appears in it | unit |
| 2 | Automatic per-turn recall | `LocalMemoryBackend.search` | A paired session's recall returns no `owner` block and no pending Source | unit |
| 3 | Explicit `memory_search` | `MemoryBM25Index.search` | A paired requester's hits exclude `owner` blocks at every `top_k` | unit |
| 4 | Embedding search | `MemoryEmbeddingIndex.search` | Same exclusion as row 3, same requester, same query | unit |
| 5 | `memory_get` | `inspect.read_file` | A paired requester reading a Topic file receives only its `shared` blocks; an all-`owner` file is a not-found, not an empty file | unit |
| 6 | `memory_grep` | `inspect.grep` | A literal string present only in an `owner` block produces no match for a paired requester | unit |
| 7 | `memory_browse` | `inspect.list_files` | A file with no `shared` block is absent from a paired listing, and block counts reflect the visible subset | unit |
| 8 | Mixed-authority block | `install_state` visibility derivation | A block citing one owner and one paired Source is labeled `owner` and never partially rendered | unit |
| 9 | Web reads | `/api/memory/*` route dependency | Every memory route resolves the requester tier and applies the same filter as the tool path | integration |
| 10 | Owner pending review | `memory_promote` review path | Pending results retain speaker, principal, trust and tier metadata | unit |
| 11 | Cache coherence | `MemoryBM25Index` persisted rows | A relabeled block changes what the next query returns without an index rebuild | unit |
| 12 | Fail-closed | tier resolution helper | An unresolvable or unknown requester tier reads as `owner`-only-denied, never as owner | unit |

**Migration impact.** None required. Unlabeled blocks read as `owner`, so an
existing workspace is unchanged and stays owner-only. Labels appear as blocks
are rewritten. No history-scanning migration code, consistent with product
decision 3.

**Not in this design.** Per-speaker read scoping, redaction inside a block, and
a hold queue for over-tier reads. Each needs its own design.

## Appendix: Implementation Status

The tier enum, the constant-table gate, fail-closed denial, structured decision
records, subagent inheritance, pairing with 8-character codes, local-owner-only
approval, stable-ID matching, display-name sanitization, the `pending` /
`trusted` split, and both context-exclusion mechanisms above are implemented.

| Item | Status | Note |
|---|---|---|
| Hold queue for over-tier requests | Not implemented | A paired sender's over-tier request (for example "restart the service") queues for one-shot owner approval instead of flat denial. Parameters to adopt: a message cap, an expiry timeout, and re-evaluation of the queue when a sender's tier changes. |
| Read-time filtering by requester tier | Designed, not implemented | The policy above fixes visibility for Core, Topics, the explicit memory tools, the retrieval indexes and the web reads as one decision. Write-time attribution is in place; the filter is not. |
| Backfill of pre-marked sources | Implemented, not yet run on live history | `openprogram memory backfill` runs the writer over every trusted source no Topic cites, ignores node markers, excludes pending sources, and promotes a legacy root `core.md` into `topics/core.md` rather than discarding it. It is idempotent against existing citations and resumable after a failed batch. |
| Queryable writer status | Implemented | Latest success time, `last_outcome`, and a failure classified into the closed `MemoryWriteFailureCode` taxonomy with its retryable verdict, plus a pending-turn count, under one versioned schema shared by the status file, CLI, tool, API and web UI. |
| Bounded archive policy for unpaired group traffic | Implemented | Unpaired group archival has a per-hour message limit, per-message and per-identity size limits, and a total archive byte ceiling. The reservation runs inside `workspace_write_lock` with the append it authorizes, so concurrent processes cannot both claim the last slot. |
