# Next-step decision making(decision.make / exec(choices=))

This document describes the **next-step decision** mechanism in OpenProgram: an agentic function hands the decision of "what to do next" to the LLM — you give it a set of options, it picks one, and the framework resolves that choice directly into "the result of the next step". This mechanism and the provider's native tool call are two independent paths.

The implementation lives in `openprogram/agentic_programming/decision.py` inside the framework. There are two entry points that share the same option shapes and parsing:

- `decision.make(prompt, options)` — pure decision; the model does no work, it just picks.
- `runtime.exec(..., choices=options)` — the model first runs a full turn (reasoning, calling tools), and only the wrap-up is a decision.

`decision.make` needs the runtime to issue the model call, but the runtime is taken automatically from the `_current_runtime` ContextVar set up by the `@agentic_function` decorator, so calling it inside an agentic function does not require passing the runtime; you only need to pass `runtime=` explicitly when calling it outside an agentic function.

## Difference from native tool call

| | Native tool call | Next-step decision (this mechanism) |
|---|---|---|
| How options are given to the model | The provider protocol's `tools` field | A text menu inside the prompt |
| How the model expresses a choice | A structured `ToolCall` at the protocol layer | A piece of JSON in the reply body |
| Who parses it | provider / agent_loop | `decision.py` parses it itself |
| Can an option be a non-function | No, it must be a tool | Yes, value options are supported |
| Dependency | provider must support tool use | None, plain text is enough |

When to choose this mechanism: you don't want to depend on the provider's tool use support; or you need an "option that isn't a function" — a decision that returns some value directly (typically routing markers such as `done` / `escalate`).

## Entry point one: `decision.make` — pure decision

Inside an `@agentic_function`, call `decision.make` once, without passing the runtime and without writing any `if`:

```python
from openprogram.agentic_programming import agentic_function, decision

@agentic_function
def route_message(msg: str) -> str:
    return decision.make("Pick a way to handle this message.", {
        "analyze":  analyze_sentiment,        # a function
        "fallback": fallback_reply,           # a function
        "done":     "CONVERSATION_OVER",      # a value
    })
```

`decision.make` renders the menu, calls the model, parses the reply, and then **resolves the choice directly into the result of the next step**:

- The LLM picked a function → that function is executed (with parsed and injected arguments), and its return value is returned.
- The LLM picked a value → that value is returned as-is.

In both cases what's returned is "the result of the next step" itself. The caller does not check "which one was picked" and does not branch by type — the decision is itself the branch, so there is no `if` to write.

## Entry point two: `runtime.exec(choices=...)` — work first, then decide

A more common need is: the model first runs a full turn (reasoning, calling tools, doing whatever needs doing), and only the **wrap-up** return is a decision. Use the `choices=` parameter of `exec`:

```python
@agentic_function
def handle_ticket(ticket: str) -> dict:
    """Read the ticket, look up references, then decide which flow to route it to."""
    return runtime.exec(
        f"Handle this ticket: {ticket}",
        toolset="default",          # earlier: the model uses tools to look up references and run commands
        choices={                   # wrap-up: the return must pick one from here
            "refund":    issue_refund,
            "escalate":  escalate_to_human,
            "close":     {"status": "closed"},
        },
    )
```

What `exec(choices=...)` does: it splices the option menu and an instruction to "work first, then pick one in JSON to wrap up" (`DECISION_FINISH_INSTRUCTION`) into the prompt, then runs a normal exec turn — the tools given via `tools` / `toolset` get called as needed, and the model reasons as needed. When the turn ends, the model's final reply must be a `{"call": ...}` JSON, which `exec` resolves with `resolve_decision`: if a function was picked it is executed and its result returned, if a value was picked the value is returned.

When `exec` is called without `choices` it returns the raw reply text; with `choices` it returns the resolved decision result. `decision.make(prompt, options)` is equivalent to an `exec(choices=options)` with "no preceding work".

## Option containers

Each option is like a tool: it has a name, a description, and a **payload schema**. Three kinds of options:

| Option type | After being picked | schema comes from |
|---|---|---|
| Function option | Execute the function, return its return value | The function signature |
| Value option | Return that fixed value | None |
| schema option | The model fills in structured data per the schema, returning `{"decision": name, **the filled fields}` | The schema you declare explicitly |

`options` can be a dict or a list.

**dict form** `{name: handler}`, where the key is the option name:

