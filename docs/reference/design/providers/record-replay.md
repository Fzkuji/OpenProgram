# Recording and replaying provider calls

> Deterministic loop tests need a provider that answers the same way every run. Recording captures one real
> session at the API-provider chokepoint into a JSONL recording file; replay serves that recording file back offline, and reports
> the exact field where a later run diverges.

---

## 1. Where it attaches

Every model call in the framework goes through `providers/stream.py`, which looks up one `ApiProvider` in
`api_registry` by `model.api` and calls `stream()` / `stream_simple()`. That registry entry is the single
narrowest point where a request goes out and events come back, so both recording and replay are `ApiProvider`
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

## 2. Recording file format

JSONL, one JSON object per line, written in the order events occur. The first line is the header:

```json
{"type": "header", "format_version": 1}
```

`format_version` is a single integer, `RECORDING_FORMAT_VERSION` in `openprogram/providers/recording.py`. Replay
compares it for equality and refuses any other value, so a recording file written before a shape change is rejected
rather than misread. Bump it whenever a line's shape changes.

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

## 5. Scope

Recording and replay are library modules used from tests. There is no CLI entry point, no UI surface, and no
recording file-management command; those wait until the format has settled. Tests register the providers themselves:

```python
register_api_provider(api, RecordingProvider(real_provider, recording_path))  # capture
register_api_provider(api, ReplayProvider(recording_path))                    # replay
```

## Implementation status

Implemented: `openprogram/providers/recording.py`, `openprogram/providers/replay.py`, covered by
`tests/providers/test_record_replay.py`.
