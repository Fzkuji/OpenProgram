---
name: agentic-program
description: "Write, edit, validate, and save Agentic Programming functions (@agentic_function) directly with your own file-editing tools. No dedicated meta functions — just follow the rules in this skill. Triggers: 'write an agentic_function', 'create a function', 'edit a function', 'improve a function', 'fix this function', 'add a function that', 'add a tool that', '写一个 agentic function', '改一下这个函数', '帮我创建一个函数'."
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
| Brand new general-purpose function | `openprogram/programs/functions/third_party/<name>.py` |
| Editing an existing function | The file you found it in (don't move it). |
| User's project / non-framework function | Wherever fits their layout (ask if unclear). |

Filename convention: lowercase snake_case, matching the function name (`analyze_sentiment.py` contains `def analyze_sentiment`).

## 2. agentic_function vs plain Python

| Needs LLM reasoning at runtime? | Use |
|---|---|
| Yes — the function asks the model to analyze, classify, generate, decide | `@agentic_function` + `runtime: Runtime` + `runtime.exec(content=[...])` |
| No — pure deterministic logic (parsing, math, file munging, calling an API) | plain function, no decorator, no `runtime` parameter |

Don't decorate a function just to "make it discoverable"; the decorator implies an LLM call.

## 3. Function metadata specification

The framework's components (WebUI, catalog menus, provider-native `tools=[...]`, meta tooling) all read metadata from the same places. Use these as the **only** sources:

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
| Working-directory picker mode | `@agentic_function(workdir_mode="optional"\|"hidden"\|"required")` | `fn.workdir_mode` |
| Auto-injected by the framework | param name in `{"runtime", "exec_runtime", "review_runtime"}` | framework checks signature |
| System-prompt override | `@agentic_function(system="...")` | `fn.system` |
| Context-tree visibility | `@agentic_function(expose="io"\|"full"\|"hidden")` | `fn.expose` |
| Context-tree render range | `@agentic_function(render_range={"depth": ..., "siblings": ...})` | `fn.render_range` |
| Tool set policy | `@agentic_function(no_tools=True)` | `fn.no_tools` |
| Skill trigger keywords | sibling `SKILL.md` frontmatter | skill loader |

Single source of truth: anything expressible in the signature / annotations is not repeated in the decorator; anything expressible in `input=` is not repeated in the docstring.

## 4. The docstring vs `content` split

These two channels have **different responsibilities**. Neither replaces the other.

| Channel | Scope | What goes here |
|---|---|---|
| docstring | Whole-function level. Read by humans, catalog menus, tool_use specs, meta tooling. | One-line summary (required). Optionally a body describing what each LLM call does, expected outputs, edge cases. As detailed as is useful for readers. |
| `runtime.exec(content=[...])` | One specific LLM call inside the function. A function may make several with different prompts. | The actual prompt + data for *this* call: the task, output format, constraints, plus the data to operate on. **Required even if the docstring already explains the same thing.** |

**Documentation in the docstring does not propagate into the LLM call.** The framework sends the docstring as descriptive context (rendered in the call tree); some providers (codex CLI / chatgpt subscription) will not read it as authoritative instruction and will respond conversationally to whatever's in `content`. Always write the per-call prompt inside `content`.

## 5. Recommended style (new code must use this)

Minimal example:

```python
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(input={
    "text": {"description": "Text to analyze."},
})
def analyze_sentiment(text: str, runtime: Runtime) -> str:
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
    runtime: Runtime,          # auto-injected
) -> dict:
    """Score an essay against a named rubric and return a structured report."""
    # ... real implementation here ...
```

The docstring stays one line + maybe a body paragraph. **No `Args:` or `Returns:` sections.** If the return shape matters to downstream callers, encode it with a structured return type (`TypedDict` / `dataclass`).

## 6. Rule-based validation — run this before declaring done

Walk through every rule. If any fails, fix it before writing the file.

### 6.1 Structural rules

| # | Rule | How to check |
|---|---|---|
| 1 | File contains at most one entry function (one `@agentic_function`-decorated `def` at top level). | Eyeball / grep. |
| 2 | If the function makes any `runtime.exec` call, the entry function must be decorated with `@agentic_function`. | Eyeball. |
| 3 | If decorated, the signature must include `runtime: Runtime`. | Eyeball signature. |
| 4 | Every parameter has a type annotation; the return has an annotation. | Eyeball signature. |
| 5 | The function has a docstring whose first paragraph is a one-line summary. | Eyeball. |
| 6 | No `async def`. | Eyeball. |
| 7 | All `import` / `from ... import` modules are either `openprogram.*` or one of the allowed stdlib modules: `os, os.path, sys, json, re, math, datetime, pathlib, collections, itertools, functools, textwrap, string, io, csv, hashlib, base64, time, random, copy, glob, shutil, tempfile, urllib, urllib.parse, urllib.request, typing, dataclasses, enum, abc, statistics, decimal, fractions`. | Read import lines. |
| 8 | If you need a stdlib module not in the list, ask the user before adding it. | — |
| 9 | Code parses as Python (no syntax error). | Mentally compile; if unsure run `python -c "import ast; ast.parse(open('<path>').read())"`. |

### 6.2 `runtime.exec` call-site rules (every `runtime.exec(...)` call)

| # | Rule | How to check |
|---|---|---|
| 10 | `content=` is a `list[dict]`. | Eyeball. |
| 11 | Each item in `content` is a dict literal — **never a bare string**. `content=[text]` is wrong; `content=[{"type":"text","text":text}]` is correct. | Eyeball. |
| 12 | Each dict has `"type"`: `"text"` (with `"text"`) or `"image"` / `"audio"` / `"file"` (with `"path"`). | Eyeball. |
| 13 | Allowed kwargs only: `content, response_format, model, tools, toolset, tools_source, tools_allow, tools_deny, tool_choice, parallel_tool_calls, max_iterations`. | Eyeball. |
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
from openprogram import create_runtime
rt = create_runtime()
result = <fn_name>(..., runtime=rt)
print(result)
rt.close()
"
```

If it crashes, read the traceback and fix before declaring done. For functions whose output is hard to verify automatically (free-form text), the smoke test only proves "didn't crash" — write a real `pytest` test in `tests/` for anything important.

## 10. Generating a `SKILL.md` for a function

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

## 11. Sanity checks if something looks wrong

| Symptom | Likely cause |
|---|---|
| Generated function returns the wrong thing when run | Per-call prompt isn't in `content=[...]` — only in docstring. Codex / chatgpt subscription will reply conversationally. Move the instruction into `content`. |
| WebUI doesn't show your function | Filename starts with `_`, or the file isn't under one of the discovery roots. |
| Function crashes with `ImportError` on a stdlib module | The module isn't in the allowlist (see §6.1 rule 7). Talk to the user about whether to allow it. |
| Same function works on one provider, fails on another | Provider treats the rendered context tree differently. Make sure `content=[...]` is self-sufficient — the test should be: would this work if the function had no docstring at all? If yes, you're good. |

## 12. Quick-reference: the absolute minimum

If you remember nothing else from this skill, remember these:

1. Decorate with `@agentic_function` only when LLM reasoning is needed.
2. `runtime: Runtime` in the signature; `runtime.exec(content=[{"type":"text","text":"..."}])` is the call shape.
3. Per-call prompt + data go in `content`. Docstring is documentation, not instruction.
4. No `system=` kwarg on `runtime.exec`.
5. Every LLM-visible parameter needs a `description` in `input={...}`.
6. No `Args:` / `Returns:` sections in the docstring.
7. Save to `openprogram/programs/functions/third_party/<name>.py` unless the user said otherwise.