```python
decision.make("...", {
    "retry":     retry_fn,                       # function option
    "skip":      "SKIPPED",                      # value option
    "abort":     (AbortSignal(), "pick when it can't continue"),  # value option + description
    "emit_plan": ("Produce a plan.", {           # schema option: ("description", schema)
        "steps": [{"action": str, "target": str}],
        "rationale": str,
    }),
})
```

**list form** — each item is a callable, `(callable, "description")`, or a string option shape (`"name"` / `("name", "description")` / `("name", "description", schema)`). For function options in list form, the name is taken from the function's `__name__`.

### The structure of a schema

A schema is `{field_name: field_type}`, and the field type can be **recursively nested**, so a single option can let the model return arbitrarily structured JSON:

| Syntax | Meaning |
|---|---|
| `field: str` (any Python type) | A scalar of that type |
| `field: "description"` | A `str` scalar with a description |
| `field: [sub-schema]` | A list, each element matching `sub-schema` |
| `field: {sub-field: ...}` | A nested object (the keys are sub-field names) |
| `field: {"type": T, "description": ..., "options": [...]}` | A meta-description with type/description/enum |

After parsing, `parse_args` **recursively validates** the type and nested structure against the schema; the `Call:` example rendered by `render_options` also carries the nested placeholder shape. This aligns "bounded branch selection" with tool calling — each branch carries an arbitrarily structured payload. If the need is "no branching at all, always return the same structure", use a single-option `decision.make`, or just `exec(response_format=...)`.

## Internal steps

### 1. `render_options` renders the menu

For each option it outputs: the signature `name(arg: type, ...)`, the description, a per-argument breakdown, and a one-line `Call:` JSON example. It only shows arguments with `source="llm"` — arguments injected by `runtime` / `context` are hidden from the LLM. If an argument declares `options` (an enum), the breakdown lists the allowed values. The placeholder values in the `Call:` example are JSON native literals (`0` / `false` / `[]` / `{}` / `"<str>"`).

### 2. Call the model

`decision.make` directly calls `runtime.exec(prompt + menu)`; `exec(choices=)` splices the menu into the turn it was going to send anyway.

### 3. `parse_args` parses and validates

- `extract_action` extracts the JSON carrying a `call` key from a ```` ```json ```` code block or from raw text. The `call` key has aliases `action` / `function` / `tool`, any of which is accepted.
- `call` not in the registry → `_ParseError("unknown_call")`.
- `_validate_field` validates each field: type (`str/int/float/bool/list/dict`, with `bool` not treated as `int`, and `float` accepting `int`), enum (`options`).
- Function option: per the signature, fill in `source="context"` arguments (taken from the `context` dict), inject `runtime`-class arguments, drop extra fields not in the signature, and check required fields (those without a default in the signature).
- Value / text option: all declared schema fields are required, and hallucinated fields not in the schema are dropped.
- Returns `(chosen, kwargs)` — for a function option `chosen` is the original function; for a value / text option `chosen` is the name string.

### 4. Retry on parse failure

If any step raises `_ParseError`, `parse_args` goes through a retry (default `max_retries=1`, set 0 to disable): it uses `runtime.exec` to send "the last reply + the error reason + the re-rendered menu" to the LLM and have it pick again. This retry is also a model call and likewise lands in the DAG. If it still fails after all retries are exhausted → it raises `DecisionError`, carrying the last error type, message, and the head of the reply.

`DecisionError` inherits from `ValueError` (an old `except ValueError` can still catch it), and the caller can `except DecisionError` to precisely catch the single case of "the model never picked a valid option" without accidentally catching unrelated `ValueError`s — for example, letting some planner treat it as "end this step". The framework goes only as far as "raising a clear exception"; how to handle it after catching is the caller's business, and the framework builds in no fallback.

### 5. `resolve_decision` resolves it into a result

If `chosen` is a function, it runs `chosen(**kwargs)` and returns the result; if `chosen` is a string, it looks up the corresponding value in the value table and returns it (if a value option declared a schema, it returns `{"decision": name, **kwargs}`).

## Relationship to the tool call loop

This mechanism does not conflict with the tool call loop of `agent_loop.py` in `tool-calling.md`; they are two parallel implementations of "let the model pick the next step". An `@agentic_function` can serve both as a native tool for `exec(tools=[...])` and as a decision option — the same function, two calling paths. Which one to choose depends on: whether you want to depend on provider tool use, whether you want "an option that is a value rather than a function", and whether you want each decision and retry to be a traceable DAG node.
