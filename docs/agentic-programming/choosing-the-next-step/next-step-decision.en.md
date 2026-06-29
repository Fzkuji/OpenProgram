# Next-step decision making (decision.make / exec(choices=))

This document describes OpenProgram's **next-step decision** mechanism: an
agentic function hands "what happens next" to the LLM — give it a set of
options, it picks one, and the framework resolves that pick directly into
"the result of the next step". This mechanism is a separate path from
provider-native tool calls.

It lives in the framework at `openprogram/agentic_programming/decision.py`.
Two entry points share the same option shapes and parsing:

- `decision.make(prompt, options)` — pure decision; the model does no work,
  it just picks.
- `runtime.exec(..., choices=options)` — the model first runs a full turn
  (reasoning, tool calls), and only the closing move is a decision.

`decision.make` needs a runtime to issue the model call, but the runtime is
taken automatically from the `_current_runtime` ContextVar. That ContextVar
is only set when a function on the call chain declares a runtime-class
parameter (`runtime` / `exec_runtime` / `review_runtime`) — an entry-point
`@agentic_function` without one makes `decision.make` raise `RuntimeError`.
So declare `runtime=None` on the function and you do not pass it on; only
outside an agentic function do you pass `runtime=` explicitly.

## Versus native tool calls

| | Native tool call | Next-step decision (this mechanism) |
|---|---|---|
| How options reach the model | the provider protocol's `tools` field | a text menu inside the prompt |
| How the model expresses its pick | protocol-level structured `ToolCall` | a JSON snippet in the reply body |
| Who parses | provider / agent_loop | `decision.py` itself |
| Can an option be a non-function | no, must be a tool | yes, value options are supported |
| Dependency | provider tool-use support | none, plain text suffices |

Pick this mechanism when you don't want to depend on the provider's tool-use
support, or when you need "an option that is not a function" — a decision
that returns a value directly (typically routing markers like `done` /
`escalate`).

## Entry one: `decision.make` — pure decision

Inside an `@agentic_function`, call `decision.make` once — no runtime passed,
no `if` written:

```python
from openprogram.agentic_programming import agentic_function, decision

@agentic_function
def route_message(msg: str, runtime=None) -> str:
    return decision.make("Pick one way to handle this message.", {
        "analyze":  analyze_sentiment,        # a function
        "fallback": fallback_reply,           # a function
        "done":     "CONVERSATION_OVER",      # a value
    })
```

`decision.make` renders the menu, calls the model, parses the reply, then
**resolves the pick directly into the result of the next step**:

- LLM picked a function → that function executes (with parsed + injected
  arguments) and its return value is returned.
- LLM picked a value → that value is returned as-is.

Both cases return "the result of the next step" itself. The caller never
checks "which one was picked" and never branches by type — the decision IS
the branch, so there is no `if` to write.

## Entry two: `runtime.exec(choices=...)` — work first, decide last

The more common need: the model first runs a full turn (reasoning, tool
calls, whatever the job takes), and the **closing** return must be a
decision. Use `exec`'s `choices=` parameter:

```python
@agentic_function
def handle_ticket(ticket: str, runtime=None) -> dict:
    """Read the ticket, look things up, then decide which flow to route to."""
    return runtime.exec(
        f"Handle this ticket: {ticket}",
        toolset="default",          # before: the model uses tools to research, run commands
        choices={                   # closing: the return must be one of these
            "refund":    issue_refund,
            "escalate":  escalate_to_human,
            "close":     {"status": "closed"},
        },
    )
```

What `exec(choices=...)` does: it splices the option menu plus a "work
first, close with a JSON pick" instruction (`DECISION_FINISH_INSTRUCTION`)
into the prompt, then runs a normal exec turn — tools from `tools` /
`toolset` get called as usual, the model reasons as usual. At the end of the
turn, the model's final reply must be one `{"call": ...}` JSON, and `exec`
resolves it with `resolve_decision`: a picked function executes and returns
its result, a picked value is returned.

`exec` without `choices` returns the raw reply text; with `choices` it
returns the resolved decision result. `decision.make(prompt, options)` is
equivalent to an `exec(choices=options)` with no preceding work — with one
nuance: only `exec(choices=)` appends the `DECISION_FINISH_INSTRUCTION`;
`decision.make` sends just your prompt plus the menu, so your own prompt
must tell the model to pick.

## Option containers

Each option is shaped like a tool: it has a name, a description, and a
**payload schema**. Three option kinds:

| Option kind | When picked | Schema comes from |
|---|---|---|
| Function option | executes the function, returns its return value | the function signature |
| Value option | returns that fixed value | none |
| Schema option | the model fills structured data per the schema; returns `{"decision": name, **filled fields}` | the schema you declare explicitly |

`options` can be a dict or a list.

**Dict form** `{name: handler}`, where the key is the option name:

