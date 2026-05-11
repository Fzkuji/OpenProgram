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


# Base of the openprogram package. Discovery scans under programs/functions/.
import openprogram as _op_pkg
_PKG_BASE = os.path.dirname(os.path.abspath(_op_pkg.__file__))


_SKIP_DIRS = {"libs", "vendor", "node_modules", "desktop_env",
              "test", "tests", "examples", "docs", "build", "dist"}


# ---------------------------------------------------------------------------
# Function discovery — scans agentic/meta_functions, agentic/functions, apps/
# ---------------------------------------------------------------------------

def _discover_functions() -> list[dict]:
    """Scan openprogram/programs/functions/third_party/ and openprogram/programs/functions/meta/ to build function list.

    Supports three kinds of entries in functions/:
      1. Single .py files (e.g. sentiment.py)
      2. Subdirectories with a main.py entry point (e.g. Research-Agent-Harness/main.py)
         The function name is extracted from the @agentic_function in main.py.
    """
    result: list[dict] = []
    base = _PKG_BASE

    # Meta functions
    meta_dir = os.path.join(base, "programs", "functions", "meta")
    if os.path.isdir(meta_dir):
        for f in sorted(os.listdir(meta_dir)):
            if f.endswith(".py") and not f.startswith("_"):
                info = _extract_function_info(os.path.join(meta_dir, f), f[:-3], "meta")
                if info:
                    result.append(info)

    # Built-in + third-party functions (single files)
    for subpkg, category in (("buildin", "builtin"), ("third_party", "generated")):
        fn_dir = os.path.join(base, "programs", "functions", subpkg)
        if not os.path.isdir(fn_dir):
            continue
        for f in sorted(os.listdir(fn_dir)):
            full_path = os.path.join(fn_dir, f)
            if f.endswith(".py") and not f.startswith("_"):
                infos = _extract_all_functions(full_path, category)
                result.extend(infos)

    # Apps — scan applications/ for subdirectories with main.py at any depth
    apps_dir = os.path.join(base, "programs", "applications")
    if os.path.isdir(apps_dir):
        for f in sorted(os.listdir(apps_dir)):
            full_path = os.path.join(apps_dir, f)
            if os.path.isdir(full_path) and not f.startswith(("_", ".")):
                found = False
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs
                               if not d.startswith(("_", "."))
                               and d not in _SKIP_DIRS]
                    if "main.py" in files:
                        main_py = os.path.join(root, "main.py")
                        info = _extract_function_info(main_py, None, "app")
                        if info:
                            result.append(info)
                            found = True
                            break
                        pkg_dir = root
                        for sub_root, sub_dirs, sub_files in os.walk(pkg_dir):
                            sub_dirs[:] = [d for d in sub_dirs
                                           if not d.startswith(("_", "."))
                                           and d not in _SKIP_DIRS]
                            for py_file in sorted(sub_files):
                                if py_file.endswith(".py") and not py_file.startswith("_"):
                                    info = _extract_function_info(
                                        os.path.join(sub_root, py_file), None, "app"
                                    )
                                    if info:
                                        result.append(info)
                                        found = True
                                        break
                            if found:
                                break
                        if found:
                            break

    return result


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
        with open(filepath) as f:
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
            args_match = re.search(r'Args:\s*\n((?:\s+\w+.*\n?)+)', full_doc)
            if args_match:
                args_block = args_match.group(1)
                for pd in params_detail:
                    arg_pat = rf'^\s+{re.escape(pd["name"])}(?:\s*\([^)]*\))?\s*:\s*(.+)'
                    arg_m = re.search(arg_pat, args_block, re.MULTILINE)
                    if arg_m:
                        pd["description"] = arg_m.group(1).strip()

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
    """Extract ALL @agentic_function decorated functions from a .py file."""
    results: list[dict] = []
    try:
        with open(filepath) as f:
            content = f.read()

        for match in re.finditer(r"@agentic_function[^\n]*\s*def\s+(\w+)\s*\(", content):
            name = match.group(1)
            if name.startswith("_"):
                continue
            info = _extract_function_info(filepath, name, category)
            if info:
                results.append(info)

        if not results:
            basename = os.path.splitext(os.path.basename(filepath))[0]
            info = _extract_function_info(filepath, basename, category)
            if info:
                results.append(info)
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _get_last_ctx(func):
    """Get _last_ctx from a function, checking wrapper for @agentic_function instances."""
    ctx = getattr(func, '_last_ctx', None)
    if ctx is None and hasattr(func, '_wrapper'):
        ctx = getattr(func._wrapper, '_last_ctx', None)
    if ctx is None and hasattr(func, 'context'):
        ctx = getattr(func, 'context', None)
    return ctx


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
# Tree lookups
# ---------------------------------------------------------------------------

