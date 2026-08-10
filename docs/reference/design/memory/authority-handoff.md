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

## Memory writer and trust semantics (settled 2026-08-10, second round)

1. **Writer model**: the auto-organize writer stops hardcoding
   ClaudeCodeAgent and follows the default chat agent (provider, model,
   credentials). New setting `memory.writer.model`, default = the default
   agent, editable in the web settings UI. Do not default to a cheap model:
   weak models misread organizing instructions; users downgrade explicitly
   if they want to save.
2. **Paired speech is trusted.** Content from paired accounts enters the
   normal distillation flow exactly like the owner's, attributed by
   speaker. Pairing is the trust decision; no second gate inside.
   `pending` + `memory_promote` apply only to unpaired group members'
   archived speech (they cannot converse, but group archives still carry
   their lines).
3. **Scope-batch naming**: the ~430-line uncommitted scope work must be
   re-keyed from shared-channel/three-tier vocabulary to paired/unpaired
   before commit.
4. **Execution order for the memory stabilization batch**: mask keys in
   diagnostics output (bug, fix immediately) → non-retryable auth/config
   errors (stop the 5-min failure loop) → default-agent writer → one real
   distillation on the live workspace as acceptance (22 sources / 0 topics
   / core at 188% budget) → scope batch (after renaming) → end-to-end test
   → complete `memory.backend=none` teardown (no prompt injection, no
   recall, no writes, no organizer, no worker thread).

## Deferred (second batch, after the above lands)

- **Hold queue**: a paired sender's over-tier request (e.g. "restart the
  service") queues for one-shot owner approval instead of flat denial.
  Parameters to copy from Claude Code cross-session messaging: 100-message
  cap, expiry timeout, re-evaluate queue on tier change.
- **Tool-vs-data split**: read-scoping of memory by requester tier
  (write-time stamping is already in place; read-time filtering per
  LangGraph's owner-filter pattern).

## Review follow-up queue (2026-08-10)

- **D2 — deferred:** unpaired group-message archival has no independent
  frequency or storage cap. Add a bounded archive policy before deployments
  where unpaired group traffic is not externally rate-limited.
- **D3 — resolved in the authority batch:** both channel design documents now
  describe pairing-only admission, 8-character codes, and local owner
  approval interfaces.
- **D4 — deferred:** remove the no-op residue at
  `openprogram/functions/tools/memory/memory.py:151` in the memory cleanup
  batch.
- **Memory batch:** isolate staging directories used by
  `test_memory_routes.py::test_stage_directories_are_cleaned_up_on_both_paths`;
  the current shared location can fail intermittently under `pytest -n auto`.

## Sequencing

Unchanged relative to existing plans: this redesign slots where the
"authority follow-ups" sat. Remote-web-access doc work is independent and
proceeds in parallel; 05B and the rest of the sandbox series are unaffected.
