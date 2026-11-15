"""Choose the tests that cover a change instead of running the whole suite.

Usage (from the repository root):

    python -m scripts.testing.affected_tests                 # working tree vs HEAD
    python -m scripts.testing.affected_tests --base dev~3    # changes since a revision
    python -m scripts.testing.affected_tests path/a.py ...   # explicit paths
    python -m scripts.testing.affected_tests --explain       # why each test was picked
    python -m scripts.testing.affected_tests --run           # run the selection

Python: a static import graph of ``openprogram/``, ``tests/`` and ``scripts/``
(``import`` statements, relative imports, ``from package import submodule``,
and dotted string literals such as ``importlib.import_module("...")`` or
``monkeypatch.setattr("openprogram.x.y.name", ...)``, plus source paths that
tests read as text). From each changed module the selector walks reverse
imports for a bounded number of steps and does not walk through hub modules
(modules imported by very many files), because nearly every test reaches the
core through some hub. Test files named after a changed module are added, and
changed test files always run. Any non-empty change, wherever it is, runs the
contract tests.

Web: the same walk over ``apps/web`` imports; Web tests also count path
strings they read or bundle (``readFile``, ``esbuild`` entry points). Any
``.ts``/``.tsx`` change adds ``tsc --noEmit``. A worktree has no
``node_modules``: ``--run`` clones them from the main checkout (never links),
also when a selected Python test shells out to node. Checks that cannot run
are left out, named by count, and the exit status is 2.

Docs: every change runs ``scripts.docs_site.check`` over the same scope, so a
code change is told which pages it may have outdated. A change under ``docs/``
also runs the tests that read ``docs/`` or import ``scripts.docs_site``.

A real change must select tests. Selecting none, or a Web run that reports
zero tests, is a failure.
"""

from __future__ import annotations

import argparse
import ast
from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

PYTHON_ROOTS = ("openprogram", "tests", "scripts")
WEB_ROOT = "apps/web"
WEB_TESTS = "apps/web/tests"
CONTRACTS = "tests/contracts"

# A module imported by more files than this is a hub: reaching it does not
# select everything behind it. Measured on this repository: the import
# fan-in has a long tail (most modules < 10 importers, a few hundred-plus),
# and walking through the tail selects about 90% of all test files.
HUB_FAN_IN = 25
# Reverse-import steps from a changed module to a test file. One step is a
# test that imports the module; two is a test that imports an importer.
MAX_DEPTH = 3
# A changed hub reaches hundreds of importers; from it the walk takes fewer steps.
HUB_START_DEPTH = 2
# Web modules are smaller and more numerous; the same rule applies.
WEB_HUB_FAN_IN = 25
WEB_MAX_DEPTH = 3

# Module names too generic to identify a test by file name.
GENERIC_STEMS = {
    "__init__", "__main__", "base", "common", "core", "types", "utils", "util",
    "helpers", "models", "model", "schema", "_schema", "config", "constants",
    "errors", "main", "service", "shared", "store", "state", "registry", "api",
    "runner", "server", "client", "worker", "context", "session", "tools",
    "manager", "handlers", "routes", "index", "app", "page", "support",
}

WEB_SOURCE_SUFFIXES = (".ts", ".tsx", ".mjs", ".js", ".mts", ".cjs", ".jsx")
WEB_RESOLVE_SUFFIXES = ("", ".ts", ".tsx", ".mjs", ".js", "/index.ts", "/index.tsx", "/index.js")

# Root packages are required; the Web workspace has its own only when npm
# could not hoist them.
REQUIRED_NODE_TREE = "node_modules"
OPTIONAL_NODE_TREES = ("apps/web/node_modules",)
EXIT_UNAVAILABLE = 2
# A Python test that needs node and its packages says one of these.
_NEEDS_NODE = re.compile(r"""@electron/|node_modules|["'](?:node|npx|npm)["']""")


# ---------------------------------------------------------------- git input


def _git(root: Path, *arguments: str) -> list[str]:
    output = subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in output.splitlines() if line.strip()]


def is_linked_worktree(root: Path) -> bool:
    absolute = _git(root, "rev-parse", "--absolute-git-dir")[0]
    return Path(absolute) != (root / _git(root, "rev-parse", "--git-common-dir")[0]).resolve()


