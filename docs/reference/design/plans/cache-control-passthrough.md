# Change Plan: Pass `cache_control` on content blocks through to the Anthropic API

## Goal

Allow OpenProgram callers (such as the screenspot locator in GUI-Agent-Harness) to
explicitly mark `"cache_control": {"type": "ephemeral"}` on a given content block in
`runtime.exec(content=[...])`, and ensure that mark reaches the Anthropic
Messages API request body verbatim, so that a prompt cache breakpoint is set right
after "the block the caller specified".

Current problem: the `cache_control` a caller writes into a content dict is dropped
inside OpenProgram, making it impossible to place a cache breakpoint at a custom
location. Right now only the "last block" breakpoint added automatically by the
provider takes effect, and the last block (image / dynamic text) is different on
every request, so the cache hit rate is 0.

Scope: this only applies to **anthropic**-class providers (the native Anthropic API,
the Claude Code subscription via proxy, any anthropic-messages interface). The
OpenAI / codex class uses automatic prefix caching and does not read cache_control;
this plan does not concern them.

## Constraints

- All three changes are "add an optional field + conditionally preserve it"; **when
  cache_control is not passed, behavior is exactly the same as today**, with zero
  regression for all existing callers.
- Do not touch the provider's existing "automatically place a breakpoint on the last
  block" logic (the two `is_last and cache_control` segments); just additionally let
  a breakpoint explicitly marked by the caller be preserved too.
- The value of cache_control is a dict (e.g. `{"type": "ephemeral"}`, or one with
  `ttl`), passed through verbatim; OpenProgram does not parse or validate its
  contents.

## Change list (3 in total)

### Change 1 — `openprogram/providers/types.py`: add an optional field to the data classes

Add an optional field `cache_control` to each of the `TextContent` and `ImageContent`
pydantic models, giving the cache mark a slot to live in.

`TextContent` (currently around lines 153-156):
```python
class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None
    cache_control: dict | None = None        # new
```

`ImageContent` (currently around lines 173-176):
```python
class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str  # base64 encoded
    mime_type: str  # e.g. "image/jpeg"
    cache_control: dict | None = None        # new
```

Note: only the Text and Image classes are needed (screenspot's fixed-rule prefix is
text, the image is image). Video/Audio are not needed for now and can be left alone.

### Change 2 — `openprogram/agentic_programming/runtime.py`: carry the field in `_build_pi_context`

`_build_pi_context` (currently around lines 1343-1405), when converting the caller's
`content: list[dict]` into `TextContent` / `ImageContent` objects, currently only
takes text / data / mime and drops the `cache_control` in the dict. Change it to
carry it along.

Currently (around lines 1388-1392):
```python
        if btype == "text":
            parts.append(TextContent(type="text", text=block["text"]))
        elif btype == "image":
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(type="image", data=data, mime_type=mime))
```

Change to:
```python
        if btype == "text":
            parts.append(TextContent(
                type="text",
                text=block["text"],
                cache_control=block.get("cache_control"),
            ))
        elif btype == "image":
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(
                type="image",
                data=data,
                mime_type=mime,
                cache_control=block.get("cache_control"),
            ))
```

