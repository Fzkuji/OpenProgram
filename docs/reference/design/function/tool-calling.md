# How the Model Picks the Next Step (the Tool-Calling Loop)

This document describes how, within a single model invocation, the LLM "makes a choice" on each turn — whether it picks a function to run, or emits text and finishes.

> Companion document: [`function-calling-unification.md`](./function-calling-unification.md)
> It describes the design of the entire function-calling framework — the two decorators
> `@function` / `@agentic_function`, the shared registry, the 6 gating layers, deferred loading, and so on. This document
> only covers the loop mechanism of the "pick the next step" stage.

## In One Sentence

Give the LLM a set of tools (`@agentic_function` or tool dicts), and on each turn it returns one assistant message. **If the message content contains a `ToolCall`, the model has picked a function** — the framework executes it, feeds the result back into the history, and lets the model pick again on the next turn; **if there is only text and no `ToolCall`, the model has picked "finish"** — the text is returned as the final reply. This loop runs in `openprogram/agent/agent_loop.py::_run_loop`.

## Entry Point: `runtime.exec`

Inside an `@agentic_function`, you call `runtime.exec(content, tools=..., tool_choice=..., max_iterations=...)`:

- `tools` is the list of functions the LLM may pick from. Each item can be an `@agentic_function`, a `{"spec":..., "execute":...}` dict, or an object with `.spec`/`.execute`.
- **Tools are opt-in.** When you pass neither `tools=` nor `toolset=`, the tools the LLM receives are `None` — this is a pure-inference call, the LLM has no functions to pick from and can only emit text. To let it "pick a function", you must explicitly pass `tools=[...]` or `toolset="default"`.
- When `tools` is passed, `exec` enters the tool loop, continuing until the model returns plain text or hits `max_iterations` (default 20).

`tool_choice` controls, for this turn, "whether picking is allowed / whether picking is required":

```
"auto" (default)                    the model decides on its own whether and which to call
"required"                          this turn must pick a function; emitting plain text is not allowed
"none"                              this turn may not pick a function; only text may be emitted
{"type":"function","name":"X"}      force the pick of function X
```

`parallel_tool_calls` (default `True`) lets the model pick multiple functions at once in a single turn.

## The Loop Body: `_run_loop`

`_run_loop` contains an inner `while has_more_tool_calls or pending_messages`, and on each turn:

1. **Get the model's output for this turn** — `_stream_assistant_response` streams the call to the provider and returns one `AssistantMessage`.
2. **Check for a terminating error** — `message.stop_reason in ("error","aborted")` → end the stream directly, no more looping.
3. **Check what the model picked** —
   ```python
   tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
   has_more_tool_calls = len(tool_calls) > 0
   ```
   - `tool_calls` non-empty → the model picked a function → proceed to step 4, then return to the top of the loop to pick again next turn.
   - `tool_calls` empty → the model emitted only `TextContent` this turn → `has_more_tool_calls=False` → the inner while exits → this text is the result.
4. **Execute the picked functions** — `_execute_tool_calls` runs them one by one, producing `ToolResultMessage`s that are appended to `current_context.messages` and `new_messages`. The history the LLM sees on the next turn now carries the tool results, and it decides what to pick next based on them.

In other words: **"picking the next step" is not a standalone decision module; it is a binary choice between `ToolCall` and `TextContent` within the assistant message the provider returns.** The framework does not decide for the model — it only parses the model's output and routes accordingly.

## Function Execution: `_execute_tool_calls`

For each `ToolCall` the model picks, it looks up the corresponding tool in `tools` by `tool_call.name`:

```
tool not found                          → ValueError, produce an is_error result
validate_tool_arguments fails           → exception, produce an is_error result
tool.execute(...) raises                → caught, the exception text becomes the is_error result
normal                                  → the result content is wrapped into a ToolResultMessage
```

Neither validation nor execution exceptions interrupt the loop — they become a single `is_error=True` tool result fed back to the model, letting the model see "this function was the wrong pick / the arguments were wrong" and correct itself.

When multiple functions are picked in parallel, they execute one by one in order. If, partway through, `get_steering_messages` returns new user-inserted messages, the remaining unexecuted `ToolCall`s are marked by `_skip_tool_call` as "Skipped due to queued user message", and the user messages are handled first.

## Termination Conditions

The inner selection loop stops on any of the following:

```
the model picked no function this turn (plain text)   normal end, the text is the result
stop_reason = error / aborted                          exception/cancellation end
inner_iterations > 50                                  hard cap MAX_INNER_ITERATIONS, prevents the model from spinning;
                                                       handled as a "normal end", returns the content already produced
exec-layer max_iterations (default 20)                 exec's own safety cap on the tool loop
```

After the inner loop exits, if `get_follow_up_messages` has follow-up messages, they are set as `pending_messages` and another round begins; if not, it ends completely and pushes `AgentEventAgentEnd`.

## Relationship to `@agentic_function`

An `@agentic_function` passed as a tool to `exec(tools=[...])` is, in the model's eyes, just a selectable function. The model picks it → `_execute_tool_calls` calls its `.execute` → if that function internally calls `runtime.exec` again, it opens another layer of the same selection loop. "Picking the next function to run" is the same single mechanism unrolled recursively across nested layers of agentic functions.