def changed_files(root: Path, base: str | None = None) -> list[str]:
    """Files changed against ``base`` (default HEAD), plus untracked files.

    A moved file lists both its old and new path. ``--base`` in the main
    checkout means the committed range only: the dirty tree there belongs to
    other sessions.
    """
    if base and not is_linked_worktree(root):
        return sorted(_git(root, "diff", "--name-only", "--no-renames", base, "HEAD"))
    paths = set(_git(root, "diff", "--name-only", "--no-renames", base or "HEAD"))
    paths.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def branch_point(root: Path, trunk: str = "dev") -> str | None:
    """Where this branch left ``trunk``, or None when it holds nothing of its own."""
    done = subprocess.run(["git", "merge-base", "HEAD", trunk], cwd=root, capture_output=True, text=True)
    point = done.stdout.strip()
    if done.returncode != 0 or point == _git(root, "rev-parse", "HEAD")[0]:
        return None
    return point


def repository_files(root: Path) -> list[str]:
    paths = set(_git(root, "ls-files", "--", *PYTHON_ROOTS, WEB_ROOT))
    paths.update(
        _git(root, "ls-files", "--others", "--exclude-standard", "--", *PYTHON_ROOTS, WEB_ROOT)
    )
    return sorted(path for path in paths if (root / path).is_file())


# ------------------------------------------------------------ python graph


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_python_test(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        pure.parts[0] == "tests"
        and pure.suffix == ".py"
        and pure.name.startswith("test_")
    )


@dataclass
class PythonGraph:
    modules: dict[str, str]  # dotted name -> path
    imports: dict[str, set[str]]  # path -> imported paths
    references: dict[str, set[str]]  # path -> non-Python repository paths it names
    imports_docs_site: set[str]

    @property
    def importers(self) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = {}
        for source, targets in self.imports.items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)
        return reverse


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, package: str) -> None:
        self.package = package
        self.names: set[str] = set()
        self.strings: set[str] = set()
        self.paths: set[str] = set()

    def _absolute(self, module: str | None, level: int) -> str:
        if level == 0:
            return module or ""
        parts = self.package.split(".") if self.package else []
        if level > 1:
            parts = parts[: len(parts) - (level - 1)]
        base = ".".join(parts)
        if module:
            return f"{base}.{module}" if base else module
        return base

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._absolute(node.module, node.level)
        if base:
            self.names.add(base)
        for alias in node.names:
            if alias.name != "*" and base:
                self.names.add(f"{base}.{alias.name}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and len(node.value) < 300:
            value = node.value.strip()
            if re.fullmatch(r"(openprogram|tests|scripts)(\.[A-Za-z_]\w*)+", value):
                self.strings.add(value)
            elif "/" in value and re.match(r"^(\./)?(openprogram|apps|scripts|docs|tests)/", value):
                self.paths.add(value.removeprefix("./"))

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # ROOT / "openprogram" / "runtime" / "x.py" -> openprogram/runtime/x.py
        if isinstance(node.op, ast.Div):
            parts: list[str] = []
            current: ast.AST = node
            while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
                right = current.right
                if isinstance(right, ast.Constant) and isinstance(right.value, str):
                    parts.append(right.value)
                else:
                    parts = []
                current = current.left
            if isinstance(current, ast.Constant) and isinstance(current.value, str):
                parts.append(current.value)
            if parts:
                joined = "/".join(reversed(parts))
                if re.match(r"^(openprogram|apps|scripts|docs|tests)(/|$)", joined):
                    self.paths.add(joined)
        self.generic_visit(node)


def build_python_graph(root: Path, files: Iterable[str]) -> PythonGraph:
    python_files = [
        path for path in files
        if path.endswith(".py") and PurePosixPath(path).parts[0] in PYTHON_ROOTS
    ]
    modules = {module_name(path): path for path in python_files}
    imports: dict[str, set[str]] = {}
    references: dict[str, set[str]] = {}
    docs_site_users: set[str] = set()

    def resolve(name: str) -> str | None:
        while name:
            if name in modules:
                return modules[name]
            if "." not in name:
                return None
            name = name.rsplit(".", 1)[0]
        return None

    for path in python_files:
        try:
            tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError, OSError):
            imports[path] = set()
            continue
        name = module_name(path)
        package = name if path.endswith("__init__.py") else name.rpartition(".")[0]
        collector = _ImportCollector(package)
        collector.visit(tree)
        targets: set[str] = set()
        for imported in collector.names | collector.strings:
            # Exact module first: "from pkg import sub" names pkg.sub; when
            # that is not a module, the longest module prefix is the target.
            target = resolve(imported)
            if target and target != path:
                targets.add(target)
            if imported.startswith("scripts.docs_site"):
                docs_site_users.add(path)
        named_paths: set[str] = set()
        for value in collector.paths:
            value = value.rstrip("/")
            if value.endswith(".py") and value in python_files and value != path:
                targets.add(value)
            else:
                named_paths.add(value)
            if value == "scripts/docs_site" or value.startswith("scripts/docs_site/"):
                docs_site_users.add(path)
        imports[path] = targets
        references[path] = named_paths
    return PythonGraph(modules, imports, references, docs_site_users)


