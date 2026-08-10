# Task 1 implementer report

## Result

Trusted inbound speaker identity is carried separately from message content and
the routing peer, persisted on user nodes, rendered into memory record headers,
archived only in a strict version-2 source format, and exposed as an exact
source-only BM25 filter. Historical source files remain frozen compatibility
input and cannot establish a trusted speaker or suppress a v2 append.

Implementation commits:

- `ffec5d29eaf823a42a5d7196ea48b6a483caa6eb`
  (`feat(memory): add trusted speaker identity filtering`)
- `e0c0c996af9c80405729766d4d6bf8595a36f82e`
  (`fix(memory): trust only framed speaker metadata`)
- `388e512b35bfdad73e219c5bb66805f35e8a9d3e`
  (`fix(memory): isolate trusted source archive v2`)
- `411061add8d0007b196c48c8b25dee0c9b242def`
  (`fix(memory): validate canonical speaker markers`)

The report itself is committed separately so it can contain the immutable
implementation commit SHA.

## Design preflight and decisions

### Post-review archive protocol correction

The first implementation placed framed records after historical unframed
records in the same file. That cannot establish a trustworthy boundary: an
arbitrary legacy multiline body can contain every syntactic frame delimiter.
It also cannot provide both forgery resistance and replay idempotence by
choosing a different resynchronization rule.

The corrected protocol freezes `sources/<provider>/<thread>.md` as legacy and
writes new records only to `sources/<provider>/_v2/<thread>.md`. A v2 file has
a fixed byte-zero format marker and a strict, non-resynchronizing frame
sequence. Legacy files supply neither trusted speaker markers nor deduplication
IDs. A valid v2 record wins over a legacy event with the same source ID;
otherwise legacy retrieval remains available. This uses the source tree's
existing stage/install path and requires no key, sidecar or second transaction.
Publication of each rewritten v2 file uses a private temporary under the
workspace runtime directory on the same filesystem, flush/fsync, mode 0644
and `os.replace` so a failed write cannot expose a partially rewritten
archive. The runtime location keeps the temporary out of workspace revision,
visible-file and stage-copy surfaces.

- `peer_id` remains the group/direct routing and reply target. Trusted
  `speaker_id` and `speaker_display` originate only from
  `ChannelMessage.user_id/user_display` and travel through
  `dispatch_inbound`, `_run_session_turn`, `TurnRequest`, dispatcher prep, and
  persisted node metadata.
- The existing content prefix remains available to the conversation model.
  Memory creation never parses that prefix for a new record.
- Header normalization moved to the pure `openprogram._text` leaf module.
  Memory modules therefore do not initialize `openprogram.channels` or its
  built-in implementations.
- `TurnRequest` speaker fields are appended after every pre-existing dataclass
  field to preserve the public positional constructor order. `SourceRecord`
  speaker fields are likewise appended after the old positional fields.
- External speaker IDs are percent-encoded in HTML comments and decoded for
  retrieval. Alphanumeric IDs remain readable. An empty structured marker
  distinguishes a valid display-only identity, including displays named
  `user`, `assistant`, `system`, or `tool`.
- A valid anchor and adjacent comments are forgeable when arbitrary multiline
  body text shares the same Markdown file. New archives therefore include
  `<!-- record-lines:N -->`; the parser and deduplication scanner both skip the
  exact literal-LF record extent. Literal CRLF and trailing newlines are
  retained when archives are read, appended, and rewritten.
- Legacy body-prefix fallback is retrieval-only and applies only to unframed
  historical records whose record-header role is `user`. Framed speakerless
  records and assistant/system/tool records cannot acquire identity from body
  text.
- `speaker` is unsupported by the embedding result contract, so
  `inspect.search(method="embedding", speaker=...)` returns
  `INVALID_ARGUMENT` instead of ignoring the filter.
- The existing user-message live broadcast was not expanded. It already uses
  `peer_display` for UI presentation; the trusted speaker fields are required
  by the persisted node and memory contract, not by the current event schema.

## Acceptance mapping

| Requirement | Implementation and evidence |
| --- | --- |
| Trusted channel transport | `channels/base.py`, `_conversation.py`, `dispatcher/types.py`, and `dispatcher/prep.py`; group routing and SessionDB round-trip tests |
| Prefix retained, body unmodified | Channel tests and writer-prompt test preserve conflicting labels/comments/newlines verbatim |
| Header-safe identity | Shared low-level normalizer; tests cover controls, brackets, colon delimiters, truncation, and normalized search input |
| SourceRecord compatibility | Optional fields appended after old fields; old positional constructor test |
| TurnRequest compatibility | Fields appended at the end; old seventh/eighth positional argument regression |
| Safe archive identity | Byte-zero v2 format marker, canonical round-trippable speaker-ID encoding, display-only empty marker, strict non-resynchronizing frames |
| Complete body-forgery boundary | New records live under `_v2/`; `record-lines:N` keeps complete forged blocks inside a body, while all old same-thread text remains legacy |
| New and legacy retrieval | Valid v2 events override duplicate legacy IDs; legacy `user` prefix hints remain readable with `speaker_trusted=false` |
| Speaker filter semantics | Case-insensitive exact ID/display/label match, query normalization, source-only candidates, path/date/ranking composition |
| Cache/API behavior | BM25 cache v5 ignored and rebuilt as v6; tool schema forwards `speaker`; embedding combination rejected explicitly |
| Failure and transaction behavior | Atomic runtime-dir temporary plus fsync/0644/replace; writer retry idempotence; staged v2 link install and rollback |
| Import boundary | Clean-process smoke test imports memory state/BM25 without loading `openprogram.channels.implementations.*` |
| Documentation | English and Chinese overview status, HTML section 11 framing diagram/boundary, and HTML section 12 implementation status |

