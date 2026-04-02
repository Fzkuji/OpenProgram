# Framework Review v4

## Bottom line

The docs and code do not currently describe the same framework.

`docs/DESIGN.md` still describes a much larger system built around `Session`, `Scope`, `Memory`, MCP exposure, and a self-hosting meta-function. The implementation in `agentic/` is a much smaller MVP: a decorator that builds a `Context` tree and a `runtime.exec()` helper that wraps an arbitrary LLM call. That simplification could be a good direction, but the project has not actually rewritten its design around it. Right now the repo reads like two incompatible versions spliced together.

The harsh version: this is not an implementation of the architecture in `DESIGN.md`. It is an instrumentation layer around prompt execution.

## Highest-priority findings

### 1. There is no real run lifecycle, so "automatic tracking" breaks at the top level

References: `agentic/function.py:60`, `agentic/function.py:73`, `agentic/function.py:85`, `agentic/context.py:229`, `agentic/context.py:239`, `docs/CONTEXT-v3.md:205`

If a decorated function is called without `init_root()`, its `Context` exists only while that function is active. After the wrapper resets the `ContextVar`, `get_root_context()` returns `None`.

That means:

- The persistence example in `docs/CONTEXT-v3.md` is incomplete and misleading.
- The claim that users "write normal Python and everything is tracked automatically" is not fully true.
- The framework has no coherent notion of "a run" unless the user knows about an internal tree root and manually initializes it.
- Even when `init_root()` is used, the root context itself is never finalized, so the framework still lacks a clean run boundary.

This is a design hole, not a documentation nit.

### 2. One `Context` can only remember one LLM call

References: `agentic/context.py:42`, `agentic/context.py:44`, `agentic/runtime.py:64`, `agentic/runtime.py:78`

`runtime.exec()` writes to `ctx.input`, `ctx.media`, and `ctx.raw_reply` as single fields. A second `runtime.exec()` inside the same function silently overwrites the first one.

This is a serious structural problem because nontrivial agentic functions often need:

- plan -> critique -> revise
- observe -> ask follow-up -> decide
- parse failure -> retry with clarification

The current data model cannot represent that. It assumes one function call maps to one LLM exchange, and later `runtime.exec()` calls inside the same function cannot see earlier ones because `summarize()` only looks at parent/sibling state.

### 3. `DESIGN.md` is not mildly stale; it is mostly a document for a different codebase

References: `docs/DESIGN.md:20`, `docs/DESIGN.md:157`, `docs/DESIGN.md:207`, `docs/DESIGN.md:300`, `docs/DESIGN.md:337`, `docs/DESIGN.md:371`, `docs/DESIGN.md:486`

Sections 1-11 describe components that do not exist in the implementation:

- `Session`
- `Scope`
- `Memory`
- `Agentic Type`
- MCP server integration
- meta function bootstrapping
- built-in `ask/extract/summarize/classify/decide`

Then section 12 abruptly describes the current tiny `agentic/` package. That makes `DESIGN.md` internally contradictory.

### 4. `@agentic_function` is sync-only, but it fails unsafely on `async def`

References: `agentic/function.py:53`, `agentic/function.py:75`

Decorating an `async def` returns a coroutine object, marks the context as `success`, stores the coroutine object as `output`, and leaves the coroutine un-awaited. That is worse than "async not supported" because it looks successful while being wrong.

This should either:

- support async explicitly, or
- reject coroutine functions at decoration time with a hard error

### 5. `summarize()` is not a trustworthy visibility policy

References: `agentic/context.py:53`, `agentic/context.py:69`, `agentic/context.py:88`, `agentic/context.py:107`

The docs present `expose` as a visibility control. The implementation lets `summarize(level=...)` override every sibling's `expose` value. In practice:

- a `"silent"` child can be made visible by passing a level override
- a `"summary"` child can be expanded to `"trace"`
- `summary_fn` bypasses the built-in level logic entirely

So `expose` is not a policy boundary. It is only a default rendering hint.

### 6. The provider abstraction is leaky and under-designed

References: `agentic/runtime.py:32`, `agentic/runtime.py:69`, `agentic/runtime.py:72`, `agentic/runtime.py:84`

`runtime.exec()` claims users can inject any LLM provider through `call`, but the framework first converts everything into one ad hoc message format and then expects the provider callback to understand it.

That is not actually provider-agnostic. It just moves the mismatch outward.

### 7. Several docstrings and error messages still reference older names

References: `agentic/context.py:5`, `agentic/context.py:41`, `agentic/runtime.py:132`, `agentic/runtime.py:135`

The code still mentions `llm_call()` and `_api_fn`, which do not exist in the public API. That is a small symptom of a broader issue: the current architecture has not been fully renamed or conceptually cleaned up.