# --------------------------------------------------------------- web graph


_WEB_SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|\brequire\s*\(\s*)(['"])([^'"\n]+)\1"""
)
_WEB_LITERAL = re.compile(r"""(['"`])((?:\.{1,2}/|@/)?[\w@.\-\[\]]+(?:/[\w@.\-\[\]]+)+)\1""")


def _web_resolve(root: Path, base_directory: str, specifier: str, known: set[str]) -> str | None:
    if specifier.startswith("@/"):
        candidate = f"{WEB_ROOT}/{specifier[2:]}"
    elif specifier.startswith("."):
        candidate = os.path.normpath(f"{base_directory}/{specifier}")
    else:
        candidate = f"{WEB_ROOT}/{specifier}"
    for suffix in WEB_RESOLVE_SUFFIXES:
        if f"{candidate}{suffix}" in known:
            return f"{candidate}{suffix}"
    return None


@dataclass
class WebGraph:
    imports: dict[str, set[str]]

    @property
    def importers(self) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = {}
        for source, targets in self.imports.items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)
        return reverse


def is_web_test(path: str) -> bool:
    return path.startswith(f"{WEB_TESTS}/") and path.endswith(".test.mjs")


def build_web_graph(root: Path, files: Iterable[str]) -> WebGraph:
    web_files = [
        path for path in files
        if path.startswith(f"{WEB_ROOT}/") and "/node_modules/" not in path
        and "/.next/" not in path
    ]
    known = set(web_files)
    imports: dict[str, set[str]] = {}
    for path in web_files:
        if not path.endswith(WEB_SOURCE_SUFFIXES):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        directory = str(PurePosixPath(path).parent)
        targets: set[str] = set()
        specifiers = {match.group(2) for match in _WEB_SPECIFIER.finditer(text)}
        if path.startswith(f"{WEB_TESTS}/"):
            # Tests also read sources as text and bundle entry points by path.
            specifiers |= {match.group(2) for match in _WEB_LITERAL.finditer(text)}
        for specifier in specifiers:
            target = _web_resolve(root, directory, specifier, known)
            if target is None and specifier.startswith("./") and path.startswith(f"{WEB_TESTS}/"):
                # esbuild stdin entries in tests resolve from the Web root.
                target = _web_resolve(root, WEB_ROOT, specifier, known)
            if target and target != path:
                targets.add(target)
        imports[path] = targets
    return WebGraph(imports)


# ---------------------------------------------------------------- selection


@dataclass
class Selection:
    python_tests: dict[str, str] = field(default_factory=dict)  # path -> reason
    web_tests: dict[str, str] = field(default_factory=dict)
    typecheck: list[str] = field(default_factory=list)  # files that need tsc
    docs_check: list[str] = field(default_factory=list)  # files the docs check covers
    # Arguments that name the scope for the docs check. None: the paths above.
    docs_scope: list[str] | None = None
    uncovered: list[str] = field(default_factory=list)  # changes no rule covers
    notes: list[str] = field(default_factory=list)

    def add_python(self, path: str, reason: str) -> None:
        self.python_tests.setdefault(path, reason)

    def add_web(self, path: str, reason: str) -> None:
        self.web_tests.setdefault(path, reason)


