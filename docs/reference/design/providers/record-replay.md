# Recording and replaying provider calls

> Deterministic loop tests need a provider that answers the same way every run. Recording captures one real
> session at the shared API-provider boundary into a JSONL recording file; replay returns those events offline and reports
> the exact field where a later run diverges.

---

## 1. Request boundary

Every model call in the framework goes through `providers/stream.py`, which looks up one `ApiProvider` in
`api_registry` by `model.api` and calls `stream()` / `stream_simple()`. That registry entry is the single
narrowest shared point where a request goes out and events come back, so both recording and replay are `ApiProvider`
implementations registered there — no vendor module knows about either.

```
stream_simple(model, context, options)
        │  get_api_provider(model.api)
        ▼
RecordingProvider ──delegates──▶ real vendor provider ──▶ network
        │ writes recording.jsonl
        ▼
    events flow on unchanged

ReplayProvider ──reads recording.jsonl──▶ events, no vendor provider, no socket
```

`RecordingProvider` wraps the provider it replaces and is transparent: it yields exactly the events the wrapped
provider produced, writing each one out first. `ReplayProvider` wraps nothing and holds no HTTP client, so an
agent loop driven by it cannot reach the network.

Provider package import and runtime activation are separate contracts. Importing `openprogram.providers` defines
and exports APIs; it must not register vendor providers, read `record_replay` configuration, open a recording, or
install a registry transform. `initialize_provider_runtime()` performs built-in registration and one process-wide
configuration snapshot before the first real provider lookup. Concurrent first callers wait for the same READY or
FAILED result. Recordings management commands call file/configuration functions directly and never initialize the
provider runtime, so `status` and `off` remain available when a configured replay file is missing or invalid.

This is not workflow recording. The Agent loop, Runtime, tools, Session, and DAG still execute the current code;
replay replaces only the live LLM provider with recorded events. It does not restore a historical Session, replay
tool side effects, roll back files, or define task steps.

## 2. Recording file format

JSONL, one JSON object per line, written in the order events occur. The first line is the header:

```json
{"type": "header", "format_version": 1}
```

`format_version` is a single integer, `RECORDING_FORMAT_VERSION` in `openprogram/providers/recording.py`. Replay
compares it for equality and refuses any other value, so an incompatible recording is rejected rather than misread.
Bump it when required request/event/call_end fields or their semantics change. Optional header metadata that old
readers may ignore does not require a version bump.

The remaining line types:

| `type` | Fields | Meaning |
| --- | --- | --- |
| `request` | `call_index`, `model`, `context`, `options` | One provider call begins; the three payloads are the redacted `model_dump(mode="json")` of the arguments |
| `event` | `call_index`, `event_index`, `event` | One streamed `AssistantMessageEvent`, dumped as JSON; `event.type` selects the class on the way back |
| `call_end` | `call_index`, `event_count` | The call's stream finished; the count makes a truncated recording file visible |

`call_index` counts provider calls within the recording; `event_index` counts events within one call. A
multi-turn agent loop with a tool call therefore produces call 0 (the tool call), then call 1 (the follow-up
after the tool result), each with its own event sequence.

## 3. Redaction

Redaction runs on every value before it reaches the file and has no off switch. `remove_secret_values()` in
`recording.py` walks the dumped structure and replaces secrets with the fixed placeholder `[secret removed]`:

- **By field name** — a dict key matching `SECRET_FIELD_NAMES` (case-insensitive) loses its whole value. The
  set covers `authorization`, `proxy-authorization`, `api_key`, `x-api-key`, `x-goog-api-key`, `api-key`,
  `token`, `access_token`, `refresh_token`, `id_token`, `cookie`, `set-cookie`, `secret`, `client_secret`,
  `password`, `session_key`. This reaches nested provider-specific dicts, since the walk is recursive.
- **By value shape** — remaining strings are scanned for a `Bearer …` credential, an `sk-…` key, and secrets
  carried in a URL query parameter (`?api_key=`, `&access_token=`, `&token=`). This catches a credential
  pasted into a free-form field whose name says nothing.

Non-secret headers survive, so a recording file stays readable for debugging. Replay redacts the incoming request the
same way before comparing, so a redacted recording file still matches a live request that carries the real key.

## 4. Difference reporting

`ReplayProvider` compares each incoming request against the recorded request at the same `call_index` and
raises `ReplayMismatch` on the first difference. The exception carries `call_index`, `field_path`,
`recorded`, `incoming`, and — when the position rather than the content diverges — `event_index`:

```
replay mismatch at call 1, field context.messages[2].content[0].text:
recorded 'echo:hi', incoming 'echo:bye'
```

`find_first_difference()` walks dicts by sorted key and lists by index, so the same pair of values always
reports the same path. Running past the end of the recording file raises the same exception with `field_path`
`call_index`, naming how many calls were recorded.

Wall-clock fields listed in `NON_DETERMINISTIC_FIELD_NAMES` (currently `timestamp`) are skipped: they differ
between the recording run and every replay run, and comparing them would make every recording file mismatch on its
second message.

## 5. Product entry and scope

The library constructors remain available for focused tests:

```python
register_api_provider(api, RecordingProvider(real_provider, recording_path))  # capture
register_api_provider(api, ReplayProvider(recording_path))                    # replay
```

Product configuration uses `record_replay.mode=off|record|replay` with next-start application. The CLI exposes
`openprogram recordings status|record|replay|off|list|show|delete|prune`. Strict offline replay covers only the LLM
provider layer; tools and other subsystems keep their own network and permission policies. Recordings may contain
prompts, model output, tool results, file excerpts, and personal data even after credential redaction.

## Implementation status

Implemented: strict versioned recording/replay, shared registry transform, next-start configuration, recordings CLI
and file management. Commit `b6460fdc` keeps recovery commands available through a temporary import-time environment
guard.

Approved but not yet implemented: explicit provider runtime lifecycle, side-effect-free provider package import,
thread-safe one-time initialization, stable FAILED propagation, and removal of the environment guard. The normative
design and evidence boundary are in [`record-replay.html`](record-replay.html).