```python
decision.make("...", {
    "retry":     retry_fn,                            # function option
    "skip":      "SKIPPED",                           # value option
    "abort":     (AbortSignal(), "pick when stuck"),  # value option + description
    "emit_plan": ("Produce a plan.", {                # schema option: ("description", schema)
        "steps": [{"action": str, "target": str}],
        "rationale": str,
    }),
})
```

**List form** — each item is a callable, a `(callable, "description")`
tuple, or a string option shape (`"name"` / `("name", "description")` /
`("name", "description", schema)`). In list form a function option's name is
the function's `__name__`.

### Schema structure

A schema is `{field_name: field_type}`, and field types **nest
recursively**, so one option can have the model return arbitrarily
structured JSON:

| Notation | Meaning |
|---|---|
| `field: str` (any Python type) | a scalar of that type |
| `field: "description"` | a described `str` scalar |
| `field: [subschema]` | a list whose every element matches `subschema` |
| `field: {subfield: ...}` | a nested object (keys are subfield names) |
| `field: {"type": T, "description": ..., "options": [...]}` | meta-description with type/description/enum |

Three caveats: a list schema must contain **exactly one** item template —
`[str, int]` raises `TypeError`; a dict whose keys all fall inside
`{type, description, options, fields, items}` is parsed as a meta-spec, not
a nested object (a nested object needs at least one key outside that set);
and tuples are reserved syntax in handler position — a literal 2-tuple value
option gets misparsed as `(value, "description")`.

After parsing, `parse_args` **recursively validates** types and nesting
against the schema; the `Call:` example `render_options` renders also
carries the nested placeholder shape. This aligns "finite-branch choice"
with tool calling — every branch can carry an arbitrarily structured
payload. If the need is "no branching at all, always return the same
structure", use a single-option `decision.make`, or just
`exec(response_format=...)`.

## Internal steps

### 1. `render_options` renders the menu

For each option it emits: the signature `name(param: type, ...)`, the
description, per-parameter detail, and a one-line `Call:` JSON example. Only
`source="llm"` parameters are shown — `runtime` / `context` injected
parameters are hidden from the LLM. If a parameter declares `options`
(enum), the detail lists the allowed values. Placeholder values in the
`Call:` example are native JSON literals (`0` / `false` / `[]` / `{}` /
`"<str>"`).

### 2. Call the model

`decision.make` does a direct `runtime.exec(prompt + menu)`;
`exec(choices=)` splices the menu into the turn it was going to send anyway.

### 3. `parse_args` parses and validates

- `extract_action` digs the JSON carrying a `call` key out of a
  ```` ```json ```` code block or bare text. The `call` key has aliases
  `action` / `function` / `tool`, any of which is accepted.
- `call` not in the registry → `_ParseError("unknown_call")`.
- `_validate_field` validates field by field: type
  (`str/int/float/bool/list/dict`; `bool` does not count as `int`, `float`
  accepts `int`), enum (`options`).
- Function options: fill `source="context"` parameters from the `context`
  dict per the signature, inject `runtime`-class parameters, drop fields
  outside the signature, check required ones (those without defaults).
- Value/text options: every declared schema field is required; hallucinated
  fields outside the schema are dropped.
- Returns `(chosen, kwargs)` — for a function option `chosen` is the
  original function; for a value/text option it is the name string.

### 4. Retry on parse failure

If any step raises `_ParseError`, `parse_args` retries (default
`max_retries=1`, set 0 to disable; `max_retries` is settable only on
`decision.make` / `parse_args` — the `exec(choices=)` path is fixed at one
retry): it uses `runtime.exec` to send "the
previous reply + the error reason + the re-rendered menu" back to the LLM
for another pick. The retry is a model call like any other and lands in the
DAG as usual. When all retries are exhausted → raises `DecisionError`
carrying the last error kind, message, and the head of the reply.

`DecisionError` subclasses `ValueError` (old `except ValueError` code still
catches it), and callers can `except DecisionError` to catch exactly "the
model never produced a valid pick" without trapping unrelated
`ValueError`s — e.g. a planner treating it as "this step is over". The
framework stops at "raise a clear exception"; what happens after catching is
the caller's business, with no built-in fallback.

### 5. `resolve_decision` resolves into a result

If `chosen` is a function, run `chosen(**kwargs)` and return the result; if
it is a string, look the value up in the value table and return it (a value
option that declared a schema returns `{"decision": name, **kwargs}`).

## Relation to the tool-call loop

This mechanism does not conflict with the tool-call loop of
`agent_loop.py` described in `tool-calling.md` — they are two parallel
implementations of "let the model pick the next step". An
`@agentic_function` can serve both as a native tool for `exec(tools=[...])`
and as a decision option — same function, two call paths. Which to use
hinges on: whether you want to depend on provider tool use, whether an
option needs to be a value rather than a function, and whether every
decision and retry should be a traceable DAG node.