def _reverse_walk(
    starts: dict[str, str],
    importers: dict[str, set[str]],
    is_test,
    hub_fan_in: int,
    max_depth: int,
    hub_start_depth: int | None = None,
) -> dict[str, str]:
    """Breadth-first walk over reverse imports; returns test -> reason chain."""
    found: dict[str, str] = {}
    seen: dict[str, int] = {}
    queue: deque[tuple[str, int, str, int]] = deque()
    for start, origin in starts.items():
        limit = max_depth
        if hub_start_depth is not None and len(importers.get(start, ())) > hub_fan_in:
            limit = min(max_depth, hub_start_depth)
        queue.append((start, 0, origin, limit))
        seen[start] = 0
    while queue:
        node, depth, chain, limit = queue.popleft()
        if depth >= limit:
            continue
        # A hub that is itself the change still selects its direct importers;
        # a hub reached on the way does not expand further.
        if depth > 0 and len(importers.get(node, ())) > hub_fan_in:
            continue
        for importer in sorted(importers.get(node, ())):
            next_chain = f"{chain} <- {importer}"
            if is_test(importer):
                found.setdefault(importer, next_chain)
            if importer not in seen or seen[importer] > depth + 1:
                seen[importer] = depth + 1
                queue.append((importer, depth + 1, next_chain, limit))
    return found


def _tests_named_after(path: str, test_files: Iterable[str]) -> list[str]:
    stem = PurePosixPath(path).stem.lstrip("_")
    if f"_{stem}" in GENERIC_STEMS or stem in GENERIC_STEMS or len(stem) < 5:
        return []
    pattern = re.compile(rf"^test_{re.escape(stem)}(_[a-z0-9_]+)?\.py$")
    return [test for test in test_files if pattern.match(PurePosixPath(test).name)]


def select(
    changed: Iterable[str],
    root: Path = ROOT,
    files: list[str] | None = None,
    *,
    hub_fan_in: int = HUB_FAN_IN,
    max_depth: int = MAX_DEPTH,
    hub_start_depth: int | None = HUB_START_DEPTH,
    include_contracts: bool = True,
) -> Selection:
    changed = sorted(set(changed))
    files = repository_files(root) if files is None else files
    existing = set(files)
    selection = Selection()

    python_changes = [
        path for path in changed
        if path.endswith(".py") and PurePosixPath(path).parts[0] in PYTHON_ROOTS
    ]
    other_source_changes = [
        path for path in changed
        if PurePosixPath(path).parts[0] in ("openprogram", "scripts")
        and not path.endswith(".py")
    ]
    docs_changes = [path for path in changed if path.startswith("docs/")]
    web_changes = [path for path in changed if path.startswith(f"{WEB_ROOT}/")]

    if changed:
        selection.docs_check = list(changed)
        graph = build_python_graph(root, files)
        importers = graph.importers
        test_files = sorted(path for path in graph.imports if is_python_test(path))

        starts: dict[str, str] = {}
        for path in python_changes:
            pure = PurePosixPath(path)
            if is_python_test(path):
                if path in existing:
                    selection.add_python(path, "changed test file")
                continue
            if pure.name == "conftest.py" and pure.parts[0] == "tests":
                directory = str(pure.parent)
                if len(pure.parts) <= 3:
                    # tests/conftest.py and tests/<layer>/conftest.py apply to
                    # hundreds of files; every selected test exercises them.
                    selection.notes.append(
                        f"{path} applies to every test under {directory}/; "
                        "the selection exercises it, run that layer if the fixture itself changed behavior"
                    )
                    continue
                for test in test_files:
                    if test.startswith(f"{directory}/"):
                        selection.add_python(test, f"under changed {path}")
                continue
            if path not in existing:
                # Deleted module: tests that still import it must fail or be
                # updated; find them by name.
                name = module_name(path)
                for test, targets in graph.imports.items():
                    if is_python_test(test) and any(
                        text.startswith(name) for text in _imported_names(root, test)
                    ):
                        selection.add_python(test, f"imports deleted {name}")
                continue
            starts[path] = path
            for test in _tests_named_after(path, test_files):
                selection.add_python(test, f"named after {path}")

        for path in other_source_changes:
            referenced = False
            for test in test_files:
                if any(
                    path == reference or path.startswith(f"{reference}/")
                    for reference in graph.references.get(test, ())
                ):
                    selection.add_python(test, f"names path {path}")
                    referenced = True
            # Data files are loaded by the modules beside them.
            directory = str(PurePosixPath(path).parent)
            siblings = [
                module for module in graph.imports
                if str(PurePosixPath(module).parent) == directory
                and not module.endswith("__init__.py")
            ]
            for sibling in siblings:
                starts.setdefault(sibling, f"{path} (beside {sibling})")
            if not referenced and not siblings and not path.endswith("README.md"):
                selection.uncovered.append(path)

        found = _reverse_walk(
            starts, importers, is_python_test, hub_fan_in, max_depth, hub_start_depth
        )
        for test, chain in found.items():
            selection.add_python(test, f"imports {chain.split(' <- ')[0]}: {chain}")

        if docs_changes:
            for test in test_files:
                references = graph.references.get(test, ())
                if test in graph.imports_docs_site or any(
                    reference == "docs" or reference.startswith("docs/")
                    for reference in references
                ):
                    selection.add_python(test, "reads docs/ or uses scripts.docs_site")

        if include_contracts:
            for test in test_files:
                if test.startswith(f"{CONTRACTS}/"):
                    selection.add_python(test, "contracts always run")

    if web_changes:
        _select_web(root, files, existing, web_changes, selection)

    handled_roots = ("openprogram/", "scripts/", "tests/", "docs/", f"{WEB_ROOT}/")
    for path in changed:
        if not path.startswith(handled_roots):
            selection.uncovered.append(path)
    return selection


