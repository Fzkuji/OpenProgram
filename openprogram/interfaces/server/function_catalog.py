"""
Function discovery, metadata extraction, loading, and result formatting
for the web UI.

Moved out of server.py to keep the web server focused on routing.
"""

from __future__ import annotations

import logging
import copy
import importlib
import inspect
import json
import os
import re
from typing import Optional

from openprogram.runtime.agent.runtime import Runtime


# Base of the openprogram package.
import openprogram as _op_pkg

_log = logging.getLogger(__name__)
_PKG_BASE = os.path.dirname(os.path.abspath(_op_pkg.__file__))


# ---------------------------------------------------------------------------
# Function discovery — registry-driven (unified programs/_host/registry.py)
# ---------------------------------------------------------------------------

def _discover_functions() -> list[dict]:
    """Build the function list from the built-in Programs and the harnesses.

    Each built-in Program package (``_registry.builtin_program_modules``)
    holds its entries in ``<name>.py``, ``workflow.py`` or ``__init__.py``.
    Harness apps are git repositories installed with ``programs install``:
    owner-recorded directories whose public entries are resolved from
    Agent instance aliases and PROGRAM_ENTRIES in their entry file.
    """
    result: list[dict] = []

    # ``category`` is the Program kind: ``tool`` for the tool packages
    # under ``programs/tools/``, ``application`` for everything else
    # (built-in applications, user packages and harnesses).
    from openprogram.programs._host.registry import TOOL_PROGRAMS, iter_program_files
    for mod_name, full_path, is_harness in iter_program_files():
        if is_harness:
            info = _extract_function_info(full_path, None, "application")
            if info:
                result.append(info)
        else:
            category = "tool" if mod_name in TOOL_PROGRAMS else "application"
            result.extend(_extract_all_functions(full_path, category))

    # Program packages (the built-in ones and the user's) register through
    # the same Agent method registry. Include those registered callables
    # in the live function list so favorites and the chat launcher can
    # resolve them by name.
    result.extend(_discover_package_functions({r["name"] for r in result}))

    return result


def _discover_package_functions(seen: set[str]) -> list[dict]:
    """Build function-info rows for the registered entries of Program packages."""
    try:
        from openprogram.programs._host.runtime import _registry, _registry_lock
    except Exception:
        return []

    from openprogram.programs._host.registry import TOOL_PROGRAMS, builtin_program_modules
    builtin_modules = tuple(builtin_program_modules().values())
    tool_modules = tuple(TOOL_PROGRAMS.values())
    # Tools register from other threads while this runs; read a snapshot.
    with _registry_lock:
        registered_tools = list(_registry.items())
    out: list[dict] = []
    for name, registered in registered_tools:
        if name in seen or name.startswith("_"):
            continue
        fn = inspect.unwrap(getattr(registered, "_python_callable", None) or registered)
        module = str(getattr(fn, "__module__", "") or "")
        # A user package from ~/.openprogram/programs imports under its
        # own top-level name; the loader marks its tools with an owner.
        if not (
            module in builtin_modules
            or module.startswith(tuple(f"{m}." for m in builtin_modules))
            or getattr(registered, "_workflow_owner", None)
        ):
            continue
        if "._generation." in module or "._project." in module or "._runtime." in module:
            continue
        try:
            src = inspect.getsourcefile(fn)
        except (TypeError, OSError):
            src = None
        if not (src and os.path.isfile(src)):
            continue
        is_tool = module in tool_modules or module.startswith(tuple(f"{m}." for m in tool_modules))
        info = _extract_function_info(src, name, "tool" if is_tool else "application")
        if info:
            out.append(info)
    return out