Note: the `role == "system"` text block is split out separately into system_text at
lines 1381-1386; that branch does not involve cache breakpoints (system breakpoints
are handled separately by the anthropic provider's _build_system), so leave it
unchanged.

### Change 3 — `openprogram/providers/anthropic/anthropic.py`: preserve the field in `_build_messages`

`_build_messages` (currently around lines 304-398), when reconstructing the API
blocks sent to Anthropic from `TextContent` / `ImageContent`, currently only writes
type/text/source and drops the `cache_control` on the object again. Change it so
that if the object carries it, it is written into the generated block.

Currently (around lines 332-344, the list-content branch of UserMessage):
```python
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            content_blocks.append({"type": "text", "text": text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        })
```

Change to:
```python
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            b: dict[str, Any] = {"type": "text", "text": text}
                            if getattr(block, "cache_control", None):
                                b["cache_control"] = block.cache_control
                            content_blocks.append(b)
                    elif isinstance(block, ImageContent):
                        b = {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        }
                        if getattr(block, "cache_control", None):
                            b["cache_control"] = block.cache_control
                        content_blocks.append(b)
```

Unchanged: the `if is_last and cache_control and content_blocks:` segment
(around lines 345-346) that immediately follows — the "automatically place a
breakpoint on the last block" logic — must not be deleted. It can coexist with
"caller-explicit marking": Anthropic allows multiple cache_control breakpoints in a
single request (up to 4).

Same-name branches that do not need changing: the "content is str" branch at lines
322-328 does not involve per-block marking by the caller, leave it alone; the
AssistantMessage starting at line 350 and the ToolResultMessage starting at line 383
are left alone.

## Verification (self-check by the implementer after completion)

1. Static: for a call that does not pass cache_control, the generated request body is
   field-for-field identical to before the change (you can do a snapshot comparison
   against the request body of an existing unit test).
2. Pass-through: construct an exec with
   `content=[{"type":"text","text":"X","cache_control":{"type":"ephemeral"}}, ...]`
   and assert that the messages[...]['content'][0] ultimately sent to Anthropic carries
   `"cache_control": {"type": "ephemeral"}`.
3. Hit: send the same fixed prefix twice as real requests (through the Anthropic
   endpoint / proxy actually in use); the second response's usage should have
   `cache_read` > 0, and `cache_creation` > 0 only on the first.
   —— this step also verifies "whether the proxy in use passes the body through": if
   the proxy drops cache_control, cache_read will stay at 0, indicating the proxy
   does not pass it through and the proxy layer needs separate handling.

## Out of scope for this plan (done separately on the harness side)

- Splitting the fixed-rule segment of each screenspot prompt into "first text block +
  set cache_control", with dynamic content and the image arranged after it. This is a
  caller-side change (GUI-Agent-Harness/screenspot_locator.py), not part of
  OpenProgram.
- Prefix caching optimization for the OpenAI / codex class (only requires the harness
  to put the prefix up front, without touching OpenProgram).

## Implementation status (landed)

All three changes are implemented (commit `2f253405`), with unit tests added
(`tests/unit/test_cache_control_passthrough.py`, 6 cases):

- `types.py`: `TextContent` / `ImageContent` each gained `cache_control: dict | None = None`.
- `runtime._build_pi_context`: carries `block.get("cache_control")` onto `TextContent` /
  `ImageContent`.
- `anthropic._build_messages`: writes `cache_control` from the object into the generated
  API block.
- The auto-breakpoint's "do not override the caller" is done more robustly than the
  original plan: it uses
  `caller_marked = any("cache_control" in b for b in content_blocks)` to decide —
  **as long as any block in this message is marked with a breakpoint by the caller, it
  no longer auto-places a breakpoint on the last block at all** (not just refraining
  from overriding the last block). This way, when the caller marks a stable prefix
  block earlier in the message, it neither wastes an extra breakpoint slot nor moves
  the cache hit point to the dynamic tail block.

Test coverage: full-chain pass-through (runtime→anthropic body), image pass-through,
byte-identical body when not passed, auto-breakpoint working as usual when there is no
caller breakpoint, no override when the caller marks the last block, and auto-breakpoint
suppressed when the caller marks an earlier block.

### Verification conclusions / confirmed boundaries

- **Zero leakage for non-Anthropic providers** (verified): OpenAI/codex block
  construction reads fields one by one — `.text` / `.data` (`openai_completions:96`,
  `_shared/transform_messages`); those two `model_dump()` calls (responses/codex) dump
  the **options object**, not the content block, so the new optional field is inert for
  them; `TextContent.model_dump()` round-trips cleanly too (persistence-safe).
- **Anthropic minimum cacheable token count**: a breakpoint only really caches when its
  prefix is ≥ 1024 tokens (2048 for Haiku); otherwise it is **silently ignored — no
  error and no hit**. The caller (screenspot) must ensure the marked fixed prefix is
  long enough, otherwise `cache_read` stays at 0 even with the breakpoint added. Read
  this together with verification step 3 above.
- **At most 4 breakpoints**: OpenProgram already auto-adds ~2 (the system block + the
  last block). If the caller's own breakpoints plus these exceed 4, Anthropic returns
  400 outright. The caller has roughly only ~2 slots left.
- **Proxy pass-through** (already mentioned in the plan): when the claude-code
  subscription goes through the Meridian proxy, if the proxy swallows cache_control,
  `cache_read` will always be 0 — that is a proxy-layer matter and needs separate
  verification.