def _imported_names(root: Path, path: str) -> set[str]:
    try:
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    collector = _ImportCollector("")
    collector.visit(tree)
    return collector.names | collector.strings


def _select_web(
    root: Path,
    files: list[str],
    existing: set[str],
    web_changes: list[str],
    selection: Selection,
) -> None:
    graph = build_web_graph(root, files)
    importers = graph.importers
    starts: dict[str, str] = {}
    for path in web_changes:
        if path.endswith((".ts", ".tsx")):
            selection.typecheck.append(path)
        if is_web_test(path):
            if path in existing:
                selection.add_web(path, "changed test file")
            continue
        if path not in existing:
            selection.uncovered.append(path)
            continue
        starts[path] = path
    found = _reverse_walk(
        starts, importers, is_web_test, WEB_HUB_FAN_IN, WEB_MAX_DEPTH, HUB_START_DEPTH
    )
    for test, chain in found.items():
        selection.add_web(test, f"imports {chain.split(' <- ')[0]}: {chain}")
    for path in starts:
        if path.startswith(f"{WEB_TESTS}/"):
            continue  # test helpers only matter through the tests that use them
        if not any(
            reason.startswith(f"imports {path}:") for reason in selection.web_tests.values()
        ) and not path.endswith((".ts", ".tsx")):
            selection.uncovered.append(path)


# ----------------------------------------------------------------- commands


def python_executable(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    return ".venv/bin/python" if venv.exists() else sys.executable


def commands(selection: Selection, root: Path = ROOT) -> list[tuple[str, list[str]]]:
    """(working directory relative to root, argv) for each command to run."""
    planned: list[tuple[str, list[str]]] = []
    if selection.python_tests:
        planned.append((".", [
            python_executable(root), "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-n", "auto", "--timeout=300", *sorted(selection.python_tests),
        ]))
    if selection.web_tests:
        web_tests = sorted(
            PurePosixPath(path).relative_to(WEB_ROOT).as_posix() for path in selection.web_tests
        )
        planned.append((WEB_ROOT, [
            "node", "--no-warnings", "--experimental-strip-types", "--test",
            "--test-reporter=spec", *web_tests,
        ]))
    if selection.typecheck:
        planned.append((WEB_ROOT, ["npx", "tsc", "--noEmit", "-p", "."]))
    if selection.docs_check:
        scope = selection.docs_check if selection.docs_scope is None else selection.docs_scope
        planned.append((".", [python_executable(root), "-m", "scripts.docs_site.check", *scope]))
    return planned


def render_command(directory: str, argv: list[str]) -> str:
    command = shlex.join(argv)
    return command if directory == "." else f"(cd {shlex.quote(directory)} && {command})"


def suite_sizes(root: Path = ROOT, files: list[str] | None = None) -> tuple[int, int]:
    files = repository_files(root) if files is None else files
    return (
        sum(1 for path in files if is_python_test(path)),
        sum(1 for path in files if is_web_test(path)),
    )


# ------------------------------------------------------------ running them


def main_checkout(root: Path) -> Path | None:
    """The checkout whose .git this tree shares; ``root`` itself when not a worktree."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(common).parent


def _clone_tree(source: Path, target: Path) -> str | None:
    """Copy-on-write clone, renamed into place so a concurrent run never sees half."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.cloning-{os.getpid()}")
    flag = ["-c"] if sys.platform == "darwin" else ["--reflink=auto"]
    result = subprocess.run(
        ["cp", *flag, "-R", "-p", str(source), str(partial)], capture_output=True, text=True
    )
    if result.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        return f"cloning {source} failed: {result.stderr.strip() or result.returncode}"
    try:
        partial.rename(target)
    except OSError:
        # Another run got there first.
        shutil.rmtree(partial, ignore_errors=True)
        if not target.is_dir():
            return f"could not put the clone at {target}"
    return None