def _program_source_entries(source: str) -> dict:
    """Resolve declared Agent methods and module entry aliases without imports."""
    import ast
    tree = ast.parse(source)
    classes = {}
    instances = {}
    aliases = {}
    functions = {node.name: node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    explicit = []
    agent_names = {"Agent"}
    statements = []
    def collect(body):
        for statement in body:
            statements.append(statement)
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                collect(statement.body)
    collect(tree.body)
    for node in statements:
        if isinstance(node, ast.ImportFrom):
            agent_names.update(alias.asname or alias.name for alias in node.names if alias.name == "Agent")
    for node in statements:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(base).split(".")[-1] for base in node.bases]
            if not any(base in agent_names or base in classes for base in bases):
                continue
            inherited = {}
            for base in bases:
                inherited.update(classes.get(base, {}))
            config = {}
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "method_options"
                    for target in statement.targets
                ) and isinstance(statement.value, ast.Dict):
                    for key, value in zip(statement.value.keys, statement.value.values):
                        if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.Dict):
                            options = {}
                            for option, data in zip(value.keys, value.values):
                                try:
                                    options[ast.literal_eval(option)] = ast.literal_eval(data)
                                except (ValueError, TypeError):
                                    continue
                            config[key.value] = options
            methods = dict(inherited)
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[method.name] = (method, config.get(method.name, {}), node.name)
            classes[node.name] = methods
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if not isinstance(target, ast.Name) or value is None:
                    continue
                if target.id == "PROGRAM_ENTRIES" and isinstance(value, (ast.List, ast.Tuple)):
                    explicit = [ast.unparse(entry) for entry in value.elts]
                elif isinstance(value, ast.Call) and ast.unparse(value.func) in classes:
                    instances[target.id] = ast.unparse(value.func)
                elif isinstance(value, ast.Attribute):
                    instance = (instances.get(value.value.id) if isinstance(value.value, ast.Name)
                                else ast.unparse(value.value.func) if isinstance(value.value, ast.Call)
                                else None)
                    if instance in classes and value.attr in classes[instance]:
                        aliases[target.id] = classes[instance][value.attr]
                elif isinstance(value, ast.Name) and value.id in aliases:
                    aliases[target.id] = aliases[value.id]
    result = {}
    for name, record in aliases.items():
        method, options, owner = record
        if not name.startswith("_") and (not explicit or name in explicit):
            result[name] = {"node": method, "options": options, "owner": owner}
    for name in explicit:
        if name in functions:
            result[name] = {"node": functions[name], "options": {}, "owner": None}
        elif "." in name:
            instance, method = name.rsplit(".", 1)
            owner = instances.get(instance)
            if owner and method in classes[owner]:
                node, options, owner = classes[owner][method]
                result[options.get("name") or method] = {"node": node, "options": options, "owner": owner}
    return result


def _extract_input_meta(source: str, func_name: str) -> dict | None:
    """Read input metadata from the declared Agent method configuration."""
    try:
        entry = _program_source_entries(source).get(func_name)
    except SyntaxError:
        return None
    return entry["options"].get("input") if entry else None


# Parsed rows by (file, name, category). Discovery runs on every page
# connection and Programs request; re-parsing every Program's source each
# time held the server for ~100ms per call.
_INFO_CACHE: dict[tuple, tuple] = {}


def _source_version(filepath: str):
    try:
        stat = os.stat(filepath)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _registered_id(name: Optional[str]) -> int:
    from openprogram.programs._host.runtime import get as get_tool
    return id(get_tool(name)) if name else 0


def _extract_function_info(filepath: str, name: Optional[str], category: str) -> Optional[dict]:
    """Extract function name and docstring from a .py file. The row is reused
    while the file and the registered method it reads options from stay the
    same."""
    version = _source_version(filepath)
    key = (filepath, name, category)
    hit = _INFO_CACHE.get(key)
    if (version is not None and hit is not None and hit[0] == version
            and hit[1] == _registered_id(hit[2] and hit[2]["name"])):
        return copy.deepcopy(hit[2])
    info = _read_function_info(filepath, name, category)
    if version is not None:
        _INFO_CACHE[key] = (version, _registered_id(info and info["name"]), info)
    return copy.deepcopy(info)


