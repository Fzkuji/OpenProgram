# How the model picks the next step (the tool-call loop)

This document describes how, within one model call, the LLM "chooses" on
every round — pick a function to run, or emit text and finish.

> Companion doc: [`function-calling-unification.md`](../../../reference/design/function/function-calling-unification.md)
> covers the design of the whole function-calling framework — the
> `@function` / `@agentic_function` decorators, the shared registry, 6-layer
> gating, deferred loading, etc. This page only covers the loop mechanics of
> the "pick the next step" part.

## One-sentence summary

Give the LLM a set of tools (`@agentic_function`s or tool dicts); each round
it returns one assistant message. If the message content **contains a
`ToolCall`, it picked a function** — the framework executes it, feeds the
result back into the history, and lets it pick again. If the message is
**text only, with no `ToolCall`, it picked "finish"** — the text is returned
as the final reply. The loop runs in
`openprogram/agent/agent_loop.py::_run_loop`.

## Entry point: `runtime.exec`

Inside an `@agentic_function`, call
`runtime.exec(content, tools=..., tool_choice=..., max_iterations=...)`:

- `tools` is the menu of functions the LLM may pick from. Each entry can be
  an `@agentic_function`, a `{"spec":..., "execute":...}` dict, or an object
  with `.spec` / `.execute`.
- **Tools are opt-in.** With neither `tools=` nor `toolset=` passed, the LLM
  gets `None` for tools — a pure reasoning call where the LLM has no function
  to pick and can only emit text. To let it "pick a function", you must pass
  `tools=[...]` or `toolset="default"` explicitly. One caveat: a nested
  `exec` inside a tool body inherits the outer call's tools (via the
  `_current_tools` contextvar), so it is not automatically tool-free.
- To trim the tool menu, `exec` also takes the policy parameters
  `tools_source`, `tools_allow`, and `tools_deny`.
- With `tools` set, `exec` enters the tool loop until the model returns pure
  text (or the loop's hard cap is hit — see [Termination](#termination)).

`tool_choice` controls whether picking is allowed / required per round —
`"auto"` (default: the model decides), `"required"` (must pick a function),
`"none"` (text only), or `{"type": "function", "name": "X"}` to force one
function. It is forwarded to the provider, which maps it onto its own
protocol shape (OpenAI, Anthropic, Gemini, and Bedrock are covered).
`parallel_tool_calls=False` forbids several picks in one round where the
provider supports the knob. `max_iterations` caps the loop's rounds — the
effective cap is `min(50, max_iterations)` (see
[Termination](#termination)). For a forced, structured decision *ending*
(rather than per-round control), `exec(choices=...)` remains the richer
tool — see [next-step decision](./next-step-decision.md).

## Loop body: `_run_loop`

`_run_loop` has an inner `while has_more_tool_calls or pending_messages`;
each round:

1. **Get the model's output for this round** — `_stream_assistant_response`
   streams from the provider and returns one `AssistantMessage`.
2. **Check for terminal errors** — `message.stop_reason in ("error",
   "aborted")` → end the stream immediately, no more looping.
3. **See what the model picked** —
   ```python
   tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
   has_more_tool_calls = len(tool_calls) > 0
   ```
   - `tool_calls` non-empty → the model picked functions → go to step 4,
     then back to the top of the loop to pick again.
   - `tool_calls` empty → the model emitted only `TextContent` this round →
     `has_more_tool_calls=False` → the inner while exits → that text is the
     result.
4. **Execute the picked functions** — `_execute_tool_calls` runs them one by
   one, producing `ToolResultMessage`s appended to
   `current_context.messages` and `new_messages`. The history the LLM sees
   next round now carries the tool results, and it decides what to pick
   next based on them.

In other words: **"picking the next step" is not a separate decision module
— it is the `ToolCall`-vs-`TextContent` dichotomy inside the assistant
message the provider returns.** The framework never decides for the model;
it only parses the output and branches on it.

## Function execution: `_execute_tool_calls`

For each `ToolCall` the model picked, look up the tool in `tools` by
`tool_call.name`:

```
tool not found                        → ValueError, produces an is_error result
validate_tool_arguments fails         → exception, produces an is_error result
tool.execute(...) raises              → caught; the exception text becomes an is_error result
success                               → result content wrapped in a ToolResultMessage
```

Neither validation nor execution exceptions break the loop — they become an
`is_error=True` tool result fed back to the model, so it can see "wrong
function / wrong arguments" and correct itself.

Parallel picks execute sequentially in order. If `get_steering_messages`
returns user-queued messages mid-way, the remaining unexecuted `ToolCall`s
are marked by `_skip_tool_call` as "Skipped due to queued user message" and
the user messages take priority.

## Termination

The inner picking loop stops on any of:

```
model picked no function (pure text)   normal finish; the text is the result
stop_reason = error / aborted          error / cancel finish
inner_iterations > 50                  hard cap MAX_INNER_ITERATIONS against idle spinning;
                                       treated as a normal finish, returns what exists
```

One continuation condition is easy to miss: the inner `while` also runs on
`pending_messages`, so queued user (steering) messages keep the loop alive
even after a pure-text reply.

After the inner loop exits, `get_follow_up_messages` may supply follow-up
messages which become `pending_messages` for another round; otherwise the
run ends for good and pushes `AgentEventAgentEnd`.

## Relation to `@agentic_function`

An `@agentic_function` passed as a tool to `exec(tools=[...])` is, in the
model's eyes, just one pickable function. The model picks it →
`_execute_tool_calls` invokes its `.execute` → if that function body calls
`runtime.exec` again, another layer of the same picking loop opens.
"Picking the next function to run" under nested agentic functions is the
same mechanism unfolding recursively.
