# Attachment Handling Design (Web Chat)

How a file the user attaches in the web chat reaches the model: where its bytes
live, which content block carries it, and how the agent reads the rest.

## One-Sentence Principle

**materialize once to a path; deliver the best block the active model accepts plus a small head preview; let the agent page the rest with its bounded tools.**

Attachment bytes hit disk at most once and are identified by **a single absolute path**; **how** their content reaches the model is recomputed every turn based on `(file kind × the input modalities the current model declares)`, degrading step by step through `native block → ≤4KB head preview → path + agent paged read`. **The prompt cost per file is O(1), independent of file size.** The same upload works on codex/gpt-5.5 today, and when you later switch to a PDF-native Claude/Gemini it just takes effect, with **zero frontend changes**.

Three layers of judgment:
1. Is it an image? → vision block.
2. Is there an existing local path? Upload/remote channel = no → write to disk; `@`-mention/typed path = yes → reference in place.
3. Capability overlay: a PDF is upgraded to a native document block only when the model declares support for `document`.

## Decision Matrix (authoritative; plain-text aligned columns, not a markdown table)

`DELIVER (now)` is based on the default codex/gpt-5.5: `model.input=["text","image"]`, no `document`.
A row's delivery method flips **only** when `model.input` declares the corresponding modality.

```
source        file kind     write to disk?            DELIVER(now, codex/gpt-5.5)                  READ path
------------  ------------  ------------------------  -------------------------------------------  ----------------------------
upload        image         no (in-memory→b64 direct) ImageContent block (pixels)                  model vision native
upload        text/code     yes attachments/<safe>    [attachment:..@/abs] + ≤4KB head preview     read tool 2000 lines/200KB paging
upload        pdf           yes attachments/<safe>    [attachment:..(P pages)@/abs] + page1 head+outline   pdf tool 80KB/page window
upload        other binary  yes attachments/<safe>    [attachment:..@/abs] mention only (no preview)       bash file/strings/xxd
@-mention     image         no (re-read+b64)          ImageContent block                           model vision native
@-mention     text/code     no (already on disk)      [attachment:..@/abs] + ≤4KB head             read paging
@-mention     pdf           no (already on disk)      [attachment:..(P pages)@/abs] + page1 head    pdf paging
@-mention     other binary  no (already on disk)      [attachment:..@/abs] mention only            bash
typed path    any           = @-mention               file-resolve treats a bare path identically by its kind
remote channel image         yes attachments/<safe>    ImageContent (re-read from on-disk bytes)    model vision native
remote channel text/pdf      yes attachments/<safe>    [attachment:..@/abs] + head preview (same as upload)  read/pdf paging
remote channel other binary  yes attachments/<safe>    [attachment:..@/abs] mention only            bash
```

**Cells that flip on more capable models (single rule, any source):**

```
pdf, model.input contains "document", size ≤ NATIVE_DOC_INLINE_CAP(10MB and the provider's page-count cap)
    → DELIVER becomes a native document content block (whole file base64, built by reading from the on-disk path);
      the [attachment:..@/abs] mention is kept (drives the chip + lets the agent still read another slice);
      the head preview is suppressed (the model already has the whole file).
pdf, contains "document" but size > NATIVE_DOC_INLINE_CAP
    → stays in the "now" column (path + head preview); no native block is built (avoid blowing up the context).
image, model.input does not contain "image" (a degraded codex config)
    → store the png + [attachment:..@/abs — view with image_analyze]
      (fixes the bug at providers/_shared/openai_responses.py:120-121 where input_image is silently dropped when image is not in model.input).
```

**Axis discipline**: the `source` axis only decides **where the bytes land** (write to disk vs. reference in place); the `(file kind × capability)` pair is the **only** thing that decides DELIVER.

## Relationship to Claude Code/opencode/openclaw

