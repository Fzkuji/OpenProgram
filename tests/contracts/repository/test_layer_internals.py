"""Inside every layer, the parts import each other without going round in circles.

``test_layer_order`` keeps the layers lower triangular: a module imports its own
layer and the ones below. This contract applies the same rule one level down.
A layer's parts are its direct children, a subpackage or a module (``runtime.jobs``,
``state.paths``). If ``runtime.jobs`` imports ``runtime.conversation``, then
``runtime.conversation`` must not import ``runtime.jobs``, directly or through
other parts. So the parts can be put in an order where each imports only the
ones before it, and that order makes the layer's design matrix lower triangular.

Two places in a layer assemble it, and no part may import them:

* the layer's ``__init__``, which may re-export its parts;
* its ``lower_layers`` modules, which fill the ports of the layers below
  (``programs`` keeps its own in ``programs._host.lower_layers``).

They may import any part. A part that needs something from them imports the
part that defines it instead, and ``import openprogram.runtime.jobs`` reaching
the layer package on the way does not count.

The scan is the one in ``test_layer_order``, so it sees imports inside functions,
relative imports and string imports. A cycle has no exceptions: break it by
moving the code to the part it belongs to, or by having the higher part hand it
down through a port that the lower part owns.

Run ``python tests/contracts/repository/test_layer_internals.py [prefix]`` to list
the imports that close a cycle, and to print each layer's order when there are none.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_layer_order as layers  # noqa: E402

ASSEMBLY = "lower_layers"


def _parts() -> tuple[dict[str, list[tuple[str, int]]], set[str]]:
    paths = [p for p in sorted(layers.PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]
    modules = {layers._module_name(p) for p in paths}
    imports = {}
    for path in paths:
        if path.relative_to(layers.ROOT).as_posix() in layers.COMPOSITION_ROOTS:
            continue
        found = {(name, line) for name, line in layers._imported_modules(path) if name in modules}
        # ``from openprogram.runtime import jobs`` names the layer package only on
        # the way to its part.
        imports[path.relative_to(layers.ROOT).as_posix()] = sorted(
            (name, line) for name, line in found
            if name not in layers.LAYERS
            or not any(other.startswith(name + ".") and at == line for other, at in found)
        )
    return imports, modules


def part(name: str) -> tuple[int, str] | None:
    """The layer and the part of it that ``name`` belongs to; the layer's own
    package when ``name`` is the layer itself."""
    layer = layers._layer(name)
    if layer is None:
        return None
    prefix = layers.LAYERS[layer]
    rest = name[len(prefix):].lstrip(".")
    if not rest:
        return layer, prefix
    child = f"{prefix}.{rest.split('.')[0]}"
    if child in layers.LAYERS:  # ``host._platform`` is a layer of its own
        return None
    return layer, child


def _is_assembly(name: str) -> bool:
    return name in layers.LAYERS or name.rpartition(".")[2] == ASSEMBLY


def graph() -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """Imports between the parts of one layer, keyed by (importer, imported), and
    every import of a layer's assembly from one of its parts."""
    imports, _ = _parts()
    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    assembly: list[str] = []
    for relative, found in imports.items():
        module = layers._module_name(layers.ROOT / relative)
        source = part(module)
        if source is None or _is_assembly(source[1]) or _is_assembly(module):
            continue
        for name, line in found:
            target = part(name)
            if target is None or target[0] != source[0] or target[1] == source[1]:
                continue
            if _is_assembly(target[1]):
                assembly.append(f"{relative}:{line}: {name}")
            else:
                edges[(source[1], target[1])].append(f"{relative}:{line}: {name}")
    return edges, sorted(set(assembly))


def _cycles(nodes: set[str], edges) -> list[list[str]]:
    """Strongly connected groups of more than one part."""
    out: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        out[a].add(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    groups: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on.add(node)
        for nxt in sorted(out[node]):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            group = []
            while True:
                member = stack.pop()
                on.discard(member)
                group.append(member)
                if member == node:
                    break
            if len(group) > 1:
                groups.append(sorted(group))

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for node in sorted(nodes):
        if node not in index:
            visit(node)
    return groups


def cycle_imports() -> list[str]:
    """Every import between two parts of the same cycle, as ``file:line: name``."""
    edges, _ = graph()
    nodes = {n for pair in edges for n in pair}
    found: list[str] = []
    for group in _cycles(nodes, edges):
        members = set(group)
        for (a, b), lines in edges.items():
            if a in members and b in members:
                found.extend(f"{line}  [{a} -> {b}]" for line in lines)
    return sorted(found)


def order() -> dict[str, list[str]]:
    """Each layer's parts, each after every part it imports."""
    edges, _ = graph()
    needs: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for a, b in edges:
        needs[a].add(b)
        nodes.update((a, b))
    placed: list[str] = []
    left = set(nodes)
    while left:
        ready = sorted(n for n in left if not (needs[n] & left))
        if not ready:
            break
        placed.extend(ready)
        left.difference_update(ready)
    by_layer: dict[str, list[str]] = defaultdict(list)
    for name in placed:
        by_layer[layers.LAYERS[part(name)[0]]].append(name)
    return dict(by_layer)


def test_the_parts_of_every_layer_import_each_other_without_a_cycle():
    found = cycle_imports()
    assert found == [], (
        "these imports close a cycle between parts of one layer; move the code to "
        "the part it belongs to, or have the higher part hand it down through a "
        "port the lower part owns (do not add an exception):\n"
        + "\n".join(f"  {line}" for line in found)
    )


def test_no_part_imports_its_layers_assembly():
    _, found = graph()
    assert found == [], (
        "a part imports its layer's __init__ or lower_layers, which assemble the "
        "whole layer; import the part that defines the name instead:\n"
        + "\n".join(f"  {line}" for line in found)
    )


if __name__ == "__main__":  # ``python tests/contracts/repository/test_layer_internals.py [prefix]``
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    cycles = [line for line in cycle_imports() if line.startswith(prefix)]
    _, assembly = graph()
    assembly = [line for line in assembly if line.startswith(prefix)]
    print("\n".join(cycles + assembly))
    print(f"{len(cycles)} imports in cycles, {len(assembly)} imports of a layer's assembly",
          file=sys.stderr)
    if not cycles and not assembly and not prefix:
        for layer, parts in order().items():
            print(f"{layer}: " + " < ".join(p.rpartition('.')[2] for p in parts))