## 1. Design coherence

Short answer: no, the docs and code do not tell the same story.

What still matches:

- "docstring = prompt" is real enough in spirit, even though nothing enforces it
- Python remains the control-flow language
- `@agentic_function` plus `runtime.exec()` does express "Python + LLM cooperate inside one function"
- the `Context` tree fits the idea that execution history should be inspectable

What does not match:

- `DESIGN.md` centers the system around `Session`, but the code has no session abstraction at all
- `DESIGN.md` treats `Scope` as the visibility model; the code uses `Context.summarize()` plus `expose`
- `DESIGN.md` treats `Memory` as a persistent event log; the code has a recursive tree dump
- `DESIGN.md` shows MCP as the transport boundary; the code has no MCP server
- `DESIGN.md` describes typed outputs, validation, retries, and parse failures; the code returns a raw string from `runtime.exec()`
- `DESIGN.md` claims self-evolving meta-function bootstrapping; the code has nothing close

The deepest coherence problem is this:

- the docs describe a platform
- the code implements a utility library

Those are different scopes, different abstractions, and different expectations.

## 2. What in `DESIGN.md` is outdated

Most of sections 1-11 are outdated relative to the current implementation.

### Section 1: Core Concepts

References: `docs/DESIGN.md:9`, `docs/DESIGN.md:20`

Outdated parts:

- `Agentic Session`
- `Agentic Scope`
- `Agentic Memory`
- `Agentic Type`
- MCP server as "single entry point"

Current code has none of these. The actual core concepts are closer to:

- `@agentic_function`
- `Context`
- `runtime.exec()`

### Section 2: Architecture Overview

References: `docs/DESIGN.md:37`, `docs/DESIGN.md:52`, `docs/DESIGN.md:68`

The whole diagram is outdated. There is no MCP server, no session layer, no scope object, and no memory subsystem in code.

### Section 3: Agentic Function

References: `docs/DESIGN.md:88`, `docs/DESIGN.md:127`, `docs/DESIGN.md:145`

Partially valid:

- dual-runtime idea
- docstring-as-prompt idea

Outdated:

- `session.send(prompt)` flow
- schema validation and retry loop
- `@function(return_type=...)`
- examples using `Session`
- built-in functions table

### Section 4: Meta Agentic Function

References: `docs/DESIGN.md:157`

Entire section is outdated. No meta-function exists.

### Section 5: Agentic Session

References: `docs/DESIGN.md:207`

Entire section is outdated. No session classes exist.

### Section 6: Agentic Scope

References: `docs/DESIGN.md:300`

Entire section is outdated. There is no `Scope` object, no `depth/detail/peer/compact` API, and no presets.

### Section 7: Agentic Memory

References: `docs/DESIGN.md:337`

Mostly outdated. The current code does have persistence, but it is not a memory/event-log system. It is just serialization of the context tree.

Missing from implementation:

- event stream
- run start / run end events
- message sent / received events
- media copying
- markdown report format described in the doc

### Section 8: MCP Integration

References: `docs/DESIGN.md:371`

Entire section is outdated. No MCP layer exists.

### Section 9: Execution Modes

References: `docs/DESIGN.md:416`

Only the "static" mode maps to current code. "dynamic" and "self-evolving" are design aspirations, not implementation.

### Section 10: Design Principles

References: `docs/DESIGN.md:445`

Still true:

- functions are functions
- docstring = prompt
- Python is the control flow

Not currently true:

- scope is intent
- sessions are pluggable
- meta bootstraps everything
- MCP is the transport

### Section 11: Comparison

References: `docs/DESIGN.md:475`

Partially outdated. The comparison still reflects the philosophy, but the current code does not implement explicit context via `Scope` or self-evolving behavior.

### Section 12: Project Structure

References: `docs/DESIGN.md:486`

This is the only section that clearly reflects the current package. Ironically that makes the rest of the document look even more stale.

My blunt recommendation: split this file into either:

- a "current architecture" doc for the actual code, and a separate "future vision" doc

or:

- a completely rewritten `DESIGN.md` for the simplified framework

Right now it should not be treated as source of truth.

## 3. Context system review

## What is good

- Using `ContextVar` is the right primitive for implicit call-stack tracking. It is better than a bare global.
- A tree of function calls is the right base shape for nested agentic workflows.
- Keeping the data structure small makes the model understandable.
- `tree()`, `traceback()`, and `save()` are useful developer ergonomics for an MVP.

## What is weak

### `Context` is carrying too many responsibilities

References: `agentic/context.py:20`

It currently acts as all of these at once:

- execution record
- parent/child tree node
- LLM request/response holder
- summarization policy container
- renderer
- persistence model

