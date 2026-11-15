"""Definition-time call scopes for explicitly authorized Program sources."""
from __future__ import annotations

import ast
import importlib.machinery
import inspect


_CAPTURE_NAME = "__openprogram_managed_definition__"


class _Definitions(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        return self._function(node)

    def visit_AsyncFunctionDef(self, node):
        return self._function(node)

    def _function(self, node):
        # Yield in a nested definition does not make its parent a generator.
        class Yields(ast.NodeVisitor):
            found = False
            def visit_Yield(self, item):
                self.found = True
            visit_YieldFrom = visit_Yield
            def visit_FunctionDef(self, item):
                pass
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_Lambda(self, item):
                pass
        yields = Yields()
        for statement in node.body:
            yields.visit(statement)
        self.generic_visit(node)
        if not yields.found:
            node.decorator_list.insert(0, ast.copy_location(
                ast.Name(id=_CAPTURE_NAME, ctx=ast.Load()), node,
            ))
        return node


def compile_managed_source(source, filename):
    tree = _Definitions().visit(ast.parse(source, filename=filename))
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec", dont_inherit=True)


class ManagedSourceLoader(importlib.machinery.SourceFileLoader):
    """Transform only the source selected by the authorized source finder.

    Source is compiled on each import. A bytecode cache cannot bypass capture.
    Existing decorators execute in their original order, before capture.
    Generators retain normal Python behavior and do not create call scopes.
    """
    def __init__(self, fullname, path, *, entry_owned=True):
        super().__init__(fullname, path)
        self.entry_owned = entry_owned

    def get_code(self, fullname):
        source = self.get_data(self.path)
        return compile_managed_source(source, self.path)

    def exec_module(self, module):
        from openprogram.runtime.agent.call_scope import managed_function, capture_suspended
        from functools import partial
        # Keep the binding: nested definitions can execute after module import.
        module.__dict__[_CAPTURE_NAME] = partial(managed_function, entry_owned=self.entry_owned)
        token = capture_suspended.set(True)
        try:
            super().exec_module(module)
        finally:
            capture_suspended.reset(token)


def register_public_entries(module, entrypoint=None, *, require_exports=False, summary=None):
    """Adapt only explicitly exported functions to the existing tool registry.

    ``summary`` describes an entrypoint whose function has no docstring, as a
    published Workflow's project summary does. Returns the registered tool
    names."""
    from openprogram.runtime.agent.agent_method import wrap_agent_method, register_agent_method
    from openprogram.runtime.agent.call_scope import managed_function
    if require_exports and not hasattr(module, "PROGRAM_ENTRIES"):
        raise ImportError(
            f"Program module {module.__name__} must export PROGRAM_ENTRIES. "
            "Use ordinary functions or bound Agent methods with the current OpenProgram API."
        )
    entries = list(getattr(module, "PROGRAM_ENTRIES", ()) or ())
    if entrypoint:
        entry = getattr(module, entrypoint, None)
        if entry is not None:
            entries.append(entry)
    replacements = {}
    registered = []
    for entry in entries:
        existing = getattr(entry, "_agent_tool", None)
        if existing is not None:
            if (summary and entrypoint and entry is getattr(module, entrypoint, None)
                    and not (entry.__doc__ or "").strip()):
                existing.description = summary
            registered.append(existing.name)
            continue
        if not (inspect.isfunction(entry) or inspect.ismethod(entry)):
            raise TypeError("Program entries must be Python functions or Agent methods")
        is_method = inspect.ismethod(entry)
        original = entry
        if not is_method and getattr(entry, "_is_managed_function", False):
            original = entry.__wrapped__
        if inspect.isgeneratorfunction(original) or inspect.isasyncgenfunction(original):
            raise TypeError("Program entries cannot be generator functions")
        if id(entry) not in replacements:
            metadata = getattr(entry, "_method_options", {})
            if not isinstance(metadata, dict):
                from dataclasses import fields, is_dataclass
                metadata = ({field.name: getattr(metadata, field.name) for field in fields(metadata)}
                            if is_dataclass(metadata) else {})
            owner = getattr(entry, "_agent_owner", None)
            if owner is None and is_method:
                owner = entry.__self__
            owner_metadata = getattr(owner, "method_options", {}).get(entry.__name__, {}) if owner is not None else {}
            metadata = {**owner_metadata, **metadata, **getattr(entry, "__agent_options__", {})}
            options = dict(metadata)
            if "as_tool" not in options and "tool" not in options:
                options["tool"] = True
            register_tool = options.get("as_tool")
            if register_tool is None:
                register_tool = options.get("tool", False)
            available_if = options.pop("available_if", None)
            available = True
            if available_if is not None:
                try:
                    available = bool(available_if())
                except Exception:
                    available = False
            execution_metadata = {key: value for key, value in metadata.items() if key != "available_if"}
            if entrypoint and entry is getattr(module, entrypoint, None):
                options.setdefault("name", entrypoint)
                if summary and not (original.__doc__ or "").strip():
                    options.setdefault("description", summary)
            # Agent methods already carry their scope and instance binding.
            if is_method or getattr(entry, "_is_agent_method", False):
                wrapper = entry
            elif execution_metadata:
                wrapper = wrap_agent_method(original, options)
            else:
                wrapper = entry if getattr(entry, "_is_managed_function", False) else managed_function(original)
            if available and register_tool:
                tool = register_agent_method(wrapper, options)
                if tool is not None:
                    tool._python_callable = wrapper
                    registered.append(tool.name)
                if not is_method:
                    wrapper._agent_tool = tool
            replacements[id(entry)] = wrapper
    for name, value in list(vars(module).items()):
        if id(value) in replacements:
            setattr(module, name, replacements[id(value)])
    if hasattr(module, "PROGRAM_ENTRIES"):
        module.PROGRAM_ENTRIES = [replacements.get(id(fn), fn) for fn in entries[:len(module.PROGRAM_ENTRIES)]]
    return registered


class ManagedSourceFinder:
    """Resolve exact approved packages and their own Python submodules."""
    def __init__(self):
        self.sources = {}

    def add(self, name, root, authorization=None):
        import os
        self.sources[name] = (os.path.realpath(root), authorization)

    def find_spec(self, fullname, path=None, target=None):
        import os
        import importlib.util
        package = next((name for name in self.sources
                        if fullname == name or fullname.startswith(name + ".")), None)
        if package is None:
            return None
        root, authorization = self.sources[package]
        if authorization is not None and not authorization():
            raise ModuleNotFoundError(f"Program source is no longer authorized: {fullname}")
        relative = fullname[len(package):].lstrip(".").split(".") if fullname != package else []
        base = os.path.join(root, *relative)
        filename = os.path.join(base, "__init__.py")
        is_package = os.path.isfile(filename)
        if not is_package:
            filename = base + ".py"
        if not os.path.isfile(filename):
            if os.path.isdir(base):
                if os.path.commonpath((os.path.realpath(base), root)) != root:
                    raise ModuleNotFoundError(f"Program module is outside its authorized source: {fullname}")
                namespace = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
                namespace.submodule_search_locations = [base]
                return namespace
            raise ModuleNotFoundError(f"Program module does not exist: {fullname}")
        if os.path.commonpath((os.path.realpath(filename), root)) != root:
            raise ModuleNotFoundError(f"Program module is outside its authorized source: {fullname}")
        return importlib.util.spec_from_file_location(
            fullname, filename, loader=ManagedSourceLoader(fullname, filename),
            submodule_search_locations=[base] if is_package else None,
        )


_program_source_finder = ManagedSourceFinder()


def install_program_source(package, root, authorization=None):
    """Install capture for a package selected by an authorized loader."""
    import sys
    _program_source_finder.add(package, root, authorization)
    if _program_source_finder not in sys.meta_path:
        sys.meta_path.insert(0, _program_source_finder)