def prepare_web(root: Path) -> str | None:
    """Make the Web checks runnable here; returns why they cannot be, or None."""
    if shutil.which("node") is None:
        return "node is not on PATH"
    checkout = main_checkout(root)
    required = root / REQUIRED_NODE_TREE
    if not required.is_dir():
        source = checkout / REQUIRED_NODE_TREE if checkout else None
        if source is None or checkout == root or not source.is_dir():
            return f"{required} is missing and there is no main checkout copy to clone; run npm ci there"
        print(f"cloning {source} into this worktree", flush=True)
        failure = _clone_tree(source, required)
        if failure:
            return failure
    for relative in OPTIONAL_NODE_TREES:
        target = root / relative
        source = checkout / relative if checkout else None
        if not target.is_dir() and source is not None and checkout != root and source.is_dir():
            failure = _clone_tree(source, target)
            if failure:
                return failure
    return None


_WEB_TEST_COUNT = re.compile(r"^\s*(?:\S+\s+|#\s*)?tests\s+(\d+)\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def web_test_count(lines: Iterable[str]) -> int | None:
    """The total from a ``node --test`` summary; None when there is no summary."""
    total = None
    for line in lines:
        match = _WEB_TEST_COUNT.match(_ANSI.sub("", line))
        if match:
            total = int(match.group(1))
    return total


def _stream(command: list[str], directory: Path) -> tuple[int, list[str]]:
    """Run, echoing output as it comes, and return the last lines as well."""
    process = subprocess.Popen(
        command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )
    tail: deque[str] = deque(maxlen=40)
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line)
    return process.wait(), list(tail)


def needs_node(root: Path, test: str) -> bool:
    """Whether a Python test, or a helper beside it, runs node."""
    try:
        text = (root / test).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if _NEEDS_NODE.search(text):
        return True
    return any(_NEEDS_NODE.search(helper.read_text(encoding="utf-8", errors="replace"))
               for helper in (root / test).parent.glob("*.py")
               if not helper.name.startswith("test_") and helper.name != "__init__.py"
               and re.search(rf"\b{re.escape(helper.stem)}\b", text))


def _is_pytest(command: list[str]) -> bool:
    return command[1:3] == ["-m", "pytest"]


def _label(command: list[str]) -> str:
    if command[0] == "node":
        return "node --test"
    if command[0] == "npx":
        return "tsc"
    return "docs check" if "scripts.docs_site.check" in command else "pytest"


