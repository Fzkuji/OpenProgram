# Task 1 implementer report

## Result

Trusted inbound speaker identity is carried separately from message content and
the routing peer, persisted on user nodes, rendered into memory record headers,
archived with an injection-safe structured marker, and exposed as an exact
source-only BM25 filter.

Implementation commit:
`ffec5d29eaf823a42a5d7196ea48b6a483caa6eb`
(`feat(memory): add trusted speaker identity filtering`).

The report itself is committed separately so it can contain the immutable
implementation commit SHA.

## Design preflight and decisions

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
| Safe archive identity | Percent-encoded ID comment, display-only empty marker, normalized-empty identity suppression |
| Complete body-forgery boundary | Literal `record-lines:N` framing; valid hash/source/speaker/record forged block stays inside its real event and cannot suppress a later real source ID |
| New and legacy retrieval | New structured records plus unframed legacy `user` fallback in one index; assistant and framed speakerless false-positive tests |
| Speaker filter semantics | Case-insensitive exact ID/display/label match, query normalization, source-only candidates, path/date/ranking composition |
| Cache/API behavior | BM25 cache v4 ignored and rebuilt as v5; tool schema forwards `speaker`; embedding combination rejected explicitly |
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

Each case failed for the named reason and passed after the local change. The
actual `inspect.search` forwarding assertion was also mutation-checked by
temporarily removing its `speaker` forwarding: the focused test failed, then
passed after restoration.

Final focused command:

```text
TMPDIR="$(mktemp -d)" python -m pytest -q \
  tests/unit/test_channels_base_inbound.py \
  tests/unit/test_channels_dispatch_inbound.py \
  tests/unit/test_memory_writing.py::test_writer_uses_trusted_speaker_header_and_preserves_body \
  tests/unit/test_memory_speaker_identity.py
```

Result: `35 passed`.

## Final verification

- `TMPDIR="$(mktemp -d)" python -m pytest -q tests/unit`
  - `2040 passed, 3 skipped`
- `TMPDIR="$(mktemp -d)" python -m pytest -q tests/context`
  - `37 passed`
- Collection without exclusions identified the repository's known broken
  import in `tests/integration/test_test_framework.py`:
  `ModuleNotFoundError: openprogram.functions.agentics.test_framework`.
- `TMPDIR="$(mktemp -d)" python -m pytest -q tests --ignore=tests/integration/test_test_framework.py`
  - `2548 passed, 7 skipped, 2 deselected, 1 xfailed, 11 failed`
  - The 11 failures are the existing TestClient origin-policy baseline:
    `test_attach_lazy_session.py` (4), `test_healthz.py` (3),
    `test_ws_qr_login.py` (1), and `test_ws_search.py` (3), all HTTP 403 or
    WebSocket 1008.
  - The live sibling baseline has the identical 11-failure set
    (`11 failed, 2556 passed, 7 skipped, 2 deselected, 1 xfailed`); its extra
    passes come from that sibling branch's additional tests.
- `ruff check` on every changed Python file: passed.
- `python -m py_compile` on every changed Python file: passed.
- `git diff --check` and cached diff check: passed.

The warnings were the existing SWIG deprecations and local
`requests`/`urllib3` dependency-version warning.

## Files changed

- Transport and persistence: `openprogram/channels/base.py`,
  `channels/_conversation.py`, `channels/_access.py`,
  `agent/dispatcher/types.py`, and `agent/dispatcher/prep.py`.
- Header and memory records: `openprogram/_text.py`,
  `memory/scriptorium/runtime/state.py`, `writing.py`, and
  `prompts/write.py`.
- Archive and retrieval: `management/source_archive.py`,
  `retrieval/bm25.py`, `retrieval/inspect.py`, and the memory tool.
- Tests: channel boundary/dispatch, writer prompt, and the dedicated speaker
  identity suite.
- Documentation: both memory overviews and the speaker-identity HTML sections
  explicitly authorized after framing became part of the archive protocol.

## Residual constraints

- Historical unframed archives have no trustworthy multiline body boundary.
  Their compatibility parser is intentionally limited to a `user` header and
  its first physical record line; old archives are not rewritten.
- Embedding search does not support `speaker` until its result contract carries
  equivalent trusted fields.
- Identity alias merging and cross-platform person resolution remain outside
  this task.