def _read_function_info(filepath: str, name: Optional[str], category: str) -> Optional[dict]:
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        import ast
        entries = _program_source_entries(content)
        if name is None:
            name = next(iter(entries), None)
        if name is None:
            return None
        if name not in entries:
            # An explicit registry lookup can select an ordinary function.
            function = next((node for node in ast.parse(content).body
                             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and node.name == name), None)
            if function is None:
                return None
            entries[name] = {"node": function, "options": {}, "owner": None}
        entry = entries[name]
        function = entry["node"]
        full_doc = ast.get_docstring(function) or ""
        doc = full_doc.split("\n")[0] if full_doc else ""

        effective_category = category
        if category == "builtin" and "Auto-generated by " in content:
            effective_category = "generated"

        params: list[str] = []
        params_detail: list[dict] = []
        positional = [*function.args.posonlyargs, *function.args.args]
        defaults = {argument.arg: default for argument, default in zip(
            positional[-len(function.args.defaults):] if function.args.defaults else [],
            function.args.defaults,
        )}
        defaults.update({argument.arg: default for argument, default in zip(
            function.args.kwonlyargs, function.args.kw_defaults,
        ) if default is not None})
        for argument in [*positional, *function.args.kwonlyargs]:
            if argument.arg in {"self", "cls", "runtime", "rt", "llm"}:
                continue
            params.append(argument.arg)
            default = defaults.get(argument.arg)
            params_detail.append({
                "name": argument.arg,
                "type": ast.unparse(argument.annotation) if argument.annotation else "",
                "default": ast.unparse(default) if default is not None else None,
                "required": argument.arg not in defaults,
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
        from openprogram.programs._host.runtime import get as get_tool
        registered = get_tool(name)
        method_options = getattr(registered, "_method_options", None)
        if method_options is not None:
            input_meta = getattr(method_options, "input_meta", None) or input_meta
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
                    if "advanced" in meta:
                        pd["advanced"] = meta["advanced"]
                    if "label" in meta:
                        pd["label"] = meta["label"]

        from openprogram.runtime.agent.durable_steps import source_capability
        return {
            "name": name,
            "continuation": source_capability(content, name),
            "category": effective_category,
            "description": doc,
            "params": params,
            "params_detail": params_detail,
            "filepath": filepath,
            "mtime": os.path.getmtime(filepath),
        }
    except Exception:
        return None


_ENTRY_NAMES: dict[str, tuple] = {}


def _extract_all_functions(filepath: str, category: str) -> list[dict]:
    """Extract declared Agent method aliases and PROGRAM_ENTRIES from source."""
    version = _source_version(filepath)
    hit = _ENTRY_NAMES.get(filepath)
    if version is not None and hit is not None and hit[0] == version:
        names = hit[1]
    else:
        try:
            with open(filepath, encoding="utf-8") as source:
                names = list(_program_source_entries(source.read()))
        except (OSError, UnicodeError, SyntaxError):
            return []
        if version is not None:
            _ENTRY_NAMES[filepath] = (version, names)
    return [info for name in names
            if (info := _extract_function_info(filepath, name, category))]


def _inject_runtime(loaded_func, kwargs: dict, runtime: Runtime):
    """Inject runtime into function kwargs if the function accepts it."""
    unwrapped_func = loaded_func
    try:
        source = inspect.getsource(unwrapped_func)
    except (OSError, TypeError):
        source = ""
    if "runtime" in source:
        sig = inspect.signature(unwrapped_func)
        if "runtime" in sig.parameters and "runtime" not in kwargs:
            kwargs["runtime"] = runtime


def _format_result(result, action: str = "create") -> str:
    """Format function result for display."""
    if callable(result):
        result_name = getattr(result, '__name__', 'unknown')
        result_doc = (getattr(result, '__doc__', '') or '').strip().split('\n')[0]
        try:
            result_sig = inspect.signature(result)
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
            from openprogram.interfaces.server.server import _broadcast as _bc
            _bc(json.dumps({"type": "functions_list", "data": _discover_functions()}, default=str))
        except Exception:
            # No server running (CLI use) or no clients; they refetch the list
            # on load.
            _log.debug("functions_list broadcast skipped", exc_info=True)
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
    import ast
    try:
        entry = _program_source_entries(source).get(func_name)
    except SyntaxError:
        return None
    if entry is None:
        return None
    doc = ast.get_docstring(entry["node"]) or ""
    return _FunctionStub(name=func_name, source=source, filepath=filepath, doc=doc)


def _load_function(func_name: str):
    """Load a function by name. Always reloads to pick up file changes.

    Search target: every built-in Program package in
    ``openprogram.programs._host.registry.builtin_program_modules`` and every
    owner-recorded harness. For each built-in module we import, reload,
    and look up ``func_name`` as a top-level attribute. Harness apps go
    through the same path but are loaded via ``spec_from_file_location``
    since their external dirs have hyphen names that can't be imported
    normally.

    If a module fails to import, falls back to a stub with the source
    code so edit() can still operate on it.
    """
    from openprogram.runtime.agent.call_state import auto_trace_module
    from openprogram.programs._host.registry import (
        builtin_program_modules, iter_program_files, _load_external_file,
    )
    modules = builtin_program_modules()

    for mod_name, fpath, is_harness in iter_program_files():
        try:
            if is_harness:
                # Re-execute the file so a hot-reload picks up edits the
                # user just made.
                source_dir = os.path.dirname(fpath)
                mod = _load_external_file(
                    source_dir, mod_name, os.path.basename(fpath),
                )
            else:
                mod = importlib.import_module(modules[mod_name])
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
