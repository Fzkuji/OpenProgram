# Attachment handling

This is the canonical attachment contract and implementation plan for Web, Desktop, and channel input. The accompanying visualization in the documentation sidebar summarizes the decisions. [Chinese translation](attachment-handling.zh.md). Implementation status is recorded at the end; target behavior below is not a claim that the runtime already implements it.

## Verified current behavior

The Web composer reads image bytes into base64, builds a separate 192-pixel thumbnail, and permits images up to 5 MiB. It does not resize the image sent to the model. Documents have a 32 MiB client limit. The backend saves uploads, including images, through `_persist_attachments`, adds path markers and bounded text/PDF previews, and filters documents out of `TurnRequest.attachments`. Image base64 remains in that request.

`normalize_agent_turn_payload` limits the complete serialized admission envelope to 256 KiB. Consequently a 200 KiB binary image, represented as base64 with a small text request, already fails this boundary. This was reproduced without a provider call; the probe tests envelope sizing, not image decoding. A live failed chat also records `input_too_large`. The log does not establish which original attachment or field supplied the excess bytes.

Admission runs before `_append_msg`. The session and title may exist even when admission fails, leaving no user node. `sendChatMessage` reports WebSocket transmission success, and `useChatSubmit` immediately clears text and attachments. An uncorrelated command error produces a generic transient toast, not a recoverable submission record.

The backend's 32 MiB per-file and 64 MiB aggregate checks run after base64 decoding. The aggregate counter increases only for newly written files; dedup hits do not consume it. A skipped oversized image is not explicitly removed from the separate dispatch list. These are source-confirmed gaps, not live exploit tests. The OpenAI Responses converter also removes image blocks for models without image input; the current frontend does not provide a matching delivery explanation. Existing marker parsing, source-path provenance, preview paths, dedup, and file access policies remain useful.

