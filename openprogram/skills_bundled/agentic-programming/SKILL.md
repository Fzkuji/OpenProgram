---
name: agentic-programming
description: "Write, edit, validate, save, and run Agentic Programming functions (@agentic_function) directly with your own file-editing tools. Covers next-step decision making — letting the LLM pick the next function/value via decision.make or runtime.exec(choices=). No dedicated meta functions — just follow the rules in this skill. Triggers: 'write an agentic_function', 'create a function', 'edit a function', 'improve a function', 'fix this function', 'add a function that', 'add a tool that', 'run a function', 'let the model decide', 'make the model pick', 'decision.make', 'next-step decision'."
---

# Agentic Programming — author, edit, validate, save

This skill is a complete guide for writing and maintaining `@agentic_function`-style functions in this repo. You (the agent) read this, then use your own `Read` / `Write` / `Edit` / `Bash` tools to make the changes directly. There are no dedicated `create()` / `edit()` / `improve()` framework functions — they were removed because all they did was wrap a single LLM call plus file write, which you can do yourself.

## When to invoke

Use this skill when the user asks to:

- write a new function (agentic or pure-Python) in this project
- edit, fix, improve, refactor an existing function in this project
- generate a `SKILL.md` for an existing function

Do **not** invoke this skill for unrelated work (downloads, web search, system commands).

## The workflow

```
1. Pick the target file
2. Decide: agentic_function or plain Python?
3. Draft the code following the spec below
4. Self-validate against the rule checklist
5. Write the file (Write tool for new file, Edit for modify)
6. (Optional) Run a smoke test by importing + calling
```

That's it. Every step is something you do with your normal tools.

## 1. Picking the target file

