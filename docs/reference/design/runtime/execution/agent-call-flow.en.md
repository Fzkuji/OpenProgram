# Agent Call Flow (Authoritative Design)

Status: design · Created: 2026-06-18

> This is the **core framework** for agent calls — every turn / LLM call follows this flow. Future features are inserted at the corresponding nodes on top of this; the skeleton stays unchanged. This document was once called "Unifying the LLM call path"; it is now promoted to the authoritative design for the entire call flow.

## Overview Diagram

[`agent-call-flow.svg`](agent-call-flow.svg) depicts the **target (after unification)**: two entry points (user message / inside a function body) converge onto **one shared trunk** — ① render_context reads the context → ② open_call_node → ③ agent_loop → ④ close_call_node; calling exec from inside a tool body returns to the top of the trunk (nesting). The special handling of each entry point (chat's front-end streaming / title / compaction, the shared retry / metering) hangs off the trunk as **optional hooks**. The bottom lists "what is already shared today" and "the 7 steps the merge requires".

> **Current state vs target**: the prose below describes the **current state** (the two entry points each manage their own periphery, each write their own records, and only converge at agent_loop) — serving as the starting reference for the merge. For the concrete steps to unify into the single trunk above, see section 8 of `session-dag.md`.

The skeleton in one sentence (current state): **two peer entry points (dispatcher · runtime.exec) each manage their own periphery and write their own DAG records, converging only at the shared agent_loop; exec sits below the loop → a tool body can call the LLM again, forming nesting**.

## Real Topology: Converging Fork, Not Three-Level Nesting

Code evidence (`agent_loop` has only two call sites in the entire repo: `dispatcher/__init__.py:902` and `agent/agent.py:418`): the dispatcher **does not go through** exec, and exec **does not go through** the dispatcher. They are two peer entry points, sharing only agent_loop.

```
Entry A: user message → dispatcher ──directly─────────────┐
                                                  ├─→ agent_loop (shared engine)
Entry B: inside @agentic_function body → runtime.exec ──via AgentSession─┘
```

| Entry | Responsibility | Path to agent_loop | What DAG record it writes | Implementation |
|---|---|---|---|---|
| **A · dispatcher** | turn lifecycle: session, user node, attach Runtime, resolve model, parse tools, persist, front-end broadcast | **directly** `agent_loop(...)` (`__init__.py:902`) | top-level reply → **assistant session message** (SessionDB, `persistence.py:207`) | `agent/dispatcher/__init__.py` |
| **B · runtime.exec** | one LLM call: open/close the llm node, build the context | via **AgentSession** → `session.run` (`runtime.py:1576`) → `agent.py:418` | this call → **role=llm DAG node** | `agentic_programming/runtime.py` |
| **shared · agent_loop** | tool loop engine: call model → run tools → feed back → loop until plain text | — | the tool's code node called_by → the current llm node | `agent/agent_loop.py:114` |

### Why Two Entry Points, Not One

dispatcher and exec serve two different scenarios, and their peripheral plugins do not overlap: dispatcher needs session management + front-end WebSocket broadcast + title/compaction; exec needs the DAG llm node + AgentSession retry-rollback. A hard merge would saddle one side with logic the other doesn't need (an attempt to fold them in practice hit model forking and node duplication — see step 4 below). agent_loop is shared, because the "call model → run tools → loop" engine is identical for both.

### Key: exec Sits **Below** agent_loop, So It Can Nest

When the dispatcher runs a turn and the model calls an `@agentic_function` tool (e.g. wiki_agent) → the tool body calls `runtime.exec` → which starts another agent_loop. So exec is both "Entry B" and is called in reverse by a tool inside agent_loop:

```
dispatcher → agent_loop → model calls tool → @agentic_function body → runtime.exec → agent_loop (nested) → ...
```

This is the fundamental capability of agentic programming (a function body can nest LLM calls), and it is the source of wiki_agent's recursion. If exec sat above the loop, a tool could not call the LLM itself.

### The Ordered Steps Inside Each Node

**A · dispatcher**: 1 create/load session (read history along the active branch) → 2 write the user node → 3 attach Runtime (_store + _current_runtime) → 4 resolve model (agent profile + override) → 5 parse tools (channel/plan/approval wrapping) → 6 **call agent_loop directly** → 7 persist + finalize (assistant node, title, auto-compact).

**B · runtime.exec**: 1 open the llm node (running, _call_id points to it, wrapped in an outer timeout/retry loop) → 2 build the context [a select history nodes via render_context → b render into messages + current turn → c resolve the toolset toolset/policy/unattended-deny → d assemble system + skills → e create AgentSession (select stream_fn)] → 3 **call agent_loop via AgentSession** → 4 close the llm node (backfill output, success).

**shared · agent_loop**: before each round of calling the model [a convert_to_llm → b memory prefetch → c deferred-tool re-split] → call the model / stream → check tool_use? → yes: run the tool → feed the result back → call the model again; no (plain text): exit the loop.

## Side-by-Side Comparison

| | OpenCode | OpenClaw | Hermes | OpenProgram (after unification) |
|---|---|---|---|---|
| Abstraction for one LLM call | `Step` (one node, tool loop inside) | `runId` pairing (llm_input/llm_output, tool nested inside) | no hierarchy, flat list | `exec` (one llm node, tool loop inside) |
| Recording method | paired: Step.Started → Step.Ended | paired: llm_input hook → llm_output hook | append-only, no marking | paired: write running → backfill output |
| Tool-call attribution | ToolPart inside the Step (sub-part) | nested under the same runId (sub-hook) | peer tool message | the code node's called_by points to the llm node (child node) |
| Chat vs programmatic call | unified (one path) | unified (same hook system) | unified (one run_conversation) | unified (both go through runtime.exec) |

OpenProgram adopts the OpenCode/OpenClaw model: one call = one node, paired writes, tools are child nodes.

## Problems (Current State)

Right now "making one LLM call" has three paths:

| Path | Entry | tool loop | Writes DAG llm node |
|---|---|---|---|
| regular chat | dispatcher → agent_loop | managed by agent_loop | dispatcher writes a SessionDB message (not a DAG llm node) |
| exec legacy | exec → `self._call()` | none | exec writes the llm node |
| exec providers | exec → `session.run` → agent_loop | managed by agent_loop | **not written** (bug) |

### Concrete Symptoms

1. **The exec providers path does not write the llm node**. `_call_via_providers` returns immediately after `session.run` returns, without calling `_append_model_call_node`. wiki_agent takes this path, so in the DAG wiki_agent connects directly to wiki_agent with no LLM node in between.

2. **The dispatcher path writes something other than a DAG llm node**. The dispatcher calls `persist_assistant_message`, which writes a SessionDB assistant message (with token stats), not a DAG `Call(role=llm)` node.

3. **The legacy path has no tool loop**. The model can only return plain text and cannot call tools.

## Design

### Core Principle

One `runtime.exec` = one llm node.

The llm node and the code node are the same abstraction: one call, running on entry, output backfilled on exit. Whatever happens inside (how many rounds the tool loop ran, which tools were called) is an internal process and is not split into multiple nodes.

```
enter exec  → write the llm node (status=running, output=None)
inside exec → agent_loop runs the LLM + tool loop
              (the tool's code node called_by points to this llm node)
exec returns → backfill the llm node (output=final reply, status=success)
```

### Call-Tree Example

The wiki_agent recursion scenario, DAG after unification:

```
llm (dispatcher calls exec, model decides to call wiki_agent)
  code wiki_agent d1 (tool execution)
    llm (wiki_agent internally calls exec, model calls wiki_agent again)
      code wiki_agent d2 (recursion)
        llm (yet another exec)
          code wiki_agent d3
            ...
```

There is an llm node between every two code nodes, so the call chain is complete.

### Unified Path

```
runtime.exec(content=[...])
  → write the llm node (status=running, output=None)
  → build the context (render_context + render_dag_messages + content)
  → call the LLM, run the tool loop until the model returns plain text
      (for tools the model calls inside the tool loop, their code node called_by points to this llm node)
  → backfill the llm node (output=final reply, status=success)
  → return text
```

### Changes Per Layer

**dispatcher**: no longer calls agent_loop directly; instead calls runtime.exec. Retained responsibilities:
- write the user node
- set the session context (ContextVar)
- call runtime.exec
- turn-level cleanup (title generation, compaction trigger)

```
process_user_turn (after change)
  → write the user node (unchanged)
  → set _store / _current_runtime (unchanged)
  → runtime.exec(content=user message)  ← change here
  → finalize
```

**runtime.exec**: unified into a single path.
- legacy `call=my_func` is wrapped into a lightweight provider adapter and uniformly goes through `_call_via_providers`
- `_call_via_providers` gets the llm node write added (paired: write running on entry, backfill on return)
- delete the legacy branch

**agent_loop**: unchanged. A pure "LLM call + tool execution" engine, unaware of the DAG.

## Detailed Analysis of the Current State

### Path 1: Regular Chat (dispatcher)

```
user sends a message
→ process_user_turn (dispatcher/__init__.py:96)
  → write the user node to the DAG (db.append_message)
  → set the _store ContextVar
  → _run_loop_blocking (dispatcher/__init__.py:640)
    → build AgentContext (tools, system_prompt, history)
    → agent_loop([prompt], context, config) (agent_loop.py:232)
      → _stream_assistant_response → LLM reply
      → if tool_use → _execute_tool_calls → tool.execute
        → if the tool is an @agentic_function → the wrapper writes a code node
        → tool returns → agent_loop continues
      → eventually get a plain-text reply
    ← return final_text
  → persist_assistant_message (persistence.py:31)
    → write the assistant message to SessionDB (not a DAG llm node)
  → finalize
```

### Path 2: runtime.exec legacy

```
inside an @agentic_function body, call runtime.exec(content=[...])
→ exec (runtime.py:789)
  → self._call(content) → user-defined function, returns text
  → _append_model_call_node(reply=...) → write the llm node to the DAG
← return text
```

### Path 3: runtime.exec providers

```
inside an @agentic_function body, call runtime.exec(content=[...])
→ exec (runtime.py:789)
  → _call_via_providers (runtime.py:1306)
    → build AgentSession
    → session.run(current) → internally runs agent_loop (same code as Path 1)
    ← return the final assistant message
  → return _assistant_text(final)  ← no llm node written!
← return text
```

## Landing Order

### Steps 1-2 (✅ done)

| Step | What to do | Verification |
|---|---|---|
| 1 | exec writes the llm node in pairs (`_open_model_call_node` / `_close_model_call_node`), both the sync and async paths | ✅ the llm node appears in the wiki_agent session DAG |
| 2 | In `_call_via_providers`, switch `_call_id` to the llm node before session.run, so the tool loop's tool code nodes called_by correctly point to the llm node | ✅ `test_tool_loop_subcall_attributes_to_llm_node` |

### Step 3: Delete the legacy `call=` branch

Wrap `Runtime(call=fn)` into a provider model, uniformly go through `_call_via_providers`, and delete `_call_fn` / `_uses_legacy_call` and the two legacy branches.

| Step | What to do | File | Verification |
|---|---|---|---|
| 3a | Add a `CallableModel` adapter (sync+async, converts pi-ai messages back to content to call the user fn, returns a single AssistantMessage, no tool loop; reinstate `response_format`→prompt suffix) | new file `openprogram/providers/callable_model.py` | `pytest tests/providers/test_functions.py` |
| 3b | In `Runtime.__init__`, `call=` → `self.api_model = CallableModel(call)`; a callable runtime forces `toolset="none"` | `runtime.py` __init__ | `pytest tests/providers/test_functions.py` |
| 3c | Delete the `_call_fn` branch in `_call`/`_async_call` | `runtime.py` | `pytest tests/agentic_programming/` |
| 3d | Delete `_uses_legacy_call` + the two legacy branches in exec/async_exec | `runtime.py` | full `pytest tests/agentic_programming tests/providers` |

**Key finding (verified)**: `_call_via_providers` does not ignore content — `_render_history_messages(content)` treats content as the current turn (`runtime.py:1451`), and with no store, `_build_pi_context(content)` (`:1456`). So the adapter needs no special handling of content; AgentSession hands history+current to the model, and the adapter converts back to content to call the user fn.

### Step 4: stream_fn injection (✅ done); dispatcher is not folded in (argued out)

| Step | What to do | Status |
|---|---|---|
| 4a | Thread the `stream_fn` parameter through exec → _call_via_providers → AgentSession.__init__ → AgentOptions, so exec can inject a stream | ✅ `test_exec_stream_fn_injection` |

**dispatcher is not folded into runtime.exec (argued out empirically)**

The initial idea was to have `_run_loop_blocking` call runtime.exec instead. In practice this direction was overturned:

1. **The model would fork**: the dispatcher uses `_resolve_model(agent_profile, req.model_override)` (agent profile + the model the user picked), whereas the attached runtime comes from `create_runtime()` with no args via auto-detection, so the `api_model` is inconsistent. Going through exec would use the wrong model.
2. **The context would conflict**: the dispatcher uses the context-engine to prepare messages (`prep.agent_messages`), while exec renders history from the DAG itself; the two sets would clash.
3. **The streaming events are incompatible**: exec's `on_stream` emits a flat dict, while the dispatcher's `on_event` expects a webui envelope.
4. **Most critical — duplicate nodes would be written**: trying to add an llm node in the dispatcher with `_open/_close_model_call_node` produced `[user, assistant, assistant]` in the DAG — because **the dispatcher's assistant session message (`persist_assistant_message`) is itself the DAG record of the top-level LLM call**. Adding another llm node duplicates it. `test_dispatcher_integration.py::test_real_loop_text_only` caught this duplication directly.

**Conclusion**: the dispatcher's top-level LLM call **already** has a DAG representation (the role=assistant session-message node), with tool calls hanging below it. There is no need — and it would be wrong — to add another llm node. The original "code→code missing an llm node" problem occurs only in the **tool loop of exec inside an `@agentic_function`**, which was already fixed by steps 1-2. The dispatcher stays as is.

The relationship between dispatcher and exec is not "dispatcher goes through exec" but rather **two parallel LLM-call entry points**, each recording its top-level call into the DAG (dispatcher → assistant session node; exec → llm node), sharing the lower agent_loop engine and the DAG node-write API. This is consistent with other frameworks (OpenClaw also has a separate dispatcher layer).

### The Agent Class: Left Alone

`Agent` sits below exec (exec → AgentSession → Agent → agent_loop); it is a loop driver, not a parallel LLM path, and is constructed in only one place (`session.py:94`). Folding it would amount to rewriting the loop, with no payoff.

### Event Layer: No Changes

`tool.before` fires in only one place, `_execute_tool_calls` (`agent_loop.py:518`), and all paths go through it. CallableModel has no internal tool loop (the user fn only returns a str), so it will not duplicate or lose events.

### Risks

1. **dispatcher test seam (highest)**: after 4a threads stream_fn through 4 layers, `_run_loop_blocking` is still the patch entry, and the test's stream_fn flows through exec to the model. `test_dispatcher_dag_attach.py` replaces `_run_loop_blocking` wholesale and is unaffected.
2. **prompt-cache prefix stability** (4b): the override must not break the DAG-prefix cache.
3. **response_format regression** (3a): the JSON-mode of `claude_call`/`gemini_call` relies on the adapter to reinstate the suffix.

### Tests to Update

**Trivial (delete an assert/override)**: `test_openai.py:61`, `test_anthropic.py:63`, `test_gemini.py:68` (delete `_uses_legacy_call() is False`); `test_decision.py:24`, `test_loop_options.py:153`, `test_dispatcher_dag_attach.py:89` (delete the `_uses_legacy_call→True` override, keep the `_call` override).

**Behavioral (need re-verification)**: `test_runtime_exec_dag.py:34`, the `_mock_call` in `test_functions.py` (branches on content shape; the adapter must pass content through verbatim), the `echo_call`/`noop_call` in `conftest.py`.

## Unifying the "record one model reply" write (keep the new primitive, delete the old mechanism)

### Current State: Two Ways to Write the Same Thing

"Recording a model reply" currently has two **paired node-write** implementations (open a running placeholder → backfill the result), split by entry point:

| Entry | Open placeholder | Backfill | Implementation |
|---|---|---|---|
| dispatcher (chat) | `insert_placeholder` (_turn_lifecycle.py:65) | `persist_assistant_message` (persistence.py) | old mechanism |
| exec (code) | `_open_model_call_node` (runtime.py:631) | `_close_model_call_node` (runtime.py:656) | new primitive |

The two have identical structure (open→close pairing), but the old one is scattered across the dispatcher and the new one lives in runtime. This is the root of "writing both head and tail twice".

### Key Facts (That Make Unification Easy)

The storage layer **has no `ROLE_ASSISTANT`** — only user/llm/code (`context/nodes.py:36-40`). The chat reply is **already stored as `ROLE_LLM`**: `_msg_to_node` maps `role="assistant"` to `ROLE_LLM`, and `_node_to_msg` reads it back and defaults to restoring it to `"assistant"` (`_msg_adapter.py`). exec's `_open_model_call_node` also writes `ROLE_LLM`, and it too reads back to "assistant" by default.

**The two primitives already use the same role at the node layer.** The only difference is that the dispatcher writes 4 extra pieces of metadata that exec's bare primitive does not. Serialization has a single chokepoint `_node_to_msg` → what the front-end reads is still "assistant".

### Plan: Upgrade the New Primitive → Have the dispatcher Call It → Delete the Old Mechanism

Upgrade exec's paired primitive into a **general paired primitive** (able to hold the fields the dispatcher needs), have both entry points use it, and delete the old duplicated placeholder/persist mechanism.

The unified primitive must retain these 4 things (otherwise the front-end / branching / metering will break):

| Field | Who needs it | Risk |
|---|---|---|
| `extra.blocks` (the order of thinking/text/tool cards) | the front-end bubble body (conv-mapper.ts) | highest |
| token columns + token_model | metering UI | high |
| `metadata.parent_id=user_msg_id` | active-branch rebuild for branch/fork/rewind | high |
| `cancelled/completed` terminal state (exec currently writes success) | partial output when the user stops | medium |

Primitive signature upgrade (optional params; exec doesn't pass them, dispatcher does):

```python
open_model_call_node(*, role="llm", parent_id=None, content_text="", model=None) -> node_id
close_model_call_node(node_id, *, reply, status="success", blocks=None, usage=None)
```

Landing order:
1. Upgrade `_open/_close_model_call_node`, adding optional `parent_id` / `blocks` / `usage` / `status` params (exec calls unchanged, default behavior unchanged)
2. Change the dispatcher's `insert_placeholder` to call `open_model_call_node(role="assistant", parent_id=user_msg_id)`
3. Change the dispatcher's `persist_assistant_message` to call `close_model_call_node(blocks=..., usage=..., status="completed"/"cancelled")` — keep only the field assembly, delete its own append/update
4. Delete the duplicated node-write logic in the placeholder/error-fold of `_turn_lifecycle.py`
5. Verify: webui end-to-end chat (bubbles, thinking/tool cards, tokens, stop, fork) all working + full pytest

Zero front-end changes — because `_node_to_msg` still outputs "assistant", the fields the front-end reads (blocks/token/parent_id) are all still there, just written by the unified primitive instead.

## Related Files

- `openprogram/agentic_programming/runtime.py` — exec / _call_via_providers / _open|_close_model_call_node (where the unified primitive lives)
- `openprogram/providers/callable_model.py` — CallableModel adapter
- `openprogram/agent/agent_loop.py` — agent_loop / _execute_tool_calls
- `openprogram/agent/session.py` — AgentSession
- `openprogram/agent/dispatcher/__init__.py` — process_user_turn / _run_loop_blocking
- `openprogram/agent/dispatcher/persistence.py` — persist_assistant_message (switched to call the unified primitive)
- `openprogram/agent/internals/_turn_lifecycle.py` — insert_placeholder / fold_error (delete the duplicated writes)
- `openprogram/store/session/_msg_adapter.py` — _node_to_msg (the serialization chokepoint, role restoration)
- `openprogram/agentic_programming/function.py` — @agentic_function wrapper
