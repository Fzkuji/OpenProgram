# meta — function lifecycle helpers

This directory holds the user-facing meta functions that **create, modify, fix, and improve** other agentic functions, plus the internal helpers they share.

## File layout

| File | Visibility | Role |
|---|---|---|
| `create.py` | Entry | Make a new function from a natural-language description. |
| `edit.py` | Entry | Modify an existing function according to an instruction (covers the "fix it" case too — pass an instruction that names the error). |
| `improve.py` | Entry | LLM picks improvements for an existing function. |
| `create_app.py` | Entry | Generate a multi-function CLI app skeleton. |
| `create_skill.py` | Entry | Write a `SKILL.md` for an existing function. |
| `_generate_code.py` | Helper | Base LLM call: every entry above eventually delegates here. |
| `_clarify.py` | Helper | Pre-flight: ask the LLM whether the task is clear before generation. |
| `_extract_code.py` | Helper | Pull a Python code block out of the LLM's markdown reply. |
| `_validate_code.py` | Helper | Rule-based static check (see §3). |
| `_compile_function.py` | Helper | Exec code in a sandbox; locate and return the resulting callable. |
| `_save_function.py` | Helper | Write code to `programs/functions/third_party/<name>.py`. |
| `_get_source.py` | Helper | Read source code + error log of an existing function. |
| `_sandbox.py` | Helper | Allowlists for builtins / imports used when exec'ing generated code. |

Underscore-prefixed files are internal plumbing. The WebUI scanner skips them so they don't clutter the function list, but they're still importable like any other module.

## When to use which entry point

| Goal | Entry function |
|---|---|
| "I have an idea, write me the function." | `create(description, runtime, name=None)` |
| "Change this function: do X differently." | `edit(fn, runtime, instruction="...")` |
| "This function crashed — fix it." | `edit(fn, runtime, instruction="fix the error in the error log")` |
| "Make this function better but I don't know how." | `improve(fn, runtime)` |
| "Generate a small CLI app, not just one function." | `create_app(description, runtime, name=None)` |
| "Make this function discoverable by skill triggers." | `create_skill(fn_name, description, code, runtime)` |
| "Delete a function." | `rm openprogram/programs/functions/third_party/<name>.py` |

## Lifecycle of a function

### 1. Creation — `create()`

```
user input
   │
   ▼
create(description, runtime, name=?)
   │
   ├─▶ _clarify  ─── only if an ask_user handler is registered;
   │                  otherwise skipped (scripts / headless).
   │                  May return follow_up dict, halting the flow.
   │
   ├─▶ _generate_code   ── one LLM call. Receives the description,
   │                        function_metadata.md, and hard runtime.exec
   │                        rules. Returns code in a ```python fence.
   │
   ├─▶ _extract_code    ── strip the fence.
   │
   ├─▶ _validate_code   ── rule-based static check (§3).
   │
   ├─▶ _save_function   ── write to programs/functions/third_party/<name>.py
   │                        with the framework header + imports prepended.
   │
   ├─▶ _compile_function── exec the file in a sandbox; return the live callable.
   │
   ▼
returns callable (or {"type":"follow_up", "question": "..."} when blocked)
```

`name=` is a hint passed into the task ("Name the function exactly X"). If the LLM ignores it, the function is saved under whatever name the LLM produced.

### 2. Modification — `edit()` / `improve()`

Both share the same internal shape:

```
edit(fn, runtime, instruction)
   │
   ├─▶ _get_source(fn)      ── read current source code.
   │
   ├─▶ get_error_log(fn)    ── prior attempt/error records from
   │                            the function's Context.
   │
   ├─▶ _clarify             ── same as create.
   │
   ├─▶ _generate_code       ── one LLM call with current source +
   │                            errors + instruction.
   │
   ├─▶ _extract_code → _validate_code → _compile_function
   │
   ├─▶ (optional) execution-based check
   │       run the new function on a smoke input to confirm it
   │       returns and doesn't crash. Failure → next round.
   │
   ├─▶ _save_function(source_path=<old path>)   ── overwrites in place.
   │
   ▼