def run_planned(planned: list[tuple[str, list[str]]], root: Path = ROOT, *, docs_tool_blocks: bool = True) -> int:
    """Run everything that can run. 0 all passed, 1 a failure, 2 Web checks could not run.

    ``docs_tool_blocks`` is off in the main checkout without named paths: the
    docs tool failing there may come from another session's half-edited file.
    """
    web_commands = [command for directory, command in planned if directory == WEB_ROOT]
    node_tests = [arg for _directory, command in planned if _is_pytest(command) for arg in command
                  if arg.startswith("tests/") and needs_node(root, arg)]
    unavailable = prepare_web(root) if web_commands or node_tests else None
    failed: list[str] = []
    for directory, command in planned:
        label = _label(command)
        if unavailable and directory == WEB_ROOT:
            continue
        if unavailable and node_tests and _is_pytest(command):
            command = [arg for arg in command if arg not in node_tests]
            if not any(arg.startswith("tests/") for arg in command):
                continue
        print(f"\n$ {render_command(directory, command)}", flush=True)
        status, tail = _stream(command, root / directory)
        if label == "docs check" and status == 2:
            print("docs check could not run (exit 2): this is the tool, not a page", file=sys.stderr)
            if docs_tool_blocks:
                failed.append("docs check could not run")
        elif status != 0:
            failed.append(label)
        elif command[0] == "node" and not web_test_count(tail):
            print("node --test finished without running a test", file=sys.stderr)
            failed.append("node --test (0 tests)")
    if unavailable:
        files = sum(1 for command in web_commands if command[0] == "node" for arg in command if arg.endswith(".test.mjs"))
        typecheck = any(command[0] == "npx" for command in web_commands)
        print(
            f"\nCOULD NOT RUN {files} Web test files{' and the typecheck' if typecheck else ''}: {unavailable}",
            file=sys.stderr,
        )
    if unavailable and node_tests:
        print(f"\nCOULD NOT RUN {len(node_tests)} Python test files that need node: {unavailable}", file=sys.stderr)
    if failed:
        print("\nfailed: " + ", ".join(failed))
        return 1
    if unavailable:
        return EXIT_UNAVAILABLE
    print("\nall selected checks passed")
    return 0


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.testing.affected_tests",
        description="Print (or run) the tests that cover the changed files.",
    )
    parser.add_argument("paths", nargs="*", help="changed paths (default: git diff HEAD + untracked)")
    parser.add_argument("--base", help="compare against this revision instead of HEAD")
    parser.add_argument("--run", action="store_true", help="run the selected commands")
    parser.add_argument("--explain", action="store_true", help="show why each test was picked")
    arguments = parser.parse_args(argv)

    files = repository_files(ROOT)
    base = arguments.base
    changed = arguments.paths or changed_files(ROOT, base)
    linked = is_linked_worktree(ROOT)
    if not arguments.paths and not base and not changed and linked:
        # Commits in a worktree pass no hook: judge the branch before it is merged.
        base = branch_point(ROOT)
        if base:
            changed = changed_files(ROOT, base)
            print(f"no uncommitted changes: checking this branch against dev ({base[:10]})")
    selection = select(changed, ROOT, files)
    if not arguments.paths:
        # Without named paths the docs check works out the scope itself, and
        # only it knows a shared checkout's dirty tree is not this session's.
        selection.docs_scope = ["--base", base] if base else []
    planned = commands(selection, ROOT)
    python_total, web_total = suite_sizes(ROOT, files)

    print(f"changed files: {len(changed)}")
    print(f"python test files: {len(selection.python_tests)} of {python_total}")
    print(f"web test files: {len(selection.web_tests)} of {web_total}")
    print(f"typecheck: {'yes' if selection.typecheck else 'no'}")
    print(f"docs check: {'yes' if selection.docs_check else 'no'}")
    for note in selection.notes:
        print(f"note: {note}")
    if selection.uncovered:
        print("not covered by any selection rule:")
        for path in sorted(set(selection.uncovered)):
            print(f"  {path}")
    if arguments.explain:
        for label, chosen in (("python", selection.python_tests), ("web", selection.web_tests)):
            if chosen:
                print(f"\n{label} tests:")
                for path in sorted(chosen):
                    print(f"  {path}\n      {chosen[path]}")
    if not changed:
        print("\nno changes, nothing to run")
        return 0
    if not selection.python_tests and not selection.web_tests:
        print(f"\nno test covers these {len(changed)} changes; the selector is wrong", file=sys.stderr)
        return 1
    print("\ncommands:")
    for directory, command in planned:
        print(f"  {render_command(directory, command)}")
    if not arguments.run:
        return 0
    return run_planned(planned, ROOT, docs_tool_blocks=bool(arguments.paths) or linked)


if __name__ == "__main__":
    sys.exit(main())