| Situation | Where the file goes |
|---|---|
| User said "save to X" | Exactly X. |
| Brand new general-purpose function | `openprogram/functions/agentics/<name>/__init__.py` |
| Editing an existing function | The file you found it in (don't move it). |
| User's project / non-framework function | Wherever fits their layout (ask if unclear). |

Directory + filename convention: lowercase snake_case folder matching the function name (e.g. `analyze_sentiment/__init__.py` contains `def analyze_sentiment`). The folder layout (one directory per agentic function, code in `__init__.py`) replaced the old flat `<name>.py` layout in the function-calling unification — see ``docs/design/function/function-calling-unification.md``. Single-file helpers inside the same logical agentic can sit next to ``__init__.py`` (e.g. ``analyze_sentiment/_prompt.py``) without polluting the top-level namespace.

## 2. agentic_function vs plain Python vs @function

| What you're building | Decorator | Where it lives |
|---|---|---|
| LLM-reasoning logic (analyze / classify / generate / decide) | `@agentic_function` + `runtime: Runtime` + `runtime.exec(content=[...])` | `openprogram/functions/agentics/<name>/__init__.py` |
| Deterministic helper (parsing / math / file munging / API wrapper) | plain function, no decorator, no `runtime` parameter | wherever it's used; if shared, `agentics/_utils/`-style |
| **Framework-level deterministic LLM tool** (bash / read / web_search / etc.) | `@function` (different decorator!) | `openprogram/functions/tools/<name>/` |

**This skill is about `@agentic_function`.** The `@function` decorator is a different mechanism for framework-level leaf tools and is out of scope here — see ``docs/design/function/function-calling-unification.md`` if you need it. Both decorators ultimately produce ``AgentTool`` entries in the same shared registry, but they target different kinds of work: `@function` for deterministic Python tools called by the LLM, `@agentic_function` for higher-order functions whose body itself drives an LLM round.

Don't decorate a function just to "make it discoverable"; `@agentic_function` implies an LLM call inside the body.

## 3. Function metadata specification

The framework's components (WebUI, catalog menus, provider-native `tools=[...]`) all read metadata from the same places. Use these as the **only** sources:

| Information | Where it lives | How it's read |
|---|---|---|
| Function name | `def <name>(...)` | `fn.__name__` |
| Parameter names | signature | `inspect.signature(fn).parameters` |
| Parameter types | annotation | `param.annotation` |
| Parameter defaults | annotation default | `param.default` |
| One-line summary (what / when-to-pick) | first paragraph of docstring | first paragraph of `inspect.getdoc(fn)` |
| Detailed function-level documentation | docstring body | rest of `inspect.getdoc(fn)` |
| Per-call LLM prompt + data | the `content=[...]` of that specific `runtime.exec` call | passed at call time |
| Per-parameter description | `@agentic_function(input={"x": {"description": ...}})` | `fn.input_meta["x"]["description"]` |
| Per-parameter enum | `@agentic_function(input={"x": {"options": [...]}})` | `fn.input_meta["x"]["options"]` |
| Hidden-from-LLM parameter | `@agentic_function(input={"x": {"hidden": True}})` | `fn.input_meta["x"]["hidden"]` |
| WebUI placeholder | `@agentic_function(input={"x": {"placeholder": "..."}})` | `fn.input_meta["x"]["placeholder"]` |
| WebUI multiline input | `@agentic_function(input={"x": {"multiline": True}})` | `fn.input_meta["x"]["multiline"]` |
| Dynamic option source | `@agentic_function(input={"x": {"options_from": "functions"}})` | `fn.input_meta["x"]["options_from"]` |
| Working-directory picker mode | `@agentic_function(workdir_mode="optional"\|"hidden"\|"required")` | AST-parsed from the decorator's source text by the WebUI (`openprogram/webui/_functions.py`), so write it as a literal in the decorator call |
| Auto-injected by the framework | param name in `{"runtime", "exec_runtime", "review_runtime"}` | framework checks signature |
| System-prompt override | `@agentic_function(system="...")` | `fn.system` |
| Context-tree visibility | `@agentic_function(expose="io"\|"full"\|"hidden")` | `fn.expose` |
| Context-tree render range | `@agentic_function(render_range={"callers": ..., "subcalls": ...})` | `fn.render_range` |
| Skill trigger keywords | sibling `SKILL.md` frontmatter | skill loader |

Single source of truth: anything expressible in the signature / annotations is not repeated in the decorator; anything expressible in `input=` is not repeated in the docstring.

## 4. The docstring vs `content` split

These two channels have **different responsibilities**. Neither replaces the other.

| Channel | Scope | What goes here |
|---|---|---|
| docstring | Whole-function level. Read by humans, catalog menus, tool_use specs. | One-line summary (required). Optionally a body describing what each LLM call does, expected outputs, edge cases. As detailed as is useful for readers. |
| `runtime.exec(content=[...])` | One specific LLM call inside the function. A function may make several with different prompts. | The actual prompt + data for *this* call: the task, output format, constraints, plus the data to operate on. **Required even if the docstring already explains the same thing.** |

**The docstring reaches the model — but as description, not as the operative instruction.** The framework stores the docstring on the function's DAG node and renders it into the context of the LLM calls made inside the function; it also becomes the tool `description` when the function is exposed via `tools=[fn]`. So the model *sees* it. But it arrives as descriptive context, and some providers (codex CLI / chatgpt subscription) respond conversationally to whatever is in `content`, treating the rest as background. The operative per-call prompt — task, output format, constraints — must therefore live in `content`. Rule of thumb: the docstring describes *what the function is*; `content` *instructs the call*. Write the per-call prompt in `content` even if the docstring already explains the same thing.

## 5. Recommended style (new code must use this)

Minimal example:

```python
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(input={
    "text": {"description": "Text to analyze."},
})
def analyze_sentiment(text: str, runtime: Runtime = None) -> str:
    """Classify the sentiment of a text into positive, negative, or neutral."""
    reply = runtime.exec(content=[{"type": "text", "text": (
        f"Classify the sentiment of the following text. Reply with exactly "
        f"one word: positive, negative, or neutral.\n\n"
        f"Text:\n{text}"
    )}])
    label = str(reply).strip().lower()
    return label if label in {"positive", "negative", "neutral"} else "neutral"
```

Fuller example exercising more metadata features:

```python
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(input={
    "essay": {
        "description": "Essay to review.",
        "placeholder": "Paste the essay text here...",
        "multiline": True,
    },
    "rubric_id": {
        "description": "Which rubric to apply.",
        "options": ["ielts_writing", "toefl_writing", "gre_argument"],
    },
    "max_score": {
        "description": "Upper bound for the numeric score.",
    },
    "show_rubric_internals": {
        "description": "Include rubric breakdown in the output.",
    },
    "session_id": {
        # System-supplied; LLM does not see this.
        "hidden": True,
    },
})
def review_essay(
    essay: str,
    rubric_id: str,
    max_score: int,
    show_rubric_internals: bool,
    session_id: str,           # filled by Python via context, not LLM
    runtime: Runtime = None,   # auto-injected
) -> dict:
    """Score an essay against a named rubric and return a structured report."""
    # ... real implementation here ...
```

The docstring stays one line + maybe a body paragraph. **No `Args:` or `Returns:` sections.** If the return shape matters to downstream callers, encode it with a structured return type (`TypedDict` / `dataclass`).

## 5b. Next-step decision making

When a function needs the LLM to **decide what to do next** — pick one of several follow-up functions or values — do not hand-roll it (render a menu, parse JSON, branch on the result). Use the framework's decision primitive. Two entry points, same options, same resolution:

| Entry | Use when |
|---|---|
| `decision.make(prompt, options)` | Pure decision — the model picks straight away, no work first. |
| `runtime.exec(..., choices=options)` | The model does a full turn (reasoning, tool calls) and only the *finish* is the pick. |

The function still declares `runtime: Runtime` like any agentic function that calls the LLM (the decorator uses it to set up context). You just do not pass `runtime=` as an argument to `decision.make` — it reads the ambient runtime itself.

```python
from openprogram.agentic_programming import agentic_function, decision
from openprogram.agentic_programming.runtime import Runtime


@agentic_function
def route_message(msg: str, runtime: Runtime = None) -> dict:
    """Decide how to handle an incoming message."""
    return decision.make(f"Pick how to handle this message:\n{msg}", {
        "analyze":  analyze_sentiment,        # function option — runs it, returns its result
        "fallback": fallback_reply,           # function option
        "done":     {"action": "ignored"},    # value option — returns the value as-is
    })


@agentic_function
def handle_ticket(ticket: str, runtime: Runtime = None) -> dict:
    """Investigate a ticket, then decide which workflow to route it to."""
    return runtime.exec(
        f"Handle this ticket:\n{ticket}",
        toolset="default",          # the model does the work with tools first
        choices={                   # the final return must be one of these
            "refund":   issue_refund,
            "escalate": escalate_to_human,
            "close":    {"status": "closed"},
        },
    )
```

Option forms — `options` / `choices` is a dict `{name: handler}` or a list of callables / option tuples. A handler can be:

- a **callable** — a function option; picking it runs the function, its return value is the result
- a **value** — a value option; picking it returns that value as-is (`{name: (value, "description")}` adds a description)
- a **`("description", schema)`** pair — a schema option; the model fills the schema and the filled structure is returned as `{"decision": name, **fields}`. The `schema` is `{field: type}` and nests: `[item]` for a list, `{sub: ...}` for a nested object — so one option can ask for any structured JSON

The resolution leaves **no `if` for you to write**: a picked function runs, a picked value/structure comes back. The decision *is* the branch. If the model never produces a resolvable pick after retries, `DecisionError` (a `ValueError` subclass) is raised — catch it if you want a graceful fallback.

Rule: if you find yourself writing `runtime.exec(...)` followed by `if "..." in reply:` to route on what the model said, replace it with `decision.make` / `exec(choices=)`.

## 6. Rule-based validation — run this before declaring done

Walk through every rule. If any fails, fix it before writing the file.

### 6.1 Structural rules

| # | Rule | How to check |
|---|---|---|
| 1 | File contains at most one entry function (one `@agentic_function`-decorated `def` at top level). | Eyeball / grep. |
| 2 | If the function makes any `runtime.exec` call, the entry function must be decorated with `@agentic_function`. | Eyeball. |
| 3 | If decorated, the signature must include `runtime: Runtime` — and give it a default (`runtime: Runtime = None`). A function passed to `exec(tools=[...])` fails at tool dispatch with `TypeError: missing a required argument: 'runtime'` before injection if the parameter has no default; direct Python calls and the `decision`/`choices` path do inject. | Eyeball signature. |
| 4 | Every parameter has a type annotation; the return has an annotation. | Eyeball signature. |
| 5 | The function has a docstring whose first paragraph is a one-line summary. | Eyeball. |
| 6 | `async def` bodies are supported (the decorator wrapper has an async branch), but sync `def` remains the default recommendation: `Runtime.async_exec` exists but lacks `tools=` / `choices=` support. | Eyeball. |
| 7 | Every `import` / `from ... import` actually resolves in this environment. There is no import sandbox — any installed package works — but prefer the stdlib and `openprogram.*` so the function stays dependency-free and portable. | Read import lines; the §9 smoke test catches anything that doesn't resolve. |
| 8 | If you pull in a third-party package (not stdlib, not `openprogram.*`), confirm it's already installed and tell the user it's a new dependency. | Check / `pip show <pkg>`. |
| 9 | Code parses as Python (no syntax error). | Mentally compile; if unsure run `python -c "import ast; ast.parse(open('<path>').read())"`. |

### 6.2 `runtime.exec` call-site rules (every `runtime.exec(...)` call)

| # | Rule | How to check |
|---|---|---|
| 10 | `content=` is canonically a `list[dict]`. | Eyeball. |
| 11 | Each item in a `content` list is a dict literal — `content=[text]` (a bare string inside the list) is wrong. Passing a plain string as `content` itself **is** accepted and auto-wrapped into a text block; the list-of-blocks form is canonical. `content=[{"type":"text","text":text}]` is the canonical shape. | Eyeball. |
| 12 | Each dict has `"type"`: `"text"` (with `"text"`) or `"image"` / `"audio"` / `"file"` (with `"path"`). | Eyeball. |
| 13 | Allowed kwargs only: `content, response_format, model, tools, toolset, tools_source, tools_allow, tools_deny, tool_choice, parallel_tool_calls, max_iterations, choices`. Note: `tool_choice`, `parallel_tool_calls`, `max_iterations` are accepted but currently not wired — non-default values are silently ignored. | Eyeball. |
| 14 | **No `system=` kwarg.** System instruction comes from the decorator (`system="..."`) or the docstring, never from a runtime.exec kwarg. | Eyeball. |
| 15 | The per-call instruction lives inside the `content` text, not only in the docstring (see §4). | Eyeball — the text in content should describe the task. |

### 6.3 `@agentic_function(input=...)` rules

For every LLM-visible parameter (not `runtime`, not `hidden=True`):

| # | Rule | How to check |
|---|---|---|
| 16 | The parameter appears as a key in `input={...}`. | Eyeball. |
| 17 | The entry has at least `description`. | Eyeball. |
| 18 | If `options=` is declared, all elements are strings (or other JSON-serializable scalars). | Eyeball. |

### 6.4 Style rules (docstring + content)

| # | Rule | How to check |
|---|---|---|
| 19 | No role-play in the docstring ("You are a helpful assistant"). | Eyeball. |
| 20 | No empty directives ("Complete the task", "Do your best"). | Eyeball. |
| 21 | The per-call `content` text defines the **exact** output format expected; don't leave it for the LLM to guess. | Eyeball. |
| 22 | The docstring may explain what the call does at any level of detail. But the LLM doesn't read it as instruction — the prompt in `content` does the work. | Eyeball. |

## 7. WebUI rendering behavior (good to know when designing parameters)

The form sidebar renders each parameter by these rules:

| Trait | Control |
|---|---|
| `bool` type | Yes / No toggle buttons |
| `str` type, `multiline` not set | Defaults to textarea (`multiline=True` implied) |
| `str` type, `multiline: False` | Single-line `<input>` |
| Non-`str` non-`bool`, no `multiline` | Single-line `<input>` |
| `options: [...]` | Clickable chips + free-form text input |
| `options_from: "functions"` | Dropdown populated from registered functions |
| `hidden: True` | Omitted from the form entirely |
| Has a Python default and no explicit `placeholder` | Placeholder set to `"default: <value>"` |

`workdir_mode` controls the working-directory picker:

| Value | Effect |
|---|---|
| `"optional"` (default) | Picker shown; can be empty |
| `"hidden"` | Picker hidden |
| `"required"` | Picker shown and required |

## 8. Editing an existing function

Same rules, but:

- Use `Read` to load the current source.
- Preserve the function name, parameter names and order, type hints, and existing `@agentic_function(input=..., ...)` arguments **unless the user's instruction explicitly asks to change them**.
- Never change `runtime: Runtime` to `Any`.
- Apply `Edit` for targeted changes; only `Write` when rewriting the whole file.
- If there's a sibling `tests/` file for this function, update or add a test for the change.

For "fix this bug" requests, look at the function's error log first if available (e.g. recent traceback in the conversation), then edit to address the root cause, not the symptom.

## 9. Smoke test after writing

When the function is new or significantly changed, run a quick import-and-call check:

```bash
python -c "
from <path.to.module> import <fn_name>
from openprogram.providers.registry import create_runtime
rt = create_runtime()
result = <fn_name>(..., runtime=rt)
print(result)
rt.close()
"
```

If it crashes, read the traceback and fix before declaring done. For functions whose output is hard to verify automatically (free-form text), the smoke test only proves "didn't crash" — write a real `pytest` test in `tests/` for anything important.

## 10. Running an existing function

Once a function is saved, there are two ways to run it.

**CLI** — for functions discoverable under `openprogram/functions/agentics/` (listed in `openprogram/functions/_registry.py::AGENTIC_MODULES`):

```bash
openprogram programs list                       # see what's available
openprogram programs run <name> --arg key=value  # --arg is repeatable
```

The CLI run path auto-injects a `Runtime` for functions that need one, so
you don't pass `runtime=` yourself. `--provider` / `--model` override the
LLM if the function calls one.

**Python** — import and call directly (any function, anywhere):

```python
from openprogram.functions.agentics.<name> import <name>
from openprogram.providers.registry import create_runtime

rt = create_runtime()
result = <name>(..., runtime=rt)   # pass runtime= only if the signature has it
rt.close()
```

Direct calls do *not* auto-inject a runtime — construct one with
`create_runtime()` and pass it. This is also the §9 smoke-test shape.

## 11. Generating a `SKILL.md` for a function

To make a function agent-discoverable, write a `SKILL.md` next to it:

```
<path>/<fn_name>/SKILL.md
```

Frontmatter format (YAML):

```yaml
---
name: <fn_name>
description: "<one-sentence summary including 4-8 trigger phrases an agent might use>"
---
```

After the frontmatter, write a short body covering when to use this skill, brief usage example, and one or two natural-language triggers. Keep it concise — agents read this every message.

## 12. Sanity checks if something looks wrong

| Symptom | Likely cause |
|---|---|
| Generated function returns the wrong thing when run | Per-call prompt isn't in `content=[...]` — only in docstring. Codex / chatgpt subscription will reply conversationally. Move the instruction into `content`. |
| WebUI doesn't show your function | Filename starts with `_`, or the file isn't under one of the discovery roots. |
| Function crashes with `ImportError` | The imported package isn't installed in this environment. Install it, or rewrite using a stdlib / `openprogram.*` equivalent (see §6.1 rule 7). |
| Same function works on one provider, fails on another | Provider treats the rendered context tree differently. Make sure `content=[...]` is self-sufficient — the test should be: would this work if the function had no docstring at all? If yes, you're good. |

## 13. Quick-reference: the absolute minimum

If you remember nothing else from this skill, remember these:

1. Decorate with `@agentic_function` only when LLM reasoning is needed.
2. `runtime: Runtime = None` in the signature; `runtime.exec(content=[{"type":"text","text":"..."}])` is the call shape.
3. Per-call prompt + data go in `content`. Docstring is documentation, not instruction.
4. No `system=` kwarg on `runtime.exec`.
5. Every LLM-visible parameter needs a `description` in `input={...}`.
6. No `Args:` / `Returns:` sections in the docstring.
7. Save to `openprogram/functions/agentics/<name>/__init__.py` unless the user said otherwise. Add `("<name>", None)` to `openprogram/functions/_registry.py::AGENTIC_MODULES` so the loader actually imports it (otherwise the @agentic_function decorator never fires and the function won't be discoverable).
8. If the LLM should also see it as a callable tool, add `"<name>"` to `openprogram/functions/__init__.py::TOOLSETS["full"]["tools"]` (the Layer 2 exposure whitelist). Without this the function exists but is invisible to LLMs.
