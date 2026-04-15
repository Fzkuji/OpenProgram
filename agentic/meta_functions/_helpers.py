"""
Shared helpers for meta functions: code extraction, validation, compilation, saving.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from typing import Optional

from agentic.function import agentic_function, traced
from agentic.functions._utils import parse_json
from agentic.runtime import Runtime


# ── Safety ────────���─────────────────────────────────────────────

_ALLOWED_BUILTINS = {
    "abs", "all", "any", "bool", "chr", "dict", "dir", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "hasattr",
    "hash", "hex", "id", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "oct", "ord", "pow",
    "print", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None", "ValueError", "TypeError", "KeyError",
    "IndexError", "RuntimeError", "Exception",
}

# Safe standard library modules that generated code may import
_ALLOWED_IMPORTS = {
    "os", "os.path", "sys", "json", "re", "math", "datetime",
    "pathlib", "collections", "itertools", "functools",
    "textwrap", "string", "io", "csv", "hashlib", "base64",
    "time", "random", "copy", "glob", "shutil", "tempfile",
    "urllib", "urllib.parse", "urllib.request",
    "typing", "dataclasses", "enum", "abc",
    "statistics", "decimal", "fractions",
}


def _make_safe_builtins() -> dict:
    """Create a restricted builtins dict."""
    import builtins
    safe = {}
    for name in _ALLOWED_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__import__"] = _safe_import
    return safe


def _safe_import(name, *args, **kwargs):
    """Allow only whitelisted standard library imports."""
    if name in _ALLOWED_IMPORTS:
        return __builtins__["__import__"](name, *args, **kwargs) if isinstance(__builtins__, dict) else __import__(name, *args, **kwargs)
    raise ImportError(
        f"Import '{name}' is not allowed in generated functions. "
        f"Allowed imports: {', '.join(sorted(_ALLOWED_IMPORTS))}"
    )


# ���─ Code extraction ───────────��────────────────────────────────

@traced
def extract_code(response: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    lines = response.strip().splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped.startswith("@agentic_function")
            or stripped.startswith("def ")
        ):
            return "\n".join(lines[i:]).strip()

    return response.strip()


# ─��� Validation ─────────────────────────────────────────────────

@traced
def validate_code(code: str, response: str) -> None:
    """Validate generated code: no disallowed imports, no async, valid syntax."""
    for line in (response + "\n" + code).split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            module = stripped.split()[1].split(".")[0].rstrip(",")
            # Allow framework imports (agentic_function, Runtime, etc.)
            if module == "agentic":
                continue
            if module not in _ALLOWED_IMPORTS:
                raise ValueError(
                    f"Import '{module}' is not allowed. Allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}\n{code}"
                )
        if stripped.startswith("async def ") or stripped.startswith("async "):
            raise ValueError(
                f"Generated code uses async (not allowed, use sync functions):\n{code}"
            )
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError as e:
        raise SyntaxError(
            f"Generated code has syntax errors:\n{code}\n\nError: {e}"
        ) from e


# ── Compilation ────────────────────────────────────────────────

@traced
def compile_function(code: str, runtime: Runtime, name: str = None) -> callable:
    """Execute code in sandbox and return the generated agentic_function."""
    namespace = {
        "__builtins__": _make_safe_builtins(),
        "agentic_function": agentic_function,
        "runtime": runtime,
    }
    try:
        exec(code, namespace)
    except Exception as e:
        raise ValueError(
            f"Generated code failed to execute:\n{code}\n\nError: {e}"
        ) from e

    compiled_func = find_function(namespace)
    if compiled_func is None:
        raise ValueError(
            f"Generated code does not contain an @agentic_function:\n{code}"
        )
    if name:
        compiled_func.__name__ = name
        compiled_func.__qualname__ = name

    # Bind runtime and any approved imports into the generated function's globals
    target_globals = None
    if hasattr(compiled_func, '__wrapped__'):
        target_globals = compiled_func.__wrapped__.__globals__
    elif hasattr(compiled_func, '_fn') and compiled_func._fn:
        target_globals = compiled_func._fn.__globals__
    elif hasattr(compiled_func, '__globals__'):
        target_globals = compiled_func.__globals__

    if target_globals is not None:
        for key, value in namespace.items():
            if key != "__builtins__":
                target_globals[key] = value
        target_globals['runtime'] = runtime

    return compiled_func


def find_function(namespace: dict) -> Optional[callable]:
    """Find the generated function in the namespace (agentic or regular)."""
    for obj_name, obj in namespace.items():
        if obj_name.startswith("_"):
            continue
        if isinstance(obj, agentic_function):
            return obj
    import types
    for obj_name, obj in namespace.items():
        if obj_name.startswith("_"):
            continue
        if isinstance(obj, types.FunctionType):
            return obj
    return None


def guess_name(code: str) -> Optional[str]:
    """Guess function name from generated code.

    Prefers the @agentic_function-decorated function name;
    falls back to the first def.
    """
    # Prefer the @agentic_function decorated function
    match = re.search(r"@agentic_function[^\n]*\s*def\s+(\w+)\s*\(", code)
    if match:
        return match.group(1)
    # Fallback: first def
    match = re.search(r"def\s+(\w+)\s*\(", code)
    return match.group(1) if match else None


def _is_agentic_function_decorator(node: ast.AST) -> bool:
    """Return True when an AST node looks like @agentic_function."""
    if isinstance(node, ast.Name):
        return node.id == "agentic_function"
    if isinstance(node, ast.Attribute):
        return node.attr == "agentic_function"
    if isinstance(node, ast.Call):
        return _is_agentic_function_decorator(node.func)
    return False


def _find_entry_function(tree: ast.Module):
    """Find the main generated function in a module AST."""
    candidates = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not candidates:
        return None

    for node in candidates:
        if any(_is_agentic_function_decorator(dec) for dec in node.decorator_list):
            return node
    return candidates[0]


def _rename_entry_function(code: str, new_name: str) -> str:
    """Rename the generated entry function and its self-references."""
    text = code.strip()
    if not text:
        return text

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text

    entry = _find_entry_function(tree)
    if entry is None or getattr(entry, "name", None) == new_name:
        return text

    old_name = entry.name

    class _Renamer(ast.NodeTransformer):
        def visit_Name(self, node):  # noqa: N802 - AST visitor API
            if node.id == old_name:
                return ast.copy_location(ast.Name(id=new_name, ctx=node.ctx), node)
            return node

    entry = _Renamer().visit(entry)
    entry.name = new_name
    ast.fix_missing_locations(entry)

    for index, node in enumerate(tree.body):
        if node is entry:
            tree.body[index] = entry
            break

    ast.fix_missing_locations(tree)

    try:
        return ast.unparse(tree).strip()
    except Exception:
        return text


def _canonicalize_function_code(code: str, fn_name: str) -> str:
    """Normalize generated code so the saved file matches the requested name."""
    return _rename_entry_function(code, fn_name)


def _delete_legacy_function_file(functions_dir: str, source_path: str | None, fn_name: str) -> None:
    """Remove a stale renamed file from agentic/functions/, if applicable."""
    if not source_path:
        return

    try:
        functions_dir_real = os.path.realpath(functions_dir)
        source_dir_real = os.path.realpath(os.path.dirname(source_path))
    except OSError:
        return

    if functions_dir_real != source_dir_real:
        return

    legacy_name = os.path.splitext(os.path.basename(source_path))[0]
    if not legacy_name or legacy_name == fn_name:
        return

    legacy_path = os.path.join(functions_dir, f"{legacy_name}.py")
    if os.path.exists(legacy_path):
        try:
            os.remove(legacy_path)
        except OSError:
            pass


# ── File I/O ───────────────────────────────────────────────────

@traced
def save_function(
    code: str,
    fn_name: str,
    description: str = None,
    source_path: str | None = None,
    action: str = "create",
) -> str:
    """Save generated function source code to agentic/functions/."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    functions_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "functions")
    os.makedirs(functions_dir, exist_ok=True)

    init_path = os.path.join(functions_dir, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w") as f:
            f.write("# Auto-generated agentic functions\n")

    # Keep module docstring short — truncate long descriptions
    short_desc = (description or "N/A").split("\n")[0].strip()
    if len(short_desc) > 80:
        short_desc = short_desc[:77] + "..."
    header = f'"""Auto-generated by {action}(). Description: {short_desc}"""\n\n'
    guard = "__test__ = False\n\n"
    imports = "from agentic.function import agentic_function\nfrom agentic.runtime import Runtime\n\n"
    canonical_code = _canonicalize_function_code(code, fn_name)
    file_content = header + guard + imports + _strip_leading_module_docstring(canonical_code) + "\n"

    filepath = os.path.join(functions_dir, f"{fn_name}.py")
    with open(filepath, "w") as f:
        f.write(file_content)

    _delete_legacy_function_file(functions_dir, source_path, fn_name)

    return filepath


def _strip_leading_module_docstring(code: str) -> str:
    """Remove a leading module docstring from generated code, if present."""
    text = code.strip()
    if not text:
        return text

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text

    if not tree.body:
        return text

    first = tree.body[0]
    value = getattr(first, "value", None)
    if not (
        isinstance(first, ast.Expr)
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    ):
        return text

    lines = text.splitlines()
    end_lineno = getattr(first, "end_lineno", first.lineno)
    if not end_lineno:
        return text

    remainder = lines[end_lineno:]
    return "\n".join(remainder).lstrip("\n").strip()


@traced
def save_skill_template(fn_name: str, description: str, code: str) -> str:
    """Create a basic template SKILL.md (no LLM needed)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    skill_dir = os.path.join(repo_root, "skills", fn_name)
    os.makedirs(skill_dir, exist_ok=True)

    skill_md = f"""---
name: {fn_name}
description: "{description}"
---

# {fn_name}

{description}

## Usage

```python
from agentic.functions.{fn_name} import {fn_name}
result = {fn_name}(...)
```
"""
    filepath = os.path.join(skill_dir, "SKILL.md")
    with open(filepath, "w") as f:
        f.write(skill_md)
    return filepath


# ── Source & error helpers ─────────────────────────────────────

def get_source(fn) -> str:
    """Get source code of a function.

    Supports three cases:
    1. Normal function — inspect.getsource()
    2. _FunctionStub (broken module) — __source__ attribute carries the full file
    3. Fallback — docstring or placeholder
    """
    # Plain string means the function couldn't be loaded — let the LLM know
    if isinstance(fn, str):
        return f"# Function '{fn}' not found — no source code available."

    # Stub from _load_function: carries full file source
    source_attr = getattr(fn, '__source__', None)
    if source_attr:
        return source_attr

    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        doc = getattr(fn, '__doc__', '') or ''
        name = getattr(fn, '__name__', 'unknown')
        if (
            inspect.isbuiltin(fn)
            or getattr(fn, "__module__", None) == "builtins"
            or _looks_like_api_doc(doc)
        ):
            return f"# Source not available for {name}"
        return f"# Source not available for {name}\n# Docstring: {doc}"


def _looks_like_api_doc(doc: str) -> bool:
    """Heuristically detect built-in/API reference docstrings.

    When source is unavailable, some callables expose long reference-style
    docstrings that are useful as API docs but noisy as prompt input.
    """
    if not doc:
        return False

    first_line = doc.strip().splitlines()[0].strip()
    if not first_line:
        return False

    # Signature-like first lines are a strong signal that this is reference
    # material rather than a user-authored docstring.
    if re.match(r"^[A-Za-z_][\w.]*\([^)]*\)\s*(?:->|:)\s*\S+", first_line):
        return True
    if re.match(r"^[A-Za-z_][\w.]*\([^)]*\)$", first_line):
        return True

    return False


def get_error_log(fn) -> str:
    """Build error log from function's Context (attempts + errors)."""
    ctx = getattr(fn, 'context', None)
    if ctx is None:
        return ""

    lines = []
    _collect_attempt_info(ctx, lines)
    return "\n".join(lines) if lines else ""


def _collect_attempt_info(ctx, lines: list, depth: int = 0):
    """Recursively collect attempt info from Context tree."""
    prefix = "  " * depth
    if ctx.attempts:
        for a in ctx.attempts:
            status = "OK" if a["error"] is None else "FAILED"
            lines.append(f"{prefix}{ctx.name} attempt {a['attempt']}: {status}")
            if a["error"]:
                lines.append(f"{prefix}  Error: {a['error']}")
            if a.get("reply") and a["error"]:
                lines.append(f"{prefix}  Reply was: {str(a['reply'])[:300]}")
    elif ctx.error:
        lines.append(f"{prefix}{ctx.name}: error: {ctx.error}")
    for child in ctx.children:
        _collect_attempt_info(child, lines, depth + 1)


# ── Follow-up ─────────────────────────────────────────────────

@agentic_function
def follow_up(question: str, runtime: Runtime) -> str:
    """向调用方提出问题以获取补充信息。

    当 LLM 判断信息不足以完成任务时，通过此函数向调用方提问。
    问题会沿调用链返回，由上层的 agent 或用户处理。

    Args:
        question: 需要回答的具体问题。
        runtime: LLM 运行时实例。

    Returns:
        问题本身（由调用方处理并在后续调用中提供答案）。
    """
    return question


# ── Clarify — pre-check before code generation ───────────────
# NOTE: Do NOT add shortcut functions here (e.g. _has_prior_context,
# _looks_obviously_vague). The LLM must always decide via runtime.exec().
# See commit 5094410 for rationale.

def _reply_looks_like_follow_up(reply: str) -> bool:
    """Heuristically detect a non-JSON clarification request.

    We keep the fallback conservative when the model fails to emit JSON:
    ask again only when the reply clearly asks for more information.
    """
    if not reply:
        return False

    lower = reply.lower()
    english_markers = (
        "question:",
        "unclear",
        "need more",
        "ambiguous",
        "please provide",
        "need clarification",
        "missing information",
    )
    chinese_markers = (
        "需要更多信息",
        "需要补充",
        "请提供",
        "不清楚",
        "有歧义",
        "缺少",
        "无法判断",
        "请说明",
    )
    return any(marker in lower for marker in english_markers) or any(
        marker in reply for marker in chinese_markers
    )


@agentic_function(summarize={"depth": 0, "siblings": 0})
def clarify(task: str, runtime: Runtime) -> dict:
    """Review a task before code generation and decide whether to ask the user first.

    Ask a clarifying question if:
    - The instruction is vague, investigative, or open-ended
    - Critical details are missing
    - The intent is ambiguous

    Exit (stop the task entirely) if:
    - The task is fundamentally impossible or nonsensical
    - The user's request doesn't match the current operation (e.g. asking to explain code in a fix flow)
    - After multiple failed attempts, the approach is clearly not working

    Proceed without asking if:
    - The instruction is specific and actionable
    - A prior Q/A pair already clarified the ambiguity

    Return JSON:
    - {"ready": false, "question": "your specific question"}
    - {"ready": true}
    - {"exit": true, "reason": "why this task should stop"}

    Args:
        task: The full task description (code, errors, instructions, etc.).
        runtime: LLM runtime instance.

    Returns:
        dict with "ready" (bool) and optionally "question" (str).
    """
    reply = runtime.exec(content=[
        {"type": "text", "text": (
            "You are reviewing a task before code generation begins.\n\n"
            "Ask a clarifying question if:\n"
            "- The instruction is vague, investigative, or open-ended "
            "(e.g. 'look into this', 'why is this happening', 'improve it')\n"
            "- Critical details are missing (what to change, expected behavior, constraints)\n"
            "- The intent is ambiguous (multiple valid interpretations)\n\n"
            "Exit (stop the task) if:\n"
            "- The task is fundamentally impossible or nonsensical\n"
            "- The request doesn't match the operation (e.g. asking to explain code in a fix flow)\n"
            "- After repeated failures, the approach is clearly not working\n\n"
            "Proceed without asking if:\n"
            "- The instruction is specific and actionable\n"
            "- A prior Q/A pair already clarified the ambiguity\n\n"
            "Return ONLY JSON:\n"
            '{"ready": false, "question": "your specific question"}\n'
            '{"ready": true}\n'
            '{"exit": true, "reason": "why this task should stop"}\n\n'
            f"Task:\n{task}"
        )},
    ])

    # Try to parse JSON from reply
    try:
        result = parse_json(reply)
        if "exit" in result or "ready" in result:
            return result
    except ValueError:
        pass

    # Fallback: if the reply looks like code, treat as ready (LLM skipped the JSON step)
    if reply.strip().startswith(("```", "def ", "import ", "@", "from ")):
        return {"ready": True}

    # If the reply clearly asks for more information, treat as not ready.
    if _reply_looks_like_follow_up(reply):
        # Extract just the question part, not the whole reply
        lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
        # Take only lines that look like natural language questions, not code
        question_lines = [l for l in lines if not l.startswith(("def ", "import ", "@", "```", "#"))]
        question = "\n".join(question_lines[:3]) if question_lines else reply[:200]
        return {"ready": False, "question": question}

    # Default: ready to proceed
    return {"ready": True}


# ── Base meta function ────────────────────────────────────────

@agentic_function
def generate_code(task: str, runtime: Runtime) -> str:
    """Generate or modify Python code following the Agentic Programming specification.

    This is the base meta function. All code generation/modification meta functions
    (create, fix, improve, etc.) call this function. The design specification below
    is the single source of truth.

    ── Framework basics ──

    In Agentic Programming, the function's docstring IS the LLM prompt.
    When runtime.exec() is called, the framework automatically sends:
    1. The full execution context (parent functions, sibling results)
    2. The current function's docstring
    3. The current function's parameters and their values
    4. Whatever the function passes in content=[...]

    So the docstring tells the LLM what to do; content ONLY carries data.

    ── Function type decision ──

    | Condition                     | Type                | @agentic_function? | runtime.exec()? |
    |-------------------------------|---------------------|--------------------|-----------------|
    | Needs LLM reasoning           | agentic function    | Yes                | Yes             |
    | Purely deterministic logic    | plain Python        | No                 | No              |

    ── Core rules ──

    - One @agentic_function calls runtime.exec() AT MOST once.
    - If you need multiple LLM calls, split into multiple @agentic_function
      and have one function call the others.
    - `agentic_function` and `runtime` are already available in scope.
    - Standard library imports allowed (os, json, re, pathlib, math, etc.).
    - No async/await.
    - Type hints on all parameters and return type.
    - Google-style docstring: one-line summary, Args, Returns.
    - Docstrings must contain only actionable instructions (output format,
      constraints, requirements). Never write filler like
      "You are a helpful assistant" or "Complete the task".

    ── Three usage patterns ──

    Pattern 1: Single task (leaf function)
    One function, one exec(), returns result. Most common case.

        @agentic_function
        def sentiment(text: str, runtime: Runtime) -> str:
            \"\"\"Analyze the sentiment of the given text.
            Return exactly one word: positive, negative, or neutral.

            Args:
                text: The text to analyze.

            Returns:
                One of: positive, negative, neutral.
            \"\"\"
            return runtime.exec(content=[
                {"type": "text", "text": text},
            ])

    Pattern 2: Fixed sequence (orchestrator function)
    Calls multiple @agentic_function in a fixed order decided by Python code.
    May optionally call exec() once for a final summary.

        @agentic_function
        def research_pipeline(task: str, runtime: Runtime) -> dict:
            \"\"\"Execute research pipeline: survey -> gaps -> ideas.

            Args:
                task: The research topic.
                runtime: LLM runtime instance.

            Returns:
                Dict with survey, gaps, and ideas.
            \"\"\"
            survey = survey_topic(topic=task, runtime=runtime)
            gaps = identify_gaps(survey=survey, runtime=runtime)
            ideas = generate_ideas(gaps=gaps, runtime=runtime)
            return {"survey": survey, "gaps": gaps, "ideas": ideas}

    Pattern 3: LLM-driven dispatch (LLM chooses which function to call)
    The function calls exec() once to let the LLM analyze the task and
    choose which sub-function to invoke. Python code then parses the
    LLM's choice and executes it. This requires:
    - A function registry (available dict)
    - build_catalog() to generate what the LLM sees
    - parse_action() to extract the LLM's choice
    - prepare_args() to merge all parameter sources

    Imports needed:
        from agentic.functions.build_catalog import build_catalog
        from agentic.functions.parse_action import parse_action
        from agentic.functions.prepare_args import prepare_args

    Full example:

        @agentic_function
        def summarize_text(text: str, runtime: Runtime) -> str:
            \"\"\"Summarize the given text into a concise paragraph.

            Args:
                text: The text to summarize.

            Returns:
                A concise summary.
            \"\"\"
            return runtime.exec(content=[
                {"type": "text", "text": text},
            ])

        @agentic_function
        def polish_text(text: str, style: str, runtime: Runtime) -> str:
            \"\"\"Polish text in the specified style.

            Args:
                text: The text to polish.
                style: One of "academic", "casual", "concise".

            Returns:
                Polished text.
            \"\"\"
            return runtime.exec(content=[
                {"type": "text", "text": f"Polish in {style} style:\\n\\n{text}"},
            ])

        @agentic_function
        def fix_call_params(func_name: str, missing: list, runtime: Runtime) -> dict:
            \"\"\"Fill in missing function call parameters.

            Args:
                func_name: Name of the function being called.
                missing: List of missing parameter names.

            Returns:
                Dict of parameter name -> value.
            \"\"\"
            reply = runtime.exec(content=[
                {"type": "text", "text": (
                    f"Function {func_name} is missing parameters: {missing}\\n"
                    "Provide them as JSON."
                )},
            ])
            from agentic.functions.parse_action import parse_action
            result = parse_action(reply)
            return result.get("args", result) if result else {}

        @agentic_function
        def research_assistant(task: str, runtime: Runtime) -> str:
            \"\"\"Analyze the task and choose the right sub-function.

            Args:
                task: User's task description.
                runtime: LLM runtime instance.

            Returns:
                Sub-function result, or LLM's direct reply.
            \"\"\"
            from agentic.functions.build_catalog import build_catalog
            from agentic.functions.parse_action import parse_action
            from agentic.functions.prepare_args import prepare_args

            # Function registry
            available = {
                "summarize_text": {
                    "function": summarize_text,
                    "description": "Summarize text into a concise paragraph",
                    "input": {
                        "text": {"source": "context"},
                    },
                    "output": {"summary": str},
                },
                "polish_text": {
                    "function": polish_text,
                    "description": "Polish text in a specified style",
                    "input": {
                        "text": {"source": "context"},
                        "style": {
                            "source": "llm",
                            "type": str,
                            "options": ["academic", "casual", "concise"],
                            "description": "Writing style",
                        },
                    },
                    "output": {"polished_text": str},
                },
            }

            catalog = build_catalog(available)

            reply = runtime.exec(content=[
                {"type": "text", "text": (
                    f"{task}\\n\\n"
                    "== Functions ==\\n"
                    "To call a function, append the JSON at the end.\\n"
                    "If no function is needed, reply directly.\\n\\n"
                    f"{catalog}"
                )},
            ])

            action = parse_action(reply)
            if not action or action["call"] not in available:
                return reply

            args = prepare_args(
                action=action,
                available=available,
                runtime=runtime,
                context={"text": task},
                fix_fn=fix_call_params,
            )
            result = available[action["call"]]["function"](**args)
            return result

    ── Function registry structure (for Pattern 3) ──

    available = {
        "function_name": {
            "function": the_callable,         # The actual function object
            "description": "What it does",    # Shown to LLM
            "input": {
                "param_name": {
                    "source": "context",      # Auto-filled by code, hidden from LLM
                },
                "other_param": {
                    "source": "llm",          # LLM must decide this
                    "type": str,              # Optional, for display
                    "options": ["a", "b"],    # Optional, constrain choices
                    "description": "...",     # Optional, shown to LLM
                },
            },
            "output": {"field": type},        # What the function returns
        },
    }

    Parameter sources:
    | source    | Who provides   | LLM sees it? |
    |-----------|----------------|--------------|
    | "context" | Python code    | No           |
    | "llm"     | LLM decides    | Yes          |
    | runtime   | Auto-injected  | No           |

    The LLM only outputs what it needs to decide. Everything else is
    auto-filled by code.

    ── Docstring rules ──

    The docstring IS the LLM prompt. It must be concise, actionable,
    and follow Google-style format exactly.

    Must include:
    - One-line summary of what the function does
    - Specific instructions (output format, constraints)
    - Args section with parameter descriptions
    - Returns section

    Must NOT include:
    - Role-playing ("You are a helpful assistant")
    - Empty directives ("Complete the task", "Do your best")
    - Data that's already in content

    Example:

        @agentic_function
        def sentiment(text: str, runtime: Runtime) -> str:
            \"\"\"Analyze the sentiment of the given text.
            Return exactly one word: positive, negative, or neutral.

            Args:
                text: The text to analyze.

            Returns:
                One of 'positive', 'negative', or 'neutral'.
            \"\"\"
            return runtime.exec(content=[
                {"type": "text", "text": text},
            ])

    ── Content rules ──

    runtime.exec(content=[...]) only carries data, never instructions:

        # CORRECT: data only
        runtime.exec(content=[{"type": "text", "text": text}])

        # WRONG: instructions in content
        runtime.exec(content=[{"type": "text", "text": f"Please analyze: {text}. Return one word."}])

    ── Robustness rules ──

    - Specific output format: define precisely in docstring, don't let LLM guess.
    - Text input: handle special characters, escaping, edge cases.
    - External state (files, APIs): validate inputs, clear error messages.
    - Result used by other functions: prefer structured data (dict/JSON).
    - Formatting matters: include example in docstring.

    ── Error handling for dispatch (Pattern 3) ──

    | Situation                     | Handling                              |
    |-------------------------------|---------------------------------------|
    | Function name not in registry | Return LLM's raw reply                |
    | Extra parameters from LLM    | Filter out, keep only valid ones      |
    | Missing required parameters   | Call fix_call_params to fill them in  |
    | JSON parse failure            | Return LLM's raw reply                |

    ── Scope restriction ──

    You are ONLY allowed to modify the target function's code.
    Do NOT read, modify, or create any other files in the project.
    Do NOT modify framework files, configuration, or other functions.
    Your entire output must be the fixed/generated function code — nothing else.

    ── Generated code boundaries ──

    The generated code will be saved into a module that already provides:
        from agentic.function import agentic_function
    So do NOT include these imports in your output.

    Your output should contain ONLY:
    - The @agentic_function decorator and function definition
    - Any helper functions/constants the main function needs
    - Additional imports the function needs (standard library, etc.)

    Do NOT include:
    - Module-level docstrings or comments (the framework adds its own)
    - `from agentic.function import agentic_function` (already provided)
    - `from agentic.runtime import Runtime` (already provided)

    Preserve the original function signature (name, parameters, type hints)
    unless the instruction explicitly asks to change it. In particular:
    - Keep `runtime: Runtime` — do not change to `Any` or other types
    - Keep the original parameter names and order
    - Keep the original @agentic_function decorator arguments (input={...})

    ── Output format ──

    Respond with ONLY the Python code inside a ```python code fence.
    No explanation, no commentary outside the fence.

    Args:
        task: Complete task description including all necessary data
              (source code, errors, instructions, etc.).
              Prior Q/A or retry feedback should be treated as context, not
              as a new instruction to repeat or re-ask.
        runtime: LLM runtime instance.

    Returns:
        str: LLM's raw reply containing the code in a ```python fence.
    """
    return runtime.exec(content=[
        {"type": "text", "text": task},
    ])