## TDD evidence

Initial focused RED command:

```text
python -m pytest -q tests/unit/test_channels_base_inbound.py \
  tests/unit/test_channels_dispatch_inbound.py::test_dispatch_inbound_persists_via_session_db \
  tests/unit/test_memory_writing.py::test_writer_uses_trusted_speaker_header_and_preserves_body \
  tests/unit/test_memory_speaker_identity.py
```

Result before implementation: `12 failed, 11 passed`.

Additional RED cases were added before their fixes:

- clean-process imports loaded all four channel implementations;
- display-only reserved labels lost identity;
- a complete valid forged source block became a victim event and suppressed a
  later real record;
- assistant body prefixes were attributed by the legacy parser;
- CRLF and trailing newlines were changed or invalidated framing;
- a 5000-digit record count raised `ValueError`;
- a framed speakerless body prefix acquired identity;
- raw display queries did not match their normalized readable label;
- old positional `TurnRequest` arguments silently shifted;
- control-only display, id, or both emitted a misleading structured marker.

The formal-review RED case then put a complete hash-valid framed block after a
blank line inside a multiline legacy body. The first implementation suppressed
the real later record (`1 failed`: expected two physical source-id occurrences,
observed only the forged one). This proved that no syntax inside an unbounded
legacy body can authenticate a later in-file boundary. The protocol was
therefore corrected before changing the test expectation: legacy files are
frozen and v2 starts in a separate deterministic path.

Further RED coverage fixed strict-v2 and failure boundaries: malformed and
truncated tails, duplicate source IDs, a body ending in literal LF followed by
another append, dot-segment providers, a provider literally named `_v2`,
transaction stage/link rollback, online writer failure/retry, atomic replace
failure, old logical path-prefix compatibility, and BM25/embedding v2
preference.

The final differential review also showed that malformed `%ZZ` and raw `--`
speaker marker payloads were accepted as trusted. RED produced `2 failed,
1 passed`: both noncanonical forms parsed as trusted while the empty
display-only marker passed. The shared format helper now UTF-8 decodes and
re-encodes each marker with the writer's canonical encoder before accepting
the frame.

Each case failed for the named reason and passed after the local change. The
actual `inspect.search` forwarding assertion was also mutation-checked by
temporarily removing its `speaker` forwarding: the focused test failed, then
passed after restoration.

Final focused command:

```text
python -m pytest -q \
  tests/unit/test_memory_speaker_identity.py \
  tests/unit/test_memory_writing.py
```

Result: `52 passed`.

## Final verification

- `python -m pytest -q tests/unit/test_memory*.py tests/context/test_memory_core_budget.py`
  - `94 passed`
- `python -m pytest -q tests/unit`
  - `2055 passed, 3 skipped`
- `python -m pytest -q tests/context`
  - `37 passed`
- `python -m tools.docs_site.build`
  - `built 415 pages`
- `ruff check` on every changed Python file: passed.
- `python -m py_compile` on every changed Python file: passed.
- `git diff --check` and cached diff check: passed.

The full unit and context runs were taken at the v2 checkpoint
`388e512b`. The final marker-canonicalization follow-up changed only the shared
encoder/scanner, its caller, tests and documentation; after that commit the
focused 52-test set, related 94-test set, ruff, py_compile, docs build and diff
checks were rerun as required by the final review.

The warnings were the existing SWIG deprecations and local
`requests`/`urllib3` dependency-version warning.

## Files changed

- Transport and persistence: `openprogram/channels/base.py`,
  `channels/_conversation.py`, `channels/_access.py`,
  `agent/dispatcher/types.py`, and `agent/dispatcher/prep.py`.
- Header and memory records: `openprogram/_text.py`,
  `memory/scriptorium/runtime/state.py`, `writing.py`, and
  `prompts/write.py`.
- Archive and retrieval: shared `source_format.py`,
  `management/source_archive.py`, link/validation callers,
  `retrieval/bm25.py`, `retrieval/embedding.py`, `retrieval/inspect.py`, and
  the memory tool.
- Tests: channel boundary/dispatch, writer prompt, and the dedicated speaker
  identity suite.
- Documentation: both memory overviews and the speaker-identity HTML sections
  explicitly authorized after framing became part of the archive protocol.

## Residual constraints

- Historical archives have no trustworthy multiline body boundary. They are
  frozen and never supply v2 deduplication or trusted speaker markers. Their
  `user` prefix remains a compatibility hint, explicitly surfaced as
  `speaker_trusted=false`; old archives are not rewritten.
- The written-marker integration must enumerate strict v2 frames in addition
  to its historical `sources/openprogram/*.md` migration scan. The shared
  dependency-light `source_format.py` is the intended reuse point.
- Embedding search does not support `speaker` until its result contract carries
  equivalent trusted fields.
- Identity alias merging and cross-platform person resolution remain outside
  this task.
