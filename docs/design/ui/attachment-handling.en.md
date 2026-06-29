# Attachment Handling Design (Web Chat)

A unified design that combines the approaches of Claude Code / opencode / openclaw with OpenProgram's own constraints.
The current baseline is committed at `c29ef3dd` (image→vision, document→absolute-path reference, agent reads on demand); this document is its next step of evolution.

## One-Sentence Principle

**materialize once to a path; deliver the best block the active model accepts plus a small head preview; let the agent page the rest with its bounded tools.**

Attachment bytes hit disk at most once and are identified by **a single absolute path**; **how** their content reaches the model is recomputed every turn based on `(file kind × the input modalities the current model declares)`, degrading step by step through `native block → ≤4KB head preview → path + agent paged read`. **The prompt cost per file is O(1), independent of file size.** The same upload works on codex/gpt-5.5 today, and when you later switch to a PDF-native Claude/Gemini it just takes effect, with **zero frontend changes**.

Three layers of judgment (the first two are baseline intuition, the third is new):
1. Is it an image? → vision block.
2. Is there an existing local path? Upload/remote channel = no → write to disk; `@`-mention/typed path = yes → reference in place.
3. Capability overlay: only when the model declares support for `document` do we upgrade the PDF's delivery to a native document block.

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
- **PDF native document block**: the preferred path for Claude Code/opencode/openclaw. OpenProgram's capability overlay makes this path **take effect automatically** when a doc-capable model is configured — this is the heart of "borrowing the best": wiring in their best path without forcing it.
- **Path + paged tool read**: everyone does this when the agent explores files **mid-task on its own**. OpenProgram routes **user attachments** through this path on codex too, because codex can't accept a document block — but with P0's head preview, the reliability gap is closed.
- **Write to a managed directory**: openclaw's claim-check (inbound has only bytes, no path). We use a per-session git workdir rather than a global one + TTL, which suits agentic better (it is the agent's cwd, committed to git every turn, replayable).
- **Rejected approach**: opencode/P3's "synthesize a fake read() tool_use+tool_result at submit time to stuff the content in" — rejected, because (a) mirroring the real read/pdf tool caps drifts, (b) once you switch to a native block it becomes dead weight, and (c) it adds sync latency at submit time. Instead we use a passive `<attachment-preview>` content snippet that gives the model a constant-cost first glance.

## Large-File Guarantee (no-context-blowup invariant)

What the backend can possibly stuff into the prompt is **only**: (a) one image block, (b) a one-time ≤4KB head preview (first turn only), (c) an ~90-byte path mention, or (d) a native doc block double-gated by "model capability + size≤10MB". Everything else enters the context page by page only through the agent's own **bounded paging tools**.

Measured caps (verified against source): the `pdf` tool is 80KB of characters per call (offset/limit by page); the `read` tool is 2000 lines per call, with a 200KB result cap; `file_search.py`'s 256KB only feeds the preview, never the delivery.

Drag in ten 30MB PDFs at once: that one turn is about `10×(90B mention + 4KB preview) ≈ 41KB`, and zero afterward — **independent of size**. A 500-page PDF on codex: written to disk once, the mention carries "500 pages", the preview = page-1 text + the first line of each page as an outline (truncated to about 50 entries, then "…(450 more pages)"); at attach time the prompt cost is ≤4KB+90B, the 8MB body never enters the context; the agent uses a `pdf(offset=N,limit=20)` window and **jumps directly** to the relevant page range via the outline, rather than scanning sequentially.

## Storage / Dedup / Security / Lifecycle