That is acceptable for a prototype, but it is not a stable design boundary. The first sign of trouble is already here: one function may need multiple LLM calls, but the single-node schema cannot represent them.

### The field design is too loose

References: `agentic/context.py:29`, `agentic/context.py:34`, `agentic/context.py:39`

Problems:

- `status` is a free string instead of an enum or `Literal`
- `expose` is a free string instead of an enum or `Literal`
- `input` is vague and conflicts conceptually with Python call params
- `children: list` and `media: Optional[list]` are weakly typed
- `error` stores only a string, losing exception type and traceback

### `summarize()` is not the right core API in its current form

References: `agentic/context.py:53`

As a convenience method, it is fine. As the core context API, it is underpowered and misleading.

Problems:

- it returns one hardcoded string format
- it only sees the immediate parent and previous siblings
- it does not include the current node's own prompt/params despite claiming to in the docstring
- it has no structured output form for providers that want system/developer/user separation
- `max_tokens` is really a rough character budget
- the method name suggests "summarize this context", but it is really "render visible surrounding history for prompt injection"

If context is important, the framework should separate:

- recorded data
- visibility policy
- rendering for LLM input

### `expose` levels are useful, but only as display hints

References: `docs/CONTEXT-v3.md:120`, `agentic/context.py:112`

The level set is reasonable:

- `trace`
- `detail`
- `summary`
- `result`
- `silent`

This is a useful compression ladder. The problem is semantics, not vocabulary.

If `expose` means "how much of this call may be shown to other calls", it currently fails.

If `expose` means "default rendering style when building summaries", it is acceptable.

I would rename it to something like `visibility` or `share_level` only if you plan to make it a real policy. Otherwise `render_level` is more honest.

## What I would change

- Keep `Context` as the call-tree node.
- Add an `events: list[LLMCall]` or `llm_calls: list[LLMCall]` field for each `runtime.exec()` invocation.
- Make `status` and `expose` typed enums.
- Move prompt rendering into a separate renderer function or class.
- Replace `init_root()` with a proper run object or context manager.

Example direction:

```python
@dataclass
class LLMCall:
    prompt: str
    input: dict[str, Any] | None
    images: list[str]
    context_text: str | None
    schema: dict[str, Any] | None
    raw_reply: str
    model: str

@dataclass
class Context:
    ...
    llm_calls: list[LLMCall] = field(default_factory=list)
```

That one change would remove several current design distortions.

## 4. `runtime.exec` review

## Module-level function

As a convenience API, `runtime.exec()` is fine.

As the main extension boundary, it is too thin.

Why it works:

- easy to call
- easy to explain
- consistent with the "functions are functions" philosophy

Why it will age badly if left as-is:

- every new feature becomes another keyword argument
- provider configuration has nowhere clean to live
- retry policy has nowhere clean to live
- tracing policy has nowhere clean to live
- output parsing and validation have nowhere clean to live

My recommendation:

- keep `runtime.exec()` as the ergonomic facade
- back it with a configurable runtime/provider object

Example shape:

```python
class Provider(Protocol):
    def complete(self, request: CompletionRequest) -> CompletionResponse: ...
```

Then `runtime.exec()` can delegate to a default provider/runtime instance.

## The `call` parameter

The current `call` hook is useful for testing and experimentation. It is not a clean provider interface.

Problems:

- it is typed as `Any`
- it must understand the framework's ad hoc message format
- it has no access to a structured request object
- it forces the framework to guess a universal message schema before provider adaptation

If you want pluggable providers, the clean interface is:

- framework builds a structured request
- provider adapter turns that into Anthropic/OpenAI/CLI-specific payloads

Not:

- framework invents one pseudo-provider message list
- arbitrary callback reverse-engineers it

## `_build_messages()` specifically

References: `agentic/runtime.py:84`

This is the most obviously leaky part of the runtime design.

Problems:

- fake assistant `"Understood."` message is arbitrary prompt engineering baked into infrastructure
- images are only inserted as text placeholders
- schema is appended as another user message instead of using provider-native structured output modes
- there is no system/developer/user distinction

This is acceptable as scaffolding, but not as a public abstraction.

## 5. Missing pieces and design gaps

These are the missing pieces that matter even if you keep the simplified architecture.

### Essential gaps

- Run lifecycle. There should be an explicit `start_run()/end_run()` or a context manager that owns the root context.
- Multiple LLM calls per function. This is the biggest missing data-model piece.
- Provider abstraction. A real protocol or adapter layer is needed.
- Structured output handling. `schema` exists in the signature but there is no parsing, validation, retry, or typed return path.
- Actual image support. Right now `images` is mostly decorative.
- Better persistence. Save should create directories, include timestamps, and support a durable run artifact.
- Error capture. Exception type and traceback should be stored, not just `str(e)`.
- Async support or explicit async rejection.

