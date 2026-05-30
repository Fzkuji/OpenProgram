"""
Function discovery, metadata extraction, loading, and result formatting
for the web UI.

Moved out of server.py to keep the web server focused on routing.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import sys
from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


# Base of the openprogram package. Discovery scans under functions/agentics/.
import openprogram as _op_pkg
_PKG_BASE = os.path.dirname(os.path.abspath(_op_pkg.__file__))


# ---------------------------------------------------------------------------
# Function discovery — registry-driven (unified functions/_registry.py)
# ---------------------------------------------------------------------------

def _discover_functions() -> list[dict]:
    """Scan openprogram/functions/agentics/ to build the function list.

    Each agentic function lives in its own directory with code in
    ``__init__.py``. Harness apps (e.g. Research-Agent-Harness) are
    symlinked subdirectories with a ``main.py`` entry point — the
    function name is extracted from the @agentic_function decorator
    inside that entry file.
    """
    result: list[dict] = []
    base = _PKG_BASE

    # All registered agentic functions live under agentics/. The
    # ``is_harness`` flag (from the (module_name, file_override) shape
    # of AGENTIC_MODULES) categorises an entry as a harness app vs a
    # plain agentic function — this is what the UI uses to render the
    # "app" landing tile differently from regular entries. There's no
    # more buildin/third_party split; that distinction is gone after
    # the function-calling unification.
    from openprogram.functions._registry import iter_agentic_files
    from openprogram.functions import agentics as _agentics_pkg
    import os as _os
    agentics_dir = _os.path.dirname(_agentics_pkg.__file__)
    for mod_name, full_path, is_harness in iter_agentic_files(agentics_dir):
        if is_harness:
            info = _extract_function_info(full_path, None, "app")
            if info:
                result.append(info)
        else:
            infos = _extract_all_functions(full_path, "agentic")
            result.extend(infos)

    # First-party *programs* — pip-installed harnesses (gui_harness /
    # research_harness / wiki_agent_harness). These register into the
    # @agentic_function registry on import but their source lives in
    # site-packages, not under agentics/, so the filesystem walk above
    # misses them. Surface each installed program's entry point by
    # introspecting the registry and parsing its real source file (so the
    # editor / welcome screen treat it exactly like a bundled app).
    result.extend(_discover_program_functions({r["name"] for r in result}))

    return result


def _discover_program_functions(seen: set[str]) -> list[dict]:
    """Build function-info dicts for installed first-party programs.

    ``seen`` is the set of names already discovered on disk, so a program
    that is *also* symlinked locally isn't listed twice.
    """
    out: list[dict] = []
    try:
        from openprogram.functions._programs import installed_programs
        from openprogram.agentic_programming.function import _registry
    except Exception:
        return out
    for prog in installed_programs():
        if prog.function in seen:
            continue
        reg = _registry.get(prog.function)
        if reg is None:
            continue
        fn = getattr(reg, "_fn", None) or reg
        try:
            src = inspect.getsourcefile(fn)
        except (TypeError, OSError):
            src = None
        if not (src and os.path.isfile(src)):
            continue
        info = _extract_function_info(src, prog.function, "app")
        if info:
            out.append(info)
    return out


def _extract_input_meta(source: str, func_name: str) -> dict | None:
    """Extract input={...} from @agentic_function(input={...}) decorator via AST."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                callee = dec.func
                callee_name = ""
                if isinstance(callee, ast.Name):
                    callee_name = callee.id
                elif isinstance(callee, ast.Attribute):
                    callee_name = callee.attr
                if callee_name != "agentic_function":
                    continue
                for kw in dec.keywords:
                    if kw.arg == "input":
                        try:
                            return ast.literal_eval(kw.value)
                        except (ValueError, TypeError):
                            return None
    return None