def _find_node_by_path(tree: dict, path: str) -> Optional[dict]:
    """Find a node in a tree dict by its path."""
    if tree.get("path") == path:
        return tree
    for child in tree.get("children", []):
        result = _find_node_by_path(child, path)
        if result is not None:
            return result
    return None


def _find_in_tree(tree: dict, path: str) -> dict | None:
    """Find a node in a tree dict by path or name."""
    if not tree or not path:
        return None
    if tree.get("path") == path or tree.get("name") == path:
        return tree
    for child in tree.get("children", []):
        found = _find_in_tree(child, path)
        if found:
            return found
    return None


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
        with open(filepath, "r") as fh:
            source = fh.read()
    except OSError:
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

    Search order:
      1. meta/  — create/edit/improve/fix/create_app/create_skill
      2. buildin/, third_party/  — single-file function modules
      3. programs/applications/*/.../main.py  — app entry points (e.g. gui_agent)

    If a module fails to import, falls back to a stub with the source code so
    edit() can still operate on it.
    """
    from openprogram.agentic_programming.function import auto_trace_module

    fn_root = os.path.join(_PKG_BASE, "programs", "functions")

    # 1 + 2: meta / buildin / third_party — each is a proper subpackage
    for subpkg, trace_root in (
        ("meta", os.path.join(fn_root, "meta")),
        ("buildin", os.path.join(fn_root, "buildin")),
        ("third_party", os.path.join(fn_root, "third_party")),
    ):
        mod_name = f"openprogram.programs.functions.{subpkg}.{func_name}"
        try:
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)
            auto_trace_module(mod, trace_pkg=os.path.abspath(trace_root))
            fn = getattr(mod, func_name, None)
            if fn is not None:
                return fn
        except ImportError:
            continue
        except Exception:
            mod_file = os.path.join(trace_root, f"{func_name}.py")
            if os.path.isfile(mod_file):
                stub = _make_stub_from_file(func_name, mod_file)
                if stub is not None:
                    return stub

    # 2b: function name may be defined inside a sibling .py (not matching filename)
    for subpkg in ("buildin", "third_party"):
        sub_dir = os.path.join(fn_root, subpkg)
        if not os.path.isdir(sub_dir):
            continue
        for f in sorted(os.listdir(sub_dir)):
            if not (f.endswith(".py") and not f.startswith("_")):
                continue
            mod_name = f"openprogram.programs.functions.{subpkg}.{f[:-3]}"
            try:
                mod = importlib.import_module(mod_name)
                importlib.reload(mod)
                auto_trace_module(mod, trace_pkg=os.path.abspath(sub_dir))
                fn = getattr(mod, func_name, None)
                if fn is not None:
                    return fn
            except Exception:
                fpath = os.path.join(sub_dir, f)
                stub = _make_stub_from_file(func_name, fpath)
                if stub is not None:
                    return stub

    # 3: applications — scan for main.py entry points
    import importlib.util as _imputil
    apps_dir = os.path.join(_PKG_BASE, "programs", "applications")
    if os.path.isdir(apps_dir):
        for d in os.listdir(apps_dir):
            full_path = os.path.join(apps_dir, d)
            if not os.path.isdir(full_path) or d.startswith(("_", ".")):
                continue
            for root, dirs, files in os.walk(full_path):
                dirs[:] = [x for x in dirs
                           if not x.startswith(("_", ".")) and x not in _SKIP_DIRS]
                if "main.py" not in files:
                    continue
                main_py = os.path.join(root, "main.py")
                # Each harness expects its parent dir on sys.path so that
                # `from gui_harness.X import ...` or `from research_harness.Y import ...`
                # resolves. The harness's main.py sits at <app_root>/<pkg>/main.py,
                # so we add <app_root> (parent of pkg).
                harness_root = os.path.dirname(os.path.dirname(main_py))
                if harness_root not in sys.path:
                    sys.path.insert(0, harness_root)
                try:
                    spec = _imputil.spec_from_file_location(
                        f"openprogram.programs.applications.{d}.main", main_py
                    )
                    mod = _imputil.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    mod = sys.modules.get(mod.__name__, mod)
                    fn = getattr(mod, func_name, None)
                    if fn is not None:
                        return fn
                except Exception:
                    pass
    return None