### Important but secondary gaps

- Tests for nested calls, multiple `runtime.exec()` calls, errors, and async misuse
- Configurable truncation/budget policy for summaries
- A clearer story for whether context visibility is a debugging feature or an isolation policy
- A real markdown report format if `save(".md")` is part of the public surface

### Optional future-platform gaps

These only matter if the project still wants the original broader vision:

- sessions
- scope policies
- event-log memory
- MCP exposure
- meta-function bootstrapping

Those should be treated as roadmap, not current design.

## 6. Naming review

## Clear enough

- `Context`
- `agentic_function`
- `runtime`
- `get_context`
- `get_root_context`

## Weak or misleading

### `exec`

References: `agentic/runtime.py:32`

`exec` collides with Python's built-in `exec`, which is already heavily loaded semantically. It is short, but not especially clear.

Better options:

- `runtime.call()`
- `runtime.invoke()`
- `runtime.complete()`

### `expose`

References: `agentic/context.py:39`

If this is a rendering hint, `render_level` is clearer.

If this is a visibility policy, `visibility` or `share_level` is clearer.

`expose` sounds half like serialization, half like security, and the code does not enforce either cleanly.

### `input`

References: `agentic/context.py:42`, `agentic/runtime.py:34`

There are two different "inputs":

- function call params
- LLM payload

Calling the second one `input` is vague. `llm_input`, `request_data`, or `payload` would be clearer.

### `call`

References: `agentic/runtime.py:39`

Too generic. `provider`, `invoke`, `transport`, or `client` would better communicate intent.

### `init_root`

References: `agentic/context.py:239`

This exposes internal tree terminology instead of user intent. If users must call it, the name should speak in run semantics.

Better options:

- `start_run()`
- `begin_trace()`
- `trace_run()`

## 7. Code quality, bugs, and improvements

### Concrete bugs

1. Top-level context is lost without `init_root()`.
   References: `agentic/function.py:60`, `agentic/function.py:85`, `agentic/context.py:229`

2. Repeated `runtime.exec()` calls overwrite earlier recorded LLM data.
   References: `agentic/runtime.py:64`, `agentic/runtime.py:78`

3. `async def` functions are mishandled and produce false-success contexts.
   References: `agentic/function.py:53`, `agentic/function.py:75`

4. `init_root()` creates a root context with `status="running"` and `start_time`, but nothing ever closes it.
   References: `agentic/context.py:239`

5. `save()` fails if parent directories do not already exist.
   References: `agentic/context.py:190`

6. `summarize()` claims to include the current function's prompt and params, but it does not.
   References: `agentic/context.py:63`, `agentic/context.py:76`

7. `_default_api_call()` error text references nonexistent `_api_fn` and `llm_call()`.
   References: `agentic/runtime.py:132`, `agentic/runtime.py:135`

### Edge cases and weaker points

1. `inspect.signature.bind()` raises before a `Context` is created, so argument-binding failures are not tracked.
   Reference: `agentic/function.py:55`

2. `tree()` and `_render()` can dump large or multiline values directly into output, which will get messy quickly.
   References: `agentic/context.py:107`, `agentic/context.py:157`

3. `traceback()` prints the whole subtree, not a focused failure chain.
   References: `agentic/context.py:170`, `agentic/context.py:183`

4. `save(".md")` writes only `tree()`, which is much weaker than the markdown artifact described in `DESIGN.md`.
   Reference: `agentic/context.py:192`

5. `__init__.py` uses `from agentic import runtime` instead of a relative import. It probably works, but it is unnecessarily awkward inside the package itself.
   Reference: `agentic/__init__.py:14`

6. `_is_agentic` and `_expose` are attached to wrappers but not used anywhere in the package.
   References: `agentic/function.py:87`, `agentic/function.py:88`

### Improvements I would make first

1. Introduce a real run API, probably as a context manager.
2. Change `Context` to record a list of LLM call events.
3. Add async-aware decoration or explicit rejection.
4. Introduce a typed provider protocol and structured request object.
5. Rewrite `DESIGN.md` to describe the current simplified framework, and move the old architecture into a roadmap doc.

## Final assessment

The current implementation has a decent small core:

- implicit context propagation via `ContextVar`
- a decorator-based tracing model
- a simple runtime call surface

But the design is not yet clean enough to deserve the ambition of the docs. The repo currently promises a platform and delivers a prototype. The fix is not to resurrect all the old abstractions. The fix is to decide what this project actually is now, then align the code and docs around that choice.

If the goal is the simplified framework, lean into it and make it solid:

- one honest architecture doc
- a real run lifecycle
- multiple LLM calls per function
- typed provider adapters
- structured output handling

Until that happens, the biggest problem is not missing code. It is conceptual drift.
