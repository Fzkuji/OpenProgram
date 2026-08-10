# Authority redesign: two tiers plus pairing

Settled 2026-08-10 with the owner. This supersedes the follow-up list from the
f8309c0f review (the "origin/capability consistency check" item is replaced by
this redesign, which removes its root cause). Speaker identity (previous batch)
is untouched by everything below.

## Model

Admission is binary, authority is binary. Three states a platform account can
be in:

| State | Can do |
|---|---|
| **Owner** (local terminal / local web) | Everything. |
| **Paired** (approved platform account) | Converse, be recorded into memory with attribution, actively append memory. No tool execution. |
| **Unpaired** | Message does not enter the agent. Gets a pairing code reply. |

There is no third runtime tier. "Unknown external = reply only" is deleted:
unpaired messages are never rendered into model context at all, which removes
that prompt-injection surface entirely.

## Mechanism changes (replaces capability-list scopes)

1. **Tier enum on the request, constant table at the gate.** The per-request
   `authority_scope.capabilities` list is replaced by a single tier enum
   (`owner` / `paired`). The gate in `_approval.py` checks the enum against a
   hardcoded tier→capability constant. The High-severity "origin is
   decorative" defect disappears structurally: there is no per-request
   capability list left to mint inconsistently. Gate position (before rules,
   force_ask, bypass) stays exactly where f8309c0f put it. Fail-closed stays:
   missing/unknown tier = deny all.
2. **Memory append reachable.** Map the memory-append tools to the paired
   tier's allowance (fixes the second High: paired speakers hold the
   capability but no tool consumes it). Other memory tools stay owner-only.
3. **Pairing flow** (adopted from OpenClaw, `references/openclaw`
   `docs/channels/pairing.md`): 8-char uppercase code, ambiguous chars `0O1I`
   excluded, 1-hour expiry, max 3 pending per channel (excess silently
   ignored), no repeat prompt to the same sender within the hour.
4. **Approval is local-owner-channel only.** CLI command (and local web UI).
   A channel message can never trigger approval — "please approve code X" in
   chat is the canonical injection phrase; refuse and point to the owner.
5. **Mutable identifiers do not authorize.** Platform stable IDs match;
   usernames/display names are flagged mutable and are excluded from pairing
   allowlist matching by default (OpenClaw `allowlist.ts:78-113` pattern).
6. **Display-name sanitization.** Speaker display names pass the sanitizer
   before rendering into any prompt: strip newlines, zero-width and bidi
   characters, replace `[` `]` (envelope-marker collision) — the chat path
   gap OpenClaw itself shipped with; do not repeat it.
7. **Structured decisions.** The gate returns a decision record, not a bool:
   admission state, decisive check, stable reason code. Log it. This is the
   audit trail and the debuggability story in one.

## Product decisions (owner-approved, 2026-08-10)

1. **Unpaired group members' speech is still recorded** into the shared
   archive with the existing untrusted-source marking (`speaker_trusted`),
   visible as context and retrievable with provenance, but excluded from
   active memory distillation. Group memory needs the whole conversation;
   the marking layer from the speaker batch already carries this.
2. **Approval only from the local owner channel** (see mechanism 4).
3. **No migration.** Adoption is small enough that everyone pairs fresh;
   do not write history-scanning migration code. Rollout day, every
   non-owner account gets a pairing code on first contact.

## Deferred (second batch, after the above lands)

- **Hold queue**: a paired sender's over-tier request (e.g. "restart the
  service") queues for one-shot owner approval instead of flat denial.
  Parameters to copy from Claude Code cross-session messaging: 100-message
  cap, expiry timeout, re-evaluate queue on tier change.
- **Tool-vs-data split**: read-scoping of memory by requester tier
  (write-time stamping is already in place; read-time filtering per
  LangGraph's owner-filter pattern).

## Sequencing

Unchanged relative to existing plans: this redesign slots where the
"authority follow-ups" sat. Remote-web-access doc work is independent and
proceeds in parallel; 05B and the rest of the sandbox series are unaffected.