- **Location**: per-session `<state_dir>/sessions/<id>/workdir/attachments/<safe-name>` (unchanged). This is the agent's cwd, committed to git every turn — attachments become part of the session's replayable state. A global media store would break both of these invariants.
- **Who writes to disk**: only path-less sources (browser upload, remote channel). `@`-mention/typed path is already on disk; reference in place, zero copy.
- **Naming**: keep the baseline `_safe_attach_name()` — `os.path.basename` + replace non-`alnum._- space` with `_`, 120-char cap, never empty. Human-readable, so the agent's intuition about `./attachments/spec.pdf` holds. No sha-prefixed names.
- **Dedup (new)**: sha256 the decoded bytes before writing, and maintain `attachments/.opdedup.json {sha256: relative name}`. On a hit, re-stat+hash to confirm it's the same file, then **reuse** it instead of writing a duplicate. Idempotent: re-dragging the same paper, or retrying a turn, is a no-op. **Fixes a baseline bug**: the current `-N` no-clobber loop has no byte comparison for same-named files, so re-dragging an identical file produces a second copy. Dedup is within-session only (the workdir is an isolated git repo; no cross-session dedup). The index is best-effort: losing/corrupting it only writes one extra copy (harmless) and never mis-maps (it always verifies before reuse).
- **Over-limit (new)**: a hard cap of `MAX_ATTACH_BYTES=32MB`/file, checked **both** before `write_bytes` **and** at WS intake (before the base64 crosses the socket). Over the limit: skip saving, rewrite the mention to "— too large (>32MB), not stored", **tell the model**, never hand it a dead path. Images: 5MB/≤2000px (downsample first). Aggregate cap of 64MB per turn. Note that b64 inflates ~1.33×.
- **Security/escape** (verified against source): upload/remote carry no source path at all (sandbox) + basename sanitization → structurally impossible to escape; `@`/typed path goes through `/api/file-resolve`'s `(cwd/path).resolve()` + `is_relative_to(cwd)` → out-of-bounds 400. `.resolve()` fully resolves symlinks, so "a symlink inside the root pointing outside the root" is **already** rejected — the symlink gap P1/P4 worried about does not exist in the current code.
- **GC**: attachments are already committed to git, and deleting one would break replay — so GC is session-level lazy reclamation: delete the session → `rm -rf workdir` takes the attachments with it. No web-path TTL. On session load, clean up dedup-index entries whose target has gone missing. openclaw's 2-minute inbound TTL applies only to the staging area before a future remote channel writes to disk.

## Display Layer

- **chip**: parse `[attachment: name (type, KB[, P pages|L lines]) @ /abs]` → file name + type badge + size + **a new scope badge** ("500 pages"/"200K lines"); strip the `@ /abs` suffix on display. The `<attachment-preview>…</…>` snippet is stripped from the bubble like a mention — the user sees the chip, not the 4KB head.
- **delivery-mode sub-label** (UX honesty): derive "read on demand"/"sent inline"/"previewed first N lines" from `delivery_mode`, so the user knows exactly what the model actually got and doesn't have to guess "did it see my file".
- **optimistic-bubble timing** (critical): the frontend **never knows** the post-disk absolute path (`@/abs` is appended by `_persist_doc_attachments` after WS message handling). So the optimistic bubble's chip must render from **client-side data** (file.name/size/type/b64), not the mention text; on reload it re-parses the mention from the final stored text. Both must render into the same chip, with the reconciliation key = the client-computed sha8. Therefore the `[attachment: name (type, KB)]` the frontend sends is **intentionally path-less**, and the chip parser must **render the chip for both forms: path-less (in flight) and path-bearing (after rewrite)**.
- **preview popup**: decode the full file locally, never send it. The HUMAN client scrolls the whole file, the MODEL only saw the 4KB head — that's the payoff.
- **sidebar title**: `_title_from_text` strips mentions before its 50-char truncation (unchanged); new: also strip `<attachment-preview>`.

## Diff Against Baseline c29ef3dd

**Already done in the baseline (verified against source; do not rebuild)**: upload bytes written to disk under workdir/attachments; `_safe_attach_name` sanitization + 120 cap; `-N` no-clobber; `[attachment: name (type, KB) @ /abs]` mention + backend-appended @path; first-turn workdir-race fallback; image→ImageContent; `@`/typed path zero-copy + file-resolve escape check (including symlink); `_title_from_text` strips mentions before truncation; `user_msg["extra"]` attachment manifest; documents stripped from req.attachments before entering the dispatcher; validate only inspects user messages.

**Net new**:
1. `providers/types.py` Model.input Literal: add `"document"`.
2. `validate_modalities.py` `_MODALITY_TYPES`: add `"document"`.
3. A new pure function `choose_delivery(file_kind, size, model) → "native_image"|"native_document"|"path_preview"|"path_only"`.
4. dispatcher ~1888: replace the image-only loop with a per-attachment switch driven by `choose_delivery`.
5. `_persist_doc_attachments` adds: 32MB cap + "too large" rewrite; sha256 within-session dedup; page/line count **injected into the parenthesized group captured by the rewrite regex** (not appended outside the parentheses, preserving the single-token invariant + keeping the various strip regexes matching); a one-time `<attachment-preview>` (≤4KB head, truncated by bytes first then lines; binary → bash hint, no preview); image-on-incapable-model store+analyze hint.
6. `handle_chat` intake: add the 32MB/64MB caps.
7. `handle_chat` ~307: make "strip documents before entering the dispatcher" **conditional on capability** (keep them when the model supports document, so the downstream can build the native block).
8. `/api/file-resolve` returns: add page/line count + truncated head, so the `@`-mention preview and the upload preview are consistent (no symlink change needed).
9. `user-attachments.tsx`: parse count into a scope badge; strip `<attachment-preview>`; add the delivery-mode sub-label; render the chip for both path-less and path-bearing forms.
10. `use-composer-attachments.ts`/`file-tiles.tsx`: per-chip status/error badge + "counting…" optimistic placeholder + "already attached" dedup feedback (key = client sha8); popup "the model previewed the first N lines".
11. `_title_from_text`: extend strip to also remove `<attachment-preview>`.