returns the new callable
```

Differences:

- `edit`: takes an explicit `instruction`. For "fix this error" cases, pass an instruction that references the error log.
- `improve`: no instruction; the LLM is asked to pick improvements from the source.

There is no second LLM call to "judge the edit"; validation is **rule-based** plus, optionally, an execution check. Removing the judge LLM keeps the loop cheap and predictable.

## 3. Validation — what `_validate_code` checks

All checks are **pure-Python / AST-based**. No LLM is involved in deciding whether the generated function is correct. The contract is:

### 3.1 Structural rules (must all pass)

| # | Rule | How it's checked |
|---|---|---|
| 1 | The file contains at least one `def` and at most one `@agentic_function`-decorated entry function. | AST walk over the top-level. |
| 2 | If the function makes any LLM call, the entry function must be decorated with `@agentic_function`. | AST decorator list of every `def`. |
| 3 | If decorated, the signature must include `runtime: Runtime`. | AST: signature parameter annotations. |
| 4 | Every parameter has a type annotation; the return has an annotation. | AST: `arg.annotation` and `FunctionDef.returns`. |
| 5 | The function has a non-empty docstring whose first paragraph is a one-line summary. | AST: first statement is `Expr(Constant(str))`. |
| 6 | No `async def`. | AST: `AsyncFunctionDef` rejected. |
| 7 | All `import` / `from ... import` modules are in `_sandbox._ALLOWED_IMPORTS` (or `openprogram`). | line-by-line text scan, then cross-checked at exec time by `_safe_import`. |
| 8 | The framework imports (`agentic_function`, `Runtime`) are **not** re-imported by the function file — the framework injects them at save time. | text scan for the exact framework import lines. |
| 9 | Code parses as Python. | `compile(code, "<generated>", "exec")`. |

### 3.2 `runtime.exec` call-site rules (where applicable)

Walked via AST over every `runtime.exec(...)` call inside the function body:

| # | Rule | How it's checked |
|---|---|---|
| 10 | `content=` argument is a list literal. | AST: `Call.keywords["content"].value` is `ast.List`. |
| 11 | Each item in the `content` list is a dict literal (not a bare string). | AST: every `List.elts[i]` is `ast.Dict`. |
| 12 | Each dict has a `"type"` key. | AST: keys include the constant `"type"`. |
| 13 | Allowed kwargs only: `content`, `response_format`, `model`, `tools`, `toolset`, `tools_source`, `tools_allow`, `tools_deny`, `tool_choice`, `parallel_tool_calls`, `max_iterations`. | AST: keyword names cross-checked against the allowlist. |
| 14 | No `system=` keyword (system prompt is decorator-side / docstring-side). | AST: `system` rejected in keyword names. |

### 3.3 `@agentic_function(input=...)` rules

For every parameter that the LLM is supposed to fill (i.e., not `runtime`, not `hidden`):

| # | Rule | How it's checked |
|---|---|---|
| 15 | The parameter appears in the decorator's `input={...}` dict. | AST: keys of the `input` dict. |
| 16 | Its entry has at least a `description` field. | AST: the per-param dict has a `description` key. |
| 17 | If `options=` is declared, all listed values are JSON-serializable scalars. | AST: each element of `Call.keywords["options"]` is `Constant`. |

### 3.4 Optional: execution-based validation

After the structural checks pass and `_compile_function` returns a live callable, callers may run a **smoke test**:

```python
# Inside edit() / improve() / a CI step
try:
    result = fn(**smoke_inputs, runtime=runtime)
except Exception as e:
    raise ValueError(f"Smoke test crashed: {e}")
# Optional: check result type/shape against the declared return annotation.
```

This is the test-side check the user requested. It's optional because:

- It costs an LLM call (the function executes).
- For functions whose return is hard to verify automatically (free-form text), the smoke test only proves "didn't crash", not "did the right thing".

For "did the right thing" verification, write a normal `pytest` test in `tests/`. That's outside the lifecycle of the meta functions but is the recommended next step after `create()` for anything important.

### 3.5 Failure behavior

If any rule in §3.1–§3.3 fails:

- `_validate_code` raises `ValueError` with the failing rule and the offending code.
- `create()` / `edit()` / `improve()` bubble that up.
- The retry loops inside `edit` / `improve` feed the error description back into the next LLM call, so the model can correct the specific violation.

No LLM judge is used. The rules are the rules.

## 4. Deletion / cleanup

There is no `delete()` meta function. To remove a function:

```bash
rm openprogram/programs/functions/third_party/<name>.py
```

To rename, use `edit(fn, instruction="rename to X")` or rename the file manually and update the entry function inside.

## 5. Internals — the LLM-side contract

`_generate_code` is the only function that actually talks to an LLM. Every other helper is pure Python. The LLM sees, in this order:

1. The `_generate_code` docstring (system-role on providers that honor it).
2. The contents of `docs/design/function/function_metadata.md`, prepended into the user content.
3. A short "runtime.exec hard rules" block prepended into the user content.
4. The actual task description from `create` / `edit` / `improve` / etc.

If you want to change the rules every generation follows, edit either the docstring of `_generate_code` or `function_metadata.md` — both are read on every call.

## 6. Quick sanity checks

| Symptom | Likely cause |
|---|---|
| `create()` returns `{"type":"follow_up", ...}` and doesn't generate code | An `ask_user` handler is registered and `_clarify` decided more info is needed. Answer the question (interactive) or run headless. |
| Generated function returns the wrong thing when run | The per-call prompt isn't in `content=[...]`. Some providers ignore the docstring as instruction. Put the prompt + data in `content`. |
| `ImportError: 'X' is not allowed` during compile | LLM wrote `import X` where X isn't in `_sandbox._ALLOWED_IMPORTS`. Allow it or remove the dependency. |
| `_validate_code` rejects "rule N" repeatedly | The retry feedback should tell the LLM which rule failed. If it keeps failing, the rule may be too strict or the task description is unclear about what to generate. |
