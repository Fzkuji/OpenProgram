# Persisting In-Flight State / Reconnect Recovery

## Problem Statement

Currently, any "in-flight" artifact shown in the UI (LLM streaming reply / tool call / agentic
function / task spawn / merge) lives **only in memory + the WebSocket
stream** until it finishes. Refreshing the page loses it — because the msg hasn't been written to
the SessionDB yet. You can only see it in a new page after the final flush to disk.

And if the backend dies midway / the network drops, that artifact is lost forever: there's no
state to query, no way to recover, and no way to "take a peek at the current progress."

## Goal

> Any "in-flight" artifact visible in chat / DAG should **immediately persist a
> placeholder**, and every subsequent incremental update is flushed to disk + pushed over WS. The
> frontend can refresh / switch back at any moment, see the current progress + keep receiving the
> live stream, with zero state loss.

## Five Components

### 1. Unified placeholder schema

Every msg (whether user / assistant / tool) gains three new fields:

```
status:          "pending" | "running" | "done" | "error" | "aborted"
started_at:      float (epoch)
last_update_at:  float (epoch)
```

`pending` = id assigned but not started yet; `running` = actively producing output; one of three terminal states.

**Write timing**: any operation that runs for a while writes its placeholder **immediately**:
- LLM streaming reply: before the dispatcher calls the LLM
- tool call: before the tool dispatcher calls the function
- agentic function: before `_execute/run.py` runs the function
- task spawn: already in place ([`runner.py`](../../../openprogram/agent/task/runner.py))
- merge: before `_execute/_run_merge`

### 2. Throttled incremental persistence

Every `status=running` msg, while running, writes its current snapshot back to the SessionDB
**throttled at ~250ms**:

- `content`: streaming partial / current tree dump
- `metadata.context_tree`: the agentic function's DAG snapshot
- `metadata.partial_tokens_used`: cumulative input/output tokens
- `last_update_at`: now

Throttling is managed by a per-msg `_ThrottledSaver` instance, which guarantees one final flush to
disk before the backend exits (atexit / signal hook).

When done, call `finalize(status="done", content=...)` to write the terminal state.

### 3. WS subscription by msg_id

New ws action:

```json
{ "action": "subscribe_msg", "session_id": "...", "msg_id": "..." }
```

The backend maintains:

```python
_msg_subscribers: dict[tuple[str, str], set[WebSocket]]
```

On each placeholder update, both persist and push to that channel:

```json
{
  "type": "msg_update",
  "data": {
    "session_id": "...",
    "msg_id": "...",
    "content_delta": "...",
    "tree": {...},
    "status": "running"
  }
}
```

Subscription release: cleaned up automatically when the client sends `unsubscribe_msg` / disconnects / the msg enters a terminal state.

### 4. Frontend load_session detection + auto-resubscribe

After `session-store`'s `feedFromConv` call, scan all ChatMsg:

```ts
const running = msgs.filter(m => m.status === "running");
running.forEach(m => wsSend({
  action: "subscribe_msg",
  session_id: sid,
  msg_id: m.id,
}));
```

On receiving a `msg_update` event, `patch` the corresponding ChatMsg's content / tree.

UI layer:
- `AssistantBubble`, on seeing `status=running`, renders a cursor / streaming animation
- `RuntimeBlock`, on seeing `status=running`, shows the running Execution DAG (already
  supported, since that's how the stream tree is drawn now)
- attach card: consistent with the existing `status=running` behavior (already present)

### 5. Death detection + abort sweep

When the worker starts:
1. Scan `~/.openprogram/sessions/*/history/*.json`
2. Find msgs with `status=running` whose `last_update_at` exceeds the threshold (e.g. 5 minutes)
3. Set status=`aborted`, and append a line `metadata.aborted_reason="worker restart"`

This ensures no orphan msgs are left "running forever" after a backend restart.

## Schema Changes (Phase 1)

`openprogram/store/_msg_adapter.py::_node_to_msg`:

- On write, reflect `node.metadata.status` / `started_at` / `last_update_at`
  into the msg dict
- Old nodes without a status field default to `status="done"` (backward compatible)

`openprogram/context/nodes.py::Call`: don't touch the dataclass; all new fields go through
`metadata`.

## Implementation Phases

| Phase | Scope | Estimate |
|---|---|---|
| 1 | placeholder schema + worker abort sweep | 30 min |
| 2 | `_execute/run.py` agentic function writes placeholder + throttled tree save | 60 min |
| 3 | dispatcher LLM reply writes placeholder + streaming content save | 60 min |
| 4 | inline tool call placeholder (long-running bash, etc.) | 30 min |
| 5 | WS `subscribe_msg` channel + per-msg broadcast | 60 min |
| 6 | frontend load_session detection + auto subscribe + live patch | 60 min |
| 7 | tests + edge cases (restart / disconnect / msg_id collision) | 30 min |

Total ~5h. Each phase is independently usable + rollback-able.

## Out of Scope This Time

- True "resume after interruption" — e.g. the backend dies midway and, after restart, continues
  running the remaining sub-calls. This requires checkpointing the LLM context + replay, several
  times the effort of this design. Under the current approach, an interrupted msg is marked
  aborted, and the user reruns it with the Retry button.
- A cross-session global active-task panel ("which msgs are running right now"). This is easy to
  extend on top of the `_msg_subscribers` keyspace, but not in this round.