- **Images go through vision**: all three plus us agree.
- **PDF native document block**: the preferred path for Claude Code/opencode/openclaw. OpenProgram's capability overlay makes this path take effect automatically when a doc-capable model is configured, without making it a requirement.
- **Path + paged tool read**: everyone does this when the agent explores files **mid-task on its own**. OpenProgram routes **user attachments** through this path on codex too, because codex cannot accept a document block; the head preview closes the reliability gap.
- **Write to a managed directory**: openclaw's claim-check (inbound has only bytes, no path). We use a per-session git workdir rather than a global one + TTL, which suits agentic better (it is the agent's cwd, committed to git every turn, replayable).
- **Rejected approach**: injecting the file body at submit time by replaying the read (opencode's approach) is not used, because (a) mirroring the real read/pdf tool caps drifts, (b) once you switch to a native block it becomes dead weight, and (c) it adds sync latency at submit time. Instead we use a passive `<attachment-preview>` content snippet that gives the model a constant-cost first glance. (What opencode actually injects is **two plain-text parts flagged `synthetic`** — a sentence "Called the Read tool with the following input …" plus the real read result — which reach the model as `role: "user"` text, not `tool_use` / `tool_result` content blocks. The three reasons above are unaffected by the correction.)

## Large-File Guarantee (no-context-blowup invariant)

What the backend can possibly stuff into the prompt is **only**: (a) one image block, (b) a one-time ≤4KB head preview (first turn only), (c) an ~90-byte path mention, or (d) a native doc block double-gated by "model capability + size≤10MB". Everything else enters the context page by page only through the agent's own **bounded paging tools**.

Measured caps: the `pdf` tool is 80KB of characters per call (offset/limit by page); the `read` tool is 2000 lines per call, with a 200KB result cap; `file_search.py`'s 256KB only feeds the preview, never the delivery.

Drag in ten 30MB PDFs at once: that one turn is about `10×(90B mention + 4KB preview) ≈ 41KB`, and zero afterward — **independent of size**. A 500-page PDF on codex: written to disk once, the mention carries "500 pages", the preview = page-1 text + the first line of each page as an outline (truncated to about 50 entries, then "…(450 more pages)"); at attach time the prompt cost is ≤4KB+90B, the 8MB body never enters the context; the agent uses a `pdf(offset=N,limit=20)` window and **jumps directly** to the relevant page range via the outline, rather than scanning sequentially.

## Storage / Dedup / Security / Lifecycle

- **Location**: per-session `<state_dir>/sessions/<id>/workdir/attachments/<safe-name>`. This is the agent's cwd, committed to git every turn — attachments become part of the session's replayable state. A global media store would break both of these invariants.
- **Who writes to disk**: only path-less sources (browser upload, remote channel). `@`-mention/typed path is already on disk; reference in place, zero copy.
- **Naming**: `_safe_attach_name()` — `os.path.basename` + replace non-`alnum._- space` with `_`, 120-char cap, never empty. Human-readable, so the agent's intuition about `./attachments/spec.pdf` holds. No sha-prefixed names.
- **Dedup**: sha256 the decoded bytes before writing, and maintain `attachments/.opdedup.json {sha256: relative name}`. On a hit, re-stat+hash to confirm it's the same file, then **reuse** it instead of writing a duplicate. Idempotent: re-dragging the same paper, or retrying a turn, is a no-op. A plain `-N` no-clobber loop cannot do this — with no byte comparison, re-dragging an identical file yields a second copy. Dedup is within-session only (the workdir is an isolated git repo; no cross-session dedup). The index is best-effort: losing/corrupting it only writes one extra copy (harmless) and never mis-maps (it always verifies before reuse).
- **Over-limit**: a hard cap of `MAX_ATTACH_BYTES=32MB`/file, checked **both** before `write_bytes` **and** at WS intake (before the base64 crosses the socket). Over the limit: skip saving, rewrite the mention to "— too large (>32MB), not stored", **tell the model**, never hand it a dead path. Images: 5MB/≤2000px (downsample first). Aggregate cap of 64MB per turn. Note that b64 inflates ~1.33×.
- **Security/escape**: upload/remote carry no source path at all (sandbox) + basename sanitization → structurally impossible to escape; `@`/typed path goes through `/api/file-resolve`'s `(cwd/path).resolve()` + `is_relative_to(cwd)` → out-of-bounds 400. `.resolve()` fully resolves symlinks, so a symlink inside the root pointing outside the root is rejected as well.
- **GC**: attachments are already committed to git, and deleting one would break replay — so GC is session-level lazy reclamation: delete the session → `rm -rf workdir` takes the attachments with it. No web-path TTL. On session load, clean up dedup-index entries whose target has gone missing. openclaw's 2-minute inbound TTL applies only to the staging area before a future remote channel writes to disk.

## Display Layer

- **Chip**: parse `[attachment: name (type, KB[, P pages|L lines]) @ /abs]` → file name + type badge + size + a scope badge ("500 pages"/"200K lines"); strip the `@ /abs` suffix on display but KEEP the captured path — it is what the chip opens, through `GET /api/file-raw` / `GET /api/file-read`. Images render the thumbnail in place of the file glyph. The `<attachment-preview>…</…>` snippet is stripped from the bubble like a mention — the user sees the chip, not the 4KB head.
- **Delivery-mode sub-label** (UX honesty): derive "read on demand"/"sent inline"/"previewed first N lines" from `delivery_mode`, so the user knows exactly what the model actually got and doesn't have to guess "did it see my file".
- **Optimistic-bubble timing**: the frontend cannot know the post-disk absolute path when it composes (`@/abs` is appended by `_persist_attachments` during WS message handling), so the `[attachment: name (type, KB)]` it sends is **intentionally path-less** and the chip parser renders **both forms: path-less (in flight) and path-bearing (after rewrite)** — a path-less chip is a label, a path-bearing one opens. The gap closes in the same turn rather than at the next reload: `chat_ack` echoes the STORED text and the local user turn is built from that, so the chip is clickable the moment the ack lands.
- **Preview popup**: decode the full file locally, never send it. The HUMAN client scrolls the whole file, the MODEL only saw the 4KB head — that's the payoff.
- **Sidebar title**: `_title_from_text` strips both mentions and `<attachment-preview>` before its 50-char truncation.

## Appendix: Implementation Status

Implemented: bytes written to disk under `workdir/attachments` with
`_safe_attach_name` sanitization and no-clobber naming; the
`[attachment: name (type, KB) @ /abs]` mention with the backend-appended path;
first-turn workdir-race fallback; image → ImageContent **and** a saved path +
mention, so the human sees what the model sees; `@`-mention and typed paths
zero-copy with the file-resolve escape check; `_title_from_text` stripping
mentions before truncation; the `user_msg["extra"]` attachment manifest; the
size caps (32MB per file, 64MB per turn) at both `write_bytes` and WS intake
with the "too large" mention rewrite; sha256 within-session dedup and
`attachments/.opdedup.json`; page/line counts inside the mention's parenthesized
group and the one-time `<attachment-preview>` head snippet.

Also implemented since, and shared with
[chat-attachments](chat-attachments.html): one marker formatter/parser in
`openprogram/attachments.py` that inbound channel attachments and the agent's
outbound `send_file` both go through, `GET /api/file-raw` as the byte exit for
an absolute path, and the chat's clickable chip + preview overlay.

Designed and not yet landed:

- the `"document"` modality in `providers/types.py` `Model.input` and in
  `validate_modalities.py`, and the `choose_delivery()` switch in the dispatcher;
- per-provider native document block builders, which need a doc-capable model
  configured before they can be exercised;
- page/line count and truncated head in the `/api/file-resolve` response;
- the delivery-mode sub-label and per-chip status/error badges.

## Tunable Constants

Two tunable constants, both with defensible defaults, both a single config knob rather than an architectural fork:
1. `PREVIEW_CAP` (suggested 4KB / ~60 lines). Too low gives just-over-the-limit small documents an extra read round-trip; too high leaks a bit more body on every attach. Default 4KB.
2. `MAX_ATTACH_BYTES` (suggested 32MB). Curbing git-workdir blob bloat (a blob committed to git is permanent in history — a real cost) vs. accommodating larger real PDFs. Default 32MB.

One product-facing question stays open: whether the permanent accumulation of large binaries in per-session git history — the cost of the "workdir = self-contained committed state" invariant — is acceptable, or whether a content store outside git is eventually needed. Such a store would sacrifice replay reproducibility, so the design intentionally keeps the invariant.