Source locations: [Web intake and persistence](https://github.com/Fzkuji/OpenProgram/blob/main/apps/server/openprogram_server/_webui/ws_actions/chat.py), [admission and activation](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agent/production_driver.py), [composer submission](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/components/chat/composer/submit/use-chat-submit.ts), [image input](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/components/chat/composer/attach/image-attach.ts), [error handling](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/lib/net/action-error.ts), [provider conversion](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/_shared/openai_responses.py).

## Official reference corpus

| Framework / scope | Verified design | Decision for OpenProgram |
|---|---|---|
| Codex public app-server and Rust protocol | Typed text/image/local-image input; local image paths are converted during request serialization. | Separate attachment identity from provider encoding. Do not infer private desktop behavior from the public protocol. |
| Codex `attachment-store` crate | Persistence returns `AttachmentRef` with URL and optional file ID. An `InlineAttachmentStore` also exists. | Adopt durable references; do not claim every Codex attachment is offloaded, and do not copy a storage-backend abstraction when one local store suffices. |
| OpenCode V2 attachments | The server materializes supported file/data inputs before prompt admission; per-item decoded limit is 20 MiB. Image processing separately limits dimensions and encoded bytes. V2 documents text and PNG/JPEG/GIF/WebP visibility; PDF and other unsupported binaries are not model-visible through that prompt attachment path. | Adopt pre-admission validation and separate image budgets. Retain OpenProgram's PDF paging instead of reducing support to this V2 subset. |
| OpenClaw Gateway / media understanding | Managed media can be offloaded; outcomes distinguish native vision, processing, skipped input, and failure. Extracted document content is explicitly untrusted. Tool-read fallback depends on runtime access to the file. | Adopt explicit delivery outcomes and runtime-readable references. Do not adopt channel-specific retention defaults for durable conversation history. |
| Claude Code documented workflow | Supports pasted/dropped images, image paths, and file/directory mentions. | Preserve these input methods. This documentation does not establish a general attachment storage architecture. |

Sources: [Codex app-server](https://learn.chatgpt.com/docs/app-server), [Codex input types](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/user_input.rs), [Codex attachment store](https://github.com/openai/codex/blob/main/codex-rs/attachment-store/src/lib.rs), [OpenCode V2 attachments](https://opencode.ai/v2/docs/attachments), [OpenCode image configuration](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/attachments.ts), [OpenClaw media understanding](https://docs.openclaw.ai/nodes/media-understanding), [OpenClaw managed images](https://github.com/openclaw/openclaw/blob/main/src/gateway/managed-image-attachments.ts), [OpenClaw cloud attachment placement](https://docs.openclaw.ai/gateway/cloud-sessions), [Claude Code images and file references](https://code.claude.com/docs/en/common-workflows).

These sources describe different interfaces and versions. A transport accepting an image is not evidence of durable storage, and a file appearing in the UI is not evidence that the model received its contents.

## Choice of scope

| Alternative | Assessment |
|---|---|
| Increase the admission limit to fit base64 | Rejected as the primary repair: retains repeated serialization and bloated execution/history inputs, and does not fix lost drafts or provider limits. |
| Compress every image below approximately 192 KiB | Rejected: metadata reduces that budget further and fine-text screenshots can become unreadable. Compression serves provider/image budgets, not the admission envelope. |
| Store only the original desktop path | Rejected for attached snapshots: later edits, temporary-file deletion, and remote placement change what can be read. Live project mentions keep their separate semantics. |
| Session-owned immutable bytes with small references | Selected: fixes the shared admission boundary and gives replay, previews, and transport a consistent identity using one local store. |

## Target contract

Attachment bytes are materialized and validated before a turn is admitted. Admission and history carry small, ordered, session-owned references. Provider conversion reads only the selected representation. User-visible delivery state records what was actually included, not just whether the file uploaded.

A reference contains a schema version, opaque attachment ID, owner session, digest, decoded byte length, detected MIME, filename, and optional original-path provenance. The client cannot choose a trusted storage path or mint ownership. Filenames and paths remain data, not instructions or capabilities.

Use one local session attachment store, with immutable originals and optional derived image representations. Place the canonical bytes under the owning session directory, outside the agent-mutable workdir; retain the current readable workdir copy or materialize one when a file tool needs it. The existing absolute-path marker and preview-path syntax remain compatible display/tool projections, not the immutable identity. No cross-session dedup or remote object-storage service is required.

The existing execution state-blob store requires an execution/attempt owner. Uploads exist before admission and can survive multiple attempts, so that API cannot directly own their lifecycle. Reuse its digest/verification conventions without weakening its ownership checks.

For Web uploads, introduce an authenticated streaming upload boundary that returns a reference after complete validation. It binds provisional chat ownership through server state before accepting bytes. Desktop source paths remain provenance; the managed snapshot is what a queued/replayed turn reads. Channels, CLI, and legacy inline clients normalize through the same ingestion primitive. Compatibility decoding is bounded before allocation and never persists base64 in new admission envelopes.

### Original, tool copy, and public read boundary

The reference ID and digest identify the authoritative snapshot. A projection record maps `(owner_session, attachment_id, digest)` to any workdir copy. Before giving a reference-aware tool a path, verify the copy's bytes. Reuse a matching copy; otherwise create a new non-clobber copy from the original and update the projection. Preserve the Agent-edited file as an ordinary project file; never overwrite it or silently treat it as the original attachment. Ordinary path-based tools still read the explicit path's current contents.

The proposed authenticated `GET /api/session/{sid}/attachments/{attachment_id}/content` serves the owned immutable original or an explicitly identified derivative for attachment previews and replay. Existing `/api/file-raw` remains for live files and legacy markers, not as the authority for new snapshot references. Serving an attachment does not grant arbitrary filesystem access; active file formats use download or an isolated preview, not execution in the application origin.

### Public upload and submission contract

These are proposed interfaces, not existing routes. `PUT /api/session/{sid}/attachments/{upload_id}` streams one file with declared filename, length, MIME, and digest, all verified against received bytes. For a provisional chat, it atomically registers upload ownership for the authenticated caller; an existing chat must pass its normal authorization. A completed identical upload returns the same attachment reference. Reusing an ID for different content conflicts; an unfinished upload can restart after its old active writer is released.

Each completed upload also returns a durable `draft_claim_id`, scoped to the authenticated principal, session, and upload selection. Store that claim server-side with the attachment reference; provisional ownership is a GC retention root, not just a browser hint. Repeated identical uploads reuse the same claim; another selection of the same bytes has a distinct claim even if the stored original is deduplicated. An explicit authenticated `DELETE /api/session/{sid}/attachment-drafts/{claim_id}` releases only that selection when the user removes it or discards its draft. Browser close/disconnection does not release it. Acceptance transfers precisely the submitted claims to history/execution ownership; rejection leaves them retained. Other selections and draft edits remain claimed. A storage quota can reject new uploads but cannot silently evict these retained drafts.

The existing WebSocket `chat` action gains `submission_id`, ordered attachment references, and the exact draft claim IDs selected for that submission. Proposed `GET /api/session/{sid}/submissions/{submission_id}` returns the durable result only after authentication and normal session-read authorization. Identifiers alone grant no access; unauthorized and nonexistent sessions produce the same safe not-found response. Within an authorized session, a missing submission returns `not_found`. Add a `chat_submissions` record in the execution database keyed by `(session_id, submission_id)`, with authenticated owner, canonical input hash, ordered reference IDs, status, execution ID, deterministic user-message ID, and a safe error code. Claim the key before admission; concurrent requests with the same `(session_id, submission_id)` and canonical input share one result and execution. Different submission IDs represent independent user submissions, even when their content matches, and obey existing session concurrency/queue rules. There is no content-based deduplication of user intent. Reload and retry reuse the original ID rather than creating another one.

| Submission result | Meaning and client behavior |
|---|---|
| `not_found` | No durable submission claim is known. Retain the draft and resend the same ID/content; do not allocate a new ID. |
| `pending` | The request is claimed but user-message persistence is not confirmed. Keep the draft and reconcile; the server resumes or closes the recorded operation after a crash. |
| `accepted` | Both execution and deterministic user node are persisted. `chat_ack` and the query return the same submission ID, user-message ID, and execution ID. Only this result clears the exact submitted draft. |
| `rejected` | The operation definitively failed. Return a bounded code and retain input. A changed/repaired draft uses a new submission ID; repeating the old ID returns its recorded rejection. |
| `conflict` | The same key was supplied with different content/ordered references. Do not change the stored original record or start another execution. |

A query, reload, or repeated request never resolves `pending` by creating a second execution. Server reconciliation uses the stored execution/user-message identities to finish message persistence or record rejection; titles are not evidence of success.

## Budgets and delivery

Proposed initial defaults retain the existing 32 MiB document and 64 MiB per-turn decoded budgets. Add an explicit 16-attachment limit. Keep the 256 KiB admission envelope limit after removing media bytes; it still bounds text, metadata, rules, and references. These are product defaults to verify, not borrowed provider guarantees.

| Budget | Enforcement and meaning |
|---|---|
| Upload bytes | Streamed decoded bytes; reserve and count every selected attachment even on a dedup hit. Validate file count and aggregate bytes before admission. |
| Legacy base64 | Estimate decoded size before decoding, validate encoding, then verify actual size. Socket ingress needs an explicit transport cap; do not assume the file cap protects the WebSocket frame. |
| Image representation | Preserve original; prepare a derivative with a proposed 2000-pixel longest side and at most 5 MiB encoded base64, further reduced by active provider limits. Detect MIME and decoding failures. Bound decoded pixel/frame counts, decoder memory, and processing time before accepting a derivative. Record transformations. |
| Text/PDF preview | At most 4096 UTF-8 bytes per file and 32 KiB total, including wrappers and truncation notices; bound extraction time and page work separately. Existing PDF slicing is character-based and cannot prove a byte cap. |
| Provider request | Apply model modality, image count, image dimensions, encoded bytes, and token/context limits at conversion. Metadata limits never replace this check. |

Budget scope is explicit: upload reservations belong to `(authenticated owner, session, upload_id)` and charge temporary-storage quota while a writer is active. Abort, validation failure, cancellation, or expiration releases the reservation and removes its incomplete temporary file. A completed upload charges stored bytes until reclaimed; repeating it does not charge storage twice. The per-turn 64 MiB / 16-item budget is recomputed from every ordered selected reference under the submission claim, including repeated/deduplicated content, independently of upload storage accounting. Duplicate submissions do not reserve it twice. Concurrent tabs have separate upload IDs and submission claims, while existing session execution concurrency rules still apply. Reclaim completed but unclaimed uploads only after checking server-side draft claims, pending submissions, and durable history ownership; a disconnected client is not proof of abandonment.

For animated images, unsupported formats, transparency, or fine text, conversion must not silently discard meaningful content. Preserve the original, state any derivative selection, and provide an original-resolution path when supported. Decoder failure is an attachment failure, not a successful thumbnail fallback.

| Input | Model delivery | Visible result |
|---|---|---|
| Supported image + vision model | Native image from verified derivative/original | Image included; transformation details available |
| Image + text-only model | Readable reference and explicit tool fallback only if an enabled, authorized image tool can read it | File available for tools; image not included in model input |
| Text/code | Bounded untrusted preview and readable reference | Preview included; remainder available on demand |
| PDF | Bounded text preview/page summary and PDF tool access | Preview included, or no extractable text with a usable file |
| Other binary | Readable file reference; no fabricated extracted content | File available for tools |
| Image + text-only model + no currently usable authorized image tool | Reject with `attachment_delivery_unavailable`; never remove the image silently | Preserve draft; select a vision model, enable an authorized tool, or remove the image |
| Missing, denied, corrupt, or oversized file | Block that submission and retain the draft | Specific error; remove/replace/retry without losing other attachments |

Native PDF blocks are a later extension: require explicit provider support, page/byte/token budgets, and integration tests. Scanned-PDF image rendering, OCR, audio, video, and remote URL fetching are not prerequisites for repairing image submission. Existing `@`/typed-path references retain live-file semantics; an explicit attached snapshot and a live project reference must be distinguishable.

If a tool is disabled, denied by policy, or unable to read the file, it is not a usable fallback. If approval is still required, retain input and complete the existing approval flow before accepting that delivery mode. The selected delivery plan is checked at admission and again at activation; a later revocation ends the accepted execution with a persistent specific error, rather than running a text-only answer. Model capability changes cannot silently drop an already attached image.

The cost claim is bounded text preview cost, not constant total model cost. Vision tokens and native document tokens scale with representation. Earlier previews can remain in later context; they are not guaranteed to cost zero on subsequent turns. Ten 30 MiB files exceed the proposed aggregate budget and are rejected before admission.

## Submission, recovery, and ownership

1. Persist the unsent draft, selected attachment IDs, original input, and a stable client submission ID in the existing per-chat draft/IndexedDB system. Upload progress and failures stay attached to the originating chat, including split views.
2. Upload, validate, and prepare representations. Upload completion does not mean the turn was accepted.
3. Send text and ordered references with the submission ID. The server checks ownership, bytes, capability, and metadata budgets, then records a submission-to-execution mapping and the user-message persistence outcome. A positive acknowledgement identifies the committed user node and execution.
4. Clear only the acknowledged submission snapshot. Preserve text/attachments added while the request was pending. Do not revoke previews needed by pending or failed submissions.
5. On a definitive rejection, keep the draft with a persistent, specific error. On timeout/disconnection, show that the result is unknown and reconcile using the same ID. Never create a new execution simply because an acknowledgement was lost. Same ID with changed content is a conflict.

Crash cases between execution admission and user-node persistence need explicit reconciliation; the SQLite execution store and Git session store are not one atomic transaction. A failed persistence operation must not return success or leave an apparently running empty chat. Session title creation is presentation state, not acceptance evidence.

Digest verification prevents a modified workdir file from changing queued input. References resolve only within authorized session ownership; guessed IDs, cross-session references, traversal, symlink escapes, and stale grants fail closed. Authorized fork/attach/export transfers retain or copy the required bytes and establish new ownership; string-copying a reference is insufficient. Project moves resolve ownership through the current session location index.

| Lifetime operation | Ownership rule |
|---|---|
| Preview / replay | Resolve the immutable ID, verify digest and owner, and read the requested original/derivative; never substitute a mutable copy. |
| Fork / attach / merge | After existing session authorization, materialize referenced bytes under the destination session and register destination-owned IDs; preserve provenance and order. Failure aborts the transfer rather than leaving dangling references. Source deletion cannot remove the destination's copy. |
| Archive / delete | Archive keeps data. Delete removes only data owned by that session once its active work is settled under normal deletion policy; never remove a shared project directory. |
| Export / import | Export includes reference manifests and verified originals; a missing required original is an explicit export error. Import validates hashes and creates destination-owned IDs before making imported history available. |
| Project relocation | Move the complete owned session store and update its location atomically through the existing location mechanism, with recovery for interrupted moves; references remain location-independent. |

Untrusted extracted text uses a consistent external-content wrapper with escaped delimiters and a bounded size. Wrapping is provenance, not complete prompt-injection protection. Existing tool authorization and file-read checks remain authoritative.

Originals remain available while referenced by server-side draft claims, history, queued work, checkpoints, or authorized branches. Unclaimed uploads can be reclaimed only after a grace period and a complete ownership check; grace duration is configured during implementation. Archive preserves attachments. Delete/export/import/relocate operate on owned data and manifests together. Never delete a project workdir to reclaim one session's media.

## Implementation plan and acceptance

Each phase updates this document's final status, product documentation, and focused evidence before the next phase begins. No phase is considered usable until its end-to-end acceptance passes.

| Phase | Changes | Required acceptance |
|---|---|---|
| A: durable references | Shared ingestion and session-owned originals; admission schema compatibility; activation/history conversion; Web and channel image callers | A valid 1 MiB image is saved and reaches a mocked provider once; new admission input has references and stays below 256 KiB. Restart, retry, missing/corrupt bytes, migration and unauthorized reference cases are covered. Existing small inline history still loads. |
| B: reliable submission | Streaming upload, provisional ownership, correlated submission ID, persistent error and exact-snapshot acknowledgement handling | Real Web entry tests cover oversize, partial upload, lost ACK, reload, double submit, tab switch, edits during submission, upload reservation release, same-ID concurrent retries, independent different-ID submissions, and offline draft restoration beyond the unclaimed-upload grace period. No silent attachment loss or duplicate execution. Default Desktop accepts a real screenshot on the first send. |
| C: delivery and lifecycle | Bounded image derivatives, truthful file state, model-switch behavior, retention/branch/export rules | Verify text-only versus vision requests, absent/denied fallback and later revocation, current/replayed image order, Unicode previews, total limits including dedup, transformed-image disclosure, branch/delete/move/export lifetime, and narrow-layout UI. |
| Later: additional modalities | Native PDF adapters, scanned PDF extraction, audio/video | Separate provider-specific contracts, budgets and acceptance before advertising support. |

Primary implementation boundaries: `chat.py`, `production_driver.py`, `dispatcher/types.py`, `dispatcher/loop_runner.py`, session location/serialization code, channel attachment normalization, composer attachment cache and submission code, command-error handling, and existing file-preview routes. Do not implement the size correction solely in the Web route: every canonical image caller shares the admission limit.

Verification uses actual chat/intake boundaries plus a deterministic mocked provider, not only helper assertions. Existing starting suites are `tests/unit/attachments/`, `tests/unit/channels/test_channels_attachments.py`, and `tests/component/agent/test_production_driver.py`; Web checks include provisional-send and local-attachment-paths. Full Python/Web/Desktop gates follow the repository test policy. A final implementation requires independent specification and quality reviews, a clean commit, and default-App verification through `scripts/refresh-local-app.sh`. No remote write is implied.

## Implementation status

Existing and inspected: uploaded-file persistence, per-session dedup intent, markers with source/preview paths, bounded preview intent, image blocks, composer IndexedDB drafts, and file previews. Their limits and failure semantics do not yet satisfy the target contract.

Reproduced: 100 KiB binary data encoded into a minimal image envelope passes admission; 200 KiB and 1 MiB fail the 256 KiB check. This is a local admission probe, not a provider or full upload test. The linked empty-chat incident independently records the same error category.

Designed, not implemented: phases A, B, C, and the later modality extension. No runtime code, stored conversation, or installed App is changed by this design update. The [two-way attachment and preview document](chat-attachments.html) covers the display/output scope; this document owns inbound storage, admission, delivery, and lifecycle.