def _extract_workdir_mode(source: str, func_name: str) -> str:
    """Extract workdir_mode from @agentic_function(workdir_mode=...) via AST.

    Returns 'optional' (default), 'hidden', or 'required'.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "optional"

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            callee = dec.func
            callee_name = ""
            if isinstance(callee, ast.Name):
                callee_name = callee.id
            elif isinstance(callee, ast.Attribute):
                callee_name = callee.attr
            if callee_name != "agentic_function":
                continue
            for kw in dec.keywords:
                if kw.arg == "workdir_mode":
                    try:
                        val = ast.literal_eval(kw.value)
                        if val in ("hidden", "optional", "required"):
                            return val
                    except (ValueError, TypeError):
                        pass
    return "optional"


def _extract_function_info(filepath: str, name: Optional[str], category: str) -> Optional[dict]:
    """Extract function name and docstring from a .py file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        if name is None:
            for match in re.finditer(r"@agentic_function[\s\S]*?def\s+(\w+)\s*\(", content):
                if not match.group(1).startswith("_"):
                    name = match.group(1)
                    break
            if name is None:
                match = re.search(r"@agentic_function[\s\S]*?def\s+(\w+)\s*\(", content)
                if not match:
                    return None
                name = match.group(1)

        doc = ""
        full_doc = ""
        func_doc_pattern = rf'def\s+{re.escape(name)}\s*\([^)]*\)[^:]*:\s*\n\s*(?:\'\'\'|""")(.+?)(?:\'\'\'|""")'
        func_doc_match = re.search(func_doc_pattern, content, re.DOTALL)
        if func_doc_match:
            full_doc = func_doc_match.group(1).strip()
            doc = full_doc.split("\n")[0]
        elif '"""' in content:
            start = content.index('"""') + 3
            end = content.index('"""', start)
            full_doc = content[start:end].strip()
            doc = full_doc.split("\n")[0]

        effective_category = category
        if category == "builtin" and "Auto-generated by " in content:
            effective_category = "generated"

        params: list[str] = []
        params_detail: list[dict] = []
        pattern = rf"def\s+{re.escape(name)}\s*\(([^)]*)\)"
        match = re.search(pattern, content)
        if match:
            param_str = match.group(1)
            for p in param_str.split(","):
                p = p.strip()
                if p and p != "self" and not p.startswith("*"):
                    pname = p.split(":")[0].split("=")[0].strip()
                    if pname:
                        params.append(pname)
                        ptype = ""
                        if ":" in p:
                            type_part = p.split(":", 1)[1]
                            if "=" in type_part:
                                ptype = type_part.split("=", 1)[0].strip()
                            else:
                                ptype = type_part.strip()
                        pdefault = None
                        has_default = False
                        if "=" in p:
                            default_str = p.rsplit("=", 1)[1].strip()
                            has_default = True
                            pdefault = default_str
                        params_detail.append({
                            "name": pname,
                            "type": ptype,
                            "default": pdefault,
                            "required": not has_default,
                            "description": "",
                        })

        if full_doc:
            # Capture everything after `Args:` until the next docstring
            # section (Returns:, Raises:, Yields:, Examples:, ...) or
            # end of docstring. The old regex required each line to
            # start with `\s+\w+`, which excluded continuation lines
            # that start with non-word chars like `"phd"` or `(int)`,
            # cutting descriptions short.
            args_match = re.search(
                r'Args:\s*\n(.*?)(?:\n\s*(?:Returns|Return|Raises|Yields|Example|Examples|Note|Notes|Attributes)\s*:|\Z)',
                full_doc,
                re.DOTALL,
            )
            if args_match:
                args_block = args_match.group(1)
                args_lines = args_block.splitlines()
                # Google-style continuation lines are indented deeper
                # than the parameter key. Collect them and join with a
                # single space so the description reads as one sentence
                # instead of getting truncated at the first newline.
                for pd in params_detail:
                    head_re = re.compile(
                        rf'^(\s+){re.escape(pd["name"])}'
                        rf'(?:\s*\([^)]*\))?\s*:\s*(.*)'
                    )
                    desc_parts: list[str] = []
                    head_indent: int | None = None
                    for line in args_lines:
                        if head_indent is None:
                            m = head_re.match(line)
                            if not m:
                                continue
                            head_indent = len(m.group(1))
                            first = m.group(2).strip()
                            if first:
                                desc_parts.append(first)
                            continue
                        if not line.strip():
                            continue
                        indent = len(line) - len(line.lstrip())
                        if indent <= head_indent:
                            break
                        desc_parts.append(line.strip())
                    if desc_parts:
                        pd["description"] = " ".join(desc_parts)

        input_meta = _extract_input_meta(content, name)
        if input_meta:
            for pd in params_detail:
                if pd["name"] in input_meta:
                    meta = input_meta[pd["name"]]
                    if "description" in meta:
                        pd["description"] = meta["description"]
                    if "placeholder" in meta:
                        pd["placeholder"] = meta["placeholder"]
                    if "multiline" in meta:
                        pd["multiline"] = meta["multiline"]
                    if "options" in meta:
                        pd["options"] = meta["options"]
                    if "options_from" in meta:
                        pd["options_from"] = meta["options_from"]
                    if "hidden" in meta:
                        pd["hidden"] = meta["hidden"]
                    if "label" in meta:
                        pd["label"] = meta["label"]

        workdir_mode = _extract_workdir_mode(content, name)

        return {
            "name": name,
            "category": effective_category,
            "description": doc,
            "params": params,
            "params_detail": params_detail,
            "filepath": filepath,
            "mtime": os.path.getmtime(filepath),
            "workdir_mode": workdir_mode,
        }
    except Exception:
        return None


