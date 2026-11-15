"""Source-only Program package function graph. Never import analyzed code."""
from __future__ import annotations

import ast
import os
from collections import deque
from pathlib import Path

from ..catalog.programs_cache import parsed_source, watch
from ...function_catalog import _program_source_entries


def package_calls(root: Path, relative: str, entry_name: str | None = None) -> dict:
    watch(root)
    warnings: set[str] = set()
    functions: dict[str, tuple[Path, ast.AST, dict[str, str]]] = {}
    entries: list[str] = []
    owners: dict[str, str] = {}
    modules: dict[str, str] = {}
    aliases: dict[str, str] = {}
    sources: list[Path] = []
    # Analyze import packages, not repository artifacts (benchmarks, runs, tests).
    for package in sorted(root.iterdir()):
        watch(package)
        if package.is_symlink() or not (package / '__init__.py').is_file():
            continue
        for directory, names, files in os.walk(package, followlinks=False):
            watch(Path(directory))
            names[:] = sorted(n for n in names if not n.startswith('.') and n not in {
                '__pycache__', 'tests', 'benchmarks', 'build', 'dist', 'node_modules',
            } and not (Path(directory) / n).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix == '.py' and not path.is_symlink():
                    sources.append(path)
            if len(sources) > 200:
                warnings.add('source_file_limit')
                break
        if len(sources) > 200:
            break
    for path in sources[:200]:
        watch(path)
        try:
            if path.stat().st_size > 1_000_000:
                warnings.add('oversized_source')
                continue
            tree = parsed_source(path)
        except (OSError, UnicodeError, SyntaxError):
            warnings.add('source_parse_failed')
            continue
        parts = list(path.relative_to(root).with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
            package_parts = parts
        else:
            package_parts = parts[:-1]
        module = '.'.join(parts)
        imports: dict[str, str] = {}
        for item in tree.body:
            if isinstance(item, ast.Import):
                for alias in item.names:
                    imports[alias.asname or alias.name.split('.')[0]] = (
                        alias.name if alias.asname else alias.name.split('.')[0]
                    )
            elif isinstance(item, ast.ImportFrom):
                base = item.module or ''
                if item.level:
                    base = '.'.join(package_parts[:len(package_parts) - item.level + 1] + ([base] if base else []))
                for alias in item.names:
                    imports[alias.asname or alias.name] = f'{base}.{alias.name}'
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f'{module}.{item.name}'
                functions[key] = path, item, imports
                modules[key] = module
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        key = f'{module}.{item.name}.{method.name}'
                        functions[key] = path, method, imports
                        owners[key] = f'{module}.{item.name}'
                        modules[key] = module
        declared = _program_source_entries(path.read_text(encoding='utf-8'))
        for name, declaration in declared.items():
            method = declaration['node']
            owner = declaration['owner']
            target = f'{module}.{owner}.{method.name}' if owner else f'{module}.{method.name}'
            key = f'{module}.{name}'
            if target in functions:
                aliases[key] = target
                entries.append(key)
                functions[key] = functions[target]
                modules[key] = module
                if owner:
                    owners[key] = f'{module}.{owner}'
    if entry_name:
        entries = [key for key in entries if key.rsplit('.', 1)[-1] == entry_name]
    nodes = [{'id': relative, 'name': root.name, 'path': relative,
              'program_kind': 'application', 'entity_kind': 'harness', 'source_path': str(root), 'depth': 0}]
    edges: list[dict] = []
    seen = {relative}
    pending = deque((relative, key, 1, 'entry') for key in entries)
    while pending and len(nodes) < 256:
        parent, key, depth, kind = pending.popleft()
        node_id = f'{relative}::{key}'
        edge = {'source': parent, 'target': node_id, 'kind': kind}
        existing = next((e for e in edges if e['source'] == parent and e['target'] == node_id), None)
        if existing is None:
            edges.append(edge)
        elif kind == 'call':
            existing['kind'] = kind
        if node_id in seen:
            continue
        seen.add(node_id)
        path, function, imports = functions[key]
        nodes.append({'id': node_id, 'name': key.rsplit('.', 1)[-1],
                      'path': f'{relative}/{path.relative_to(root)}:{function.lineno}',
                      'program_kind': 'application' if key in owners else 'tool', 'depth': depth})
        module = modules[key]
        imports = dict(imports)
        local_names = {arg.arg for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
        local_names.update(
            node.id for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        )
        for item in ast.walk(function):
            if isinstance(item, ast.ImportFrom) and not item.level:
                for alias in item.names:
                    imports[alias.asname or alias.name] = f'{item.module}.{alias.name}'
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    imports[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]

        def visit(item: ast.AST, conditional: bool = False) -> None:
            if item is not function and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                return
            conditional = conditional or isinstance(item, (ast.If, ast.IfExp, ast.Match))
            if isinstance(item, ast.Call):
                name = ast.unparse(item.func)
                head, _, tail = name.partition('.')
                target = (owners[key] + f'.{tail}' if head in {'self', 'cls'} and key in owners
                          else imports.get(head, f'{module}.{head}') + (f'.{tail}' if tail else ''))
                target = next((alias for alias, resolved in aliases.items() if resolved == target), target)
                if target in functions:
                    pending.append((node_id, target, depth + 1, 'conditional' if conditional else 'call'))
                elif isinstance(item.func, (ast.Subscript, ast.Call)) or (
                    isinstance(item.func, ast.Name) and head in local_names
                ):
                    warnings.add('unresolved_dynamic_call')
            for child in ast.iter_child_nodes(item):
                visit(child, conditional)

        for statement in function.body:
            visit(statement)
    if pending:
        warnings.add('node_limit')
    if not entries:
        warnings.add('entry_not_resolved')
    return {'root': relative, 'nodes': nodes, 'edges': edges,
            'analysis_complete': not warnings, 'analysis_warnings': sorted(warnings),
            'analysis_kind': 'function_calls'}