**Stays identical on the wire**: the `[attachment: … @ /abs]` token (with count optionally added only inside the parentheses). codex/gpt-5.5 behavior is byte-for-byte the same as today, **plus** the head preview + count; the native document branch stays dormant until a doc-capable model is configured.

## Phased Plan

**P0 — Build now (end-to-end usable for the default codex/gpt-5.5; all net-new relative to the baseline)**
- `_persist_doc_attachments`: 32MB cap + "too large"; sha256 within-session dedup; page/line count injected into the parenthesized group; a one-time `<attachment-preview>` (≤4KB, bytes first then lines; binary → bash hint); image-on-incapable store+analyze.
- `handle_chat`: WS intake 32MB/64MB caps.
- `choose_delivery()` pure function + wired into the dispatcher switch (native_image unchanged; path_preview/path_only are the live branches; native_document is a guarded stub that degrades to path_preview).
- `types.py` + `validate_modalities.py`: add `"document"` (defines the seam; harmless on codex since no model declares it).
- `/api/file-resolve`: return count + truncated head.
- frontend: scope badge + strip `<attachment-preview>` + per-chip status/error + "counting…" optimistic + "already attached" + path-less/path-bearing chip consistency.
- `_title_from_text`: extend strip.
- **Value**: small and medium documents get a free head preview (lower latency, the model has the gist); large files keep O(1) prompt cost via path + bounded tools; over-limit is reported, not stored; dedup fixes the baseline same-byte double copy; the capability seam is in place.
- **Verification** (per the hard self-verify rule): restart the worker, curl healthz, then use the chrome MCP to walk a real WS on a fresh session: a small .txt (preview appears, chip clean), a 500-page PDF (count badge, page-1 outline, no context blowup), an over-limit file ("too large" chip), an `@`-mention (zero-copy, count+preview); confirm the sidebar title is clean and there is no 500/build overlay.

**P1 — Deferred (requires a doc-capable model configured; the seam is built in P0)**
- Each provider's native document block builder (the Anthropic document / Gemini inline_data application/pdf wire format), hung after `choose_delivery=="native_document"` + the size guard.
- Make `chat.py:307`'s "strip documents before entering the dispatcher" conditional on capability.
- Reason for deferral: no configured model today has document in its input, so it can't be tested end-to-end against the real default; and it needs a per-provider request builder + verification of the native size cap.

**P2 — Deferred (requires remote channels to ship)**
- openclaw-style remote inbound staging directory + 2-minute TTL GC + claim-check + `media://` indirection + channel adapters (discord download_attachment / wechat) wiring inbound bytes into the same `_persist` save call. The save+mention+preview representation can already accommodate "bytes only" sources; all that's missing is the inbound pipeline + staging lifecycle.
- Also deferred: a page-image fallback for scanned / image-only PDFs; xlsx/docx structured extraction (when binary → path); chunked/resumable uploads near 32MB + raising the WS max frame; a per-session attachment-quota hint UI; cross-session global dedup (intentionally never done — the per-session-workdir invariant).

## Open Questions (do not block P0)

Two tunable constants, both with defensible defaults, both a single config knob rather than an architectural fork:
1. `PREVIEW_CAP` (suggested 4KB / ~60 lines). Too low gives just-over-the-limit small documents an extra read round-trip; too high leaks a bit more body on every attach. Start at 4KB, tunable.
2. `MAX_ATTACH_BYTES` (suggested 32MB). Curbing git-workdir blob bloat (a blob committed to git is permanent in history — a real cost) vs. accommodating larger real PDFs. Start at 32MB, tunable.

The only genuinely product-facing question, deferrable until it actually fires: whether the permanent accumulation of large binaries in per-session git history (the cost of the "workdir = self-contained committed state" invariant) is acceptable, or whether a content store outside git is needed in the future — but that would sacrifice replay reproducibility, so the current design **intentionally keeps** this invariant, and it is not a P0 open item.