def _extract_all_functions(filepath: str, category: str) -> list[dict]:
    """Extract ALL @agentic_function-decorated functions from a .py file.

    Decorator may span multiple lines (e.g. ``@agentic_function(input={...})``
    with a multi-line dict literal), so the regex uses ``[\\s\\S]*?`` and
    ``re.DOTALL`` to span linebreaks between ``@agentic_function`` and
    ``def``. Falls back to the parent-directory name when the discovered
    file is an ``__init__.py`` (the new agentics layout puts each
    function in its own dir with code in ``__init__.py``, so the bare
    file basename is always "__init__" and useless as a name).
    """
    results: list[dict] = []
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        # Anchor ``@agentic_function`` to start-of-line (with only
        # leading whitespace). Otherwise the regex also matches
        # mentions inside docstrings / comments like "in an
        # @agentic_function body" and then picks up the next def below
        # — yielding bogus entries (e.g. ``set_ask_user`` from
        # ``ask_user/__init__.py``, whose module docstring mentions
        # the decorator).
        for match in re.finditer(
            r"^[ \t]*@agentic_function[\s\S]*?def\s+(\w+)\s*\(",
            content,
            re.MULTILINE,
        ):
            name = match.group(1)
            if name.startswith("_"):
                continue
            info = _extract_function_info(filepath, name, category)
            if info:
                results.append(info)

        if not results:
            basename = os.path.splitext(os.path.basename(filepath))[0]
            if basename == "__init__":
                # Fall back to the containing directory name —
                # agentics/<name>/__init__.py convention.
                basename = os.path.basename(os.path.dirname(filepath))
            info = _extract_function_info(filepath, basename, category)
            if info:
                results.append(info)
    except Exception:
        pass
    return results


def _inject_runtime(loaded_func, kwargs: dict, runtime: Runtime):
    """Inject runtime into function kwargs if the function accepts it."""
    unwrapped_func = loaded_func._fn if hasattr(loaded_func, '_fn') else loaded_func
    try:
        source = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        source = ""
    if "runtime" in source:
        sig = inspect.signature(unwrapped_func)
        if "runtime" in sig.parameters and "runtime" not in kwargs:
            kwargs["runtime"] = runtime
        elif hasattr(loaded_func, '_fn') and loaded_func._fn:
            loaded_func._fn.__globals__['runtime'] = runtime


