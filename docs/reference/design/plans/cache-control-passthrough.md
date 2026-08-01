# Cache-Control Passthrough — Design

> How a caller-placed `cache_control` mark on a content block reaches the
> Anthropic Messages API request body unchanged, so the prompt cache breakpoint
> lands where the caller wants it rather than where the provider guesses.

## 1. The problem

A caller such as the screenspot locator in GUI-Agent-Harness knows which part of
its prompt is stable: a long fixed rule block, followed by dynamic text and a
screenshot. It wants the cache breakpoint right after that stable prefix.

Without passthrough, a `cache_control` key written into a content dict is
dropped inside OpenProgram, and the only breakpoint that survives is the one the
provider adds automatically on the last block. The last block is the image or
the dynamic text, which differs on every request, so the cache never hits.

Scope: **anthropic**-class providers only — the native Anthropic API, the Claude
Code subscription through a proxy, and any anthropic-messages interface. The
OpenAI and codex classes use automatic prefix caching and never read
`cache_control`; the field is inert for them.

## 2. The passthrough path

`cache_control` is an optional field carried unchanged across three layers.

**Content types** (`openprogram/providers/types.py`). `TextContent` and
`ImageContent` each carry `cache_control: dict | None = None`. Video and audio
do not — nothing marks them today. The value is an opaque dict such as
`{"type": "ephemeral"}`, possibly with a `ttl`. OpenProgram never parses or
validates its contents; whatever the caller writes is what Anthropic receives.

**Context building** (`openprogram/agentic_programming/runtime.py`,
`_build_pi_context`). When the caller's `content: list[dict]` is converted into
`TextContent` / `ImageContent` objects, each block's `cache_control` is copied
onto the object. The `role == "system"` text block is an exception: it is
extracted into `system_text` and does not carry a per-block breakpoint, because
system breakpoints are placed separately by the Anthropic provider's
`_build_system`.

**Wire building** (`openprogram/providers/anthropic/anthropic.py`,
`_build_messages`). When API blocks are reconstructed from the content objects,
a block whose object carries `cache_control` gets the field written into the
generated dict. This applies to the list-content branch of `UserMessage`; the
string-content branch, `AssistantMessage`, and `ToolResultMessage` have no
per-block caller marks and are unaffected.

A call that passes no `cache_control` produces a request body identical to one
built before the field existed. Every existing caller is unaffected.

## 3. Automatic breakpoint versus caller breakpoint

The provider still places a breakpoint on the last block of a message on its
own. The rule that reconciles the two:

> If any block in a message carries a caller-placed `cache_control`, the
> provider does not auto-place a breakpoint on that message's last block at all.

The check is `caller_marked = any("cache_control" in b for b in
content_blocks)`. This is stronger than merely declining to overwrite the last
block. When the caller marks a stable prefix early in the message, an automatic
breakpoint on the dynamic tail block would waste one of the four available slots
and pin the cache boundary past the very content that changes every request.
Suppressing it entirely keeps the caller's intent as the only breakpoint in that
message.

## 4. Boundaries the caller must respect

**Minimum cacheable prefix.** A breakpoint only caches when the content before
it is at least 1024 tokens (2048 for Haiku). Below that, Anthropic **silently
ignores it** — no error, no hit. A caller that marks a short prefix sees
`cache_read` stay at 0 and will look for a bug in the passthrough that is not
there.

**Four breakpoints per request.** OpenProgram already adds roughly two on its
own (the system block and the last block). Exceeding four makes Anthropic return
400. A caller has roughly two slots to work with.

**Proxy fidelity.** When the Claude Code subscription goes through the Meridian
proxy, a proxy that strips `cache_control` from the body keeps `cache_read` at 0
regardless of what OpenProgram sends. This is a proxy-layer property and has to
be verified separately; sending the same fixed prefix twice and checking that
the second response reports `cache_read > 0` tests OpenProgram and the proxy
together.

**Other providers.** OpenAI and codex block construction reads fields
individually (`.text` / `.data` in `openai_completions` and
`_shared/transform_messages`), and the two `model_dump()` calls in the responses
and codex paths dump the options object rather than a content block, so the new
optional field never leaks into their request bodies. `TextContent.model_dump()`
round-trips cleanly, so persistence is unaffected.

## 5. Caller-side work, not OpenProgram's

Splitting a prompt so its fixed rules form the first text block and marking that
block is a caller change — for screenspot, in
`GUI-Agent-Harness/screenspot_locator.py`. Prefix-caching optimization for the
OpenAI and codex classes needs only that the caller put its stable prefix first,
with no OpenProgram change at all.

## Appendix: Implementation Status

Implemented across `providers/types.py`, `runtime._build_pi_context`, and
`anthropic._build_messages`, with the auto-breakpoint suppression rule of §3.
Covered by `tests/unit/test_cache_control_passthrough.py` (six cases):
full-chain passthrough from runtime to the Anthropic body, image passthrough,
byte-identical body when the field is absent, the automatic breakpoint still
working when there is no caller mark, no overwrite when the caller marks the
last block, and auto-breakpoint suppression when the caller marks an earlier
block. The non-Anthropic no-leak property in §4 was verified by reading the
OpenAI and codex block construction paths. Proxy passthrough is the one boundary
still unverified in practice.