def _format_result(result, action: str = "create") -> str:
    """Format function result for display."""
    if callable(result):
        result_name = getattr(result, '__name__', 'unknown')
        result_doc = (getattr(result, '__doc__', '') or '').strip().split('\n')[0]
        try:
            result_sig = inspect.signature(result._fn if hasattr(result, '_fn') else result)
            params = [p for p in result_sig.parameters if p not in ('runtime', 'callback', 'self')]
        except (ValueError, TypeError):
            params = []
        action_labels = {
            "create": "Created",
            "edit": "Edited",
            "improve": "Improved",
        }
        label = action_labels.get(action, "Created")
        msg = f"{label} function `{result_name}`."
        if params:
            param_str = ' '.join(p + '="..."' for p in params)
            msg += f"\nUsage: `run {result_name} {param_str}`"
        if result_doc:
            msg += f"\nDescription: {result_doc}"
        # Broadcast updated function list — late import to avoid circular dep
        try:
            from openprogram.webui.server import _broadcast as _bc
            _bc(json.dumps({"type": "functions_list", "data": _discover_functions()}, default=str))
        except Exception:
            pass
        return msg
    elif isinstance(result, dict) and "summary" in result:
        return result["summary"]
    elif isinstance(result, str):
        return result
    else:
        try:
            return json.dumps(result, indent=2, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)


# ---------------------------------------------------------------------------
# Function loading (with graceful stub fallback for broken modules)
# ---------------------------------------------------------------------------

class _FunctionStub:
    """Stand-in for a function whose module cannot be imported.

    Carries enough attributes (__name__, __doc__, __file__, __source__) for
    edit()/improve() to read source code and file path without needing a
    working import.
    """
    def __init__(self, name: str, source: str, filepath: str, doc: str = ""):
        self.__name__ = name
        self.__qualname__ = name
        self.__doc__ = doc
        self.__file__ = filepath
        self.__source__ = source

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            f"Function '{self.__name__}' cannot be called — its module failed to import."
        )


def _make_stub_from_file(func_name: str, filepath: str):
    """Read a .py file and build a _FunctionStub for the named function."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    if f"def {func_name}" not in source:
        return None

    doc = ""
    pattern = re.compile(
        rf'def\s+{re.escape(func_name)}\s*\([^)]*\)[^:]*:\s*'
        r'(?:\n\s+)?"""(.*?)"""',
        re.DOTALL,
    )
    match = pattern.search(source)
    if match:
        doc = match.group(1).strip()
    return _FunctionStub(name=func_name, source=source, filepath=filepath, doc=doc)


def _load_function(func_name: str):
    """Load a function by name. Always reloads to pick up file changes.

    Search target: every module listed in
    ``openprogram.functions._registry.AGENTIC_MODULES`` (covers both
    flat agentic functions and the harness apps under the unified
    ``functions/agentics/`` tree). For each registered module we import,
    reload, and look up ``func_name`` as a top-level attribute. Harness
    apps go through the same path but are loaded via
    ``spec_from_file_location`` since their external dirs have hyphen
    names that can't be imported normally.

    If a module fails to import, falls back to a stub with the source
    code so edit() can still operate on it.
    """
    from openprogram.agentic_programming.function import auto_trace_module
    from openprogram.functions._registry import (
        iter_agentic_files, _load_external_file,
    )
    from openprogram.functions import agentics as _agentics_pkg
    agentics_dir = os.path.dirname(_agentics_pkg.__file__)
    import importlib.util as _imputil

    for mod_name, fpath, is_harness in iter_agentic_files(agentics_dir):
        full_mod = f"openprogram.functions.agentics.{mod_name}"
        try:
            if is_harness:
                # Re-execute the file under its registered module name
                # so a hot-reload picks up edits the user just made.
                _load_external_file(
                    agentics_dir, mod_name,
                    os.path.relpath(fpath, agentics_dir),
                )
                mod = sys.modules.get(full_mod)
            else:
                mod = importlib.import_module(full_mod)
                importlib.reload(mod)
                auto_trace_module(
                    mod, trace_pkg=os.path.dirname(os.path.abspath(fpath))
                )
            fn = getattr(mod, func_name, None) if mod is not None else None
            if fn is not None:
                return fn
        except Exception:
            stub = _make_stub_from_file(func_name, fpath)
            if stub is not None:
                return stub
    return None
