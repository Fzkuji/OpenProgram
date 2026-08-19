from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pytest

from tests.support.repository import tracked_python_files
from tests.support.unit_runtime import reject_unit_background_threads


TESTS = Path(__file__).resolve().parents[2]
ROOT = TESTS.parent
LAYERS = {"contracts", "unit", "component", "integration", "e2e", "live", "support"}
EXECUTION_LAYERS = {"contracts", "unit", "component", "integration", "e2e", "live"}


def test_tracked_tests_use_the_declared_top_level_categories() -> None:
    misplaced = []
    for path in tracked_python_files(TESTS):
        relative = path.relative_to(TESTS)
        if len(relative.parts) == 1:
            if relative.name not in {"__init__.py", "conftest.py"}:
                misplaced.append(relative.as_posix())
        elif relative.parts[0] not in LAYERS:
            misplaced.append(relative.as_posix())
        elif (
            relative.parts[0] in EXECUTION_LAYERS
            and relative.name.startswith("test_")
            and len(relative.parts) < 3
        ):
            misplaced.append(relative.as_posix())

    assert misplaced == []


def _unit_dependency_violations(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    violations: set[str] = set()

    def qualified_name(node: ast.AST | None) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            owner = qualified_name(node.value)
            return f"{owner}.{node.attr}" if owner else ""
        return ""

    def is_thread_target(target: ast.AST | None, attribute: ast.AST | None) -> bool:
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            return target.value == "threading.Thread" or target.value.endswith(
                ".threading.Thread"
            )
        target_name = qualified_name(target)
        return (
            (target_name == "threading" or target_name.endswith(".threading"))
            and isinstance(attribute, ast.Constant)
            and attribute.value == "Thread"
        )

    def module_sleep_captures() -> set[str]:
        module_aliases: dict[str, str] = {}
        captured: set[str] = set()

        def module_name(node: ast.AST | None) -> str:
            if isinstance(node, ast.Name):
                return module_aliases.get(node.id, "")
            if isinstance(node, ast.Attribute):
                owner = module_name(node.value)
                return f"{owner}.{node.attr}" if owner else ""
            return ""

        def inspect_value(node: ast.AST | None) -> None:
            if node is None:
                return
            name = module_name(node)
            if name in {"time.sleep", "asyncio.sleep"}:
                captured.add(name)
                return
            if isinstance(node, ast.Call):
                for argument in node.args:
                    inspect_value(argument)
                for keyword in node.keywords:
                    inspect_value(keyword.value)
                return
            if isinstance(node, ast.Lambda):
                for default in (*node.args.defaults, *node.args.kw_defaults):
                    inspect_value(default)
                return
            for child in ast.iter_child_nodes(node):
                inspect_value(child)

        def clear_target(target: ast.AST) -> None:
            if isinstance(target, ast.Name):
                module_aliases.pop(target.id, None)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    clear_target(item)

        def bind_target(target: ast.AST, module: str) -> None:
            if isinstance(target, ast.Name):
                if module in {"time", "asyncio"}:
                    module_aliases[target.id] = module
                else:
                    module_aliases.pop(target.id, None)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    clear_target(item)

        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    if alias.name in {"time", "asyncio"}:
                        module_aliases[bound] = alias.name
                    else:
                        module_aliases.pop(bound, None)
            elif isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    module_aliases.pop(alias.asname or alias.name, None)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for value in (
                    *statement.decorator_list,
                    *statement.args.defaults,
                    *statement.args.kw_defaults,
                ):
                    inspect_value(value)
                module_aliases.pop(statement.name, None)
            elif isinstance(statement, ast.ClassDef):
                for value in (*statement.decorator_list, *statement.bases):
                    inspect_value(value)
                module_aliases.pop(statement.name, None)
            elif isinstance(statement, ast.Assign):
                inspect_value(statement.value)
                module = module_name(statement.value)
                for target in statement.targets:
                    bind_target(target, module)
            elif isinstance(statement, ast.AnnAssign):
                inspect_value(statement.value)
                if statement.value is not None:
                    bind_target(statement.target, module_name(statement.value))
            elif isinstance(statement, ast.Expr):
                inspect_value(statement.value)
        return captured

    violations.update(module_sleep_captures())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                aliases[alias.asname or root] = alias.name if alias.asname else root
                if root in {"subprocess", "socket", "playwright"}:
                    violations.add(root)
        elif isinstance(node, ast.ImportFrom):
            root = node.module.split(".", 1)[0] if node.module else ""
            for alias in node.names:
                aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                    if node.level == 0 and node.module
                    else alias.name
                )
            if node.level == 0 and root in {"subprocess", "socket", "playwright"}:
                violations.add(root)
            if node.level == 0 and node.module in {
                "fastapi.testclient",
                "starlette.testclient",
            }:
                violations.add("TestClient")
            if node.level == 0 and node.module in {"time", "asyncio"} and any(
                alias.name == "sleep" for alias in node.names
            ):
                violations.add(f"{node.module}.sleep")
        elif isinstance(node, ast.Call):
            name = qualified_name(node.func)
            if name.endswith(".TestClient") or name == "TestClient":
                violations.add("TestClient")
            target = node.args[0] if node.args else None
            attribute = node.args[1] if len(node.args) > 1 else None
            if (
                name in {"setattr", "unittest.mock.patch.object"}
                or name.endswith(".setattr")
            ) and is_thread_target(target, attribute):
                violations.add("threading.Thread")
            if name == "unittest.mock.patch" and is_thread_target(target, None):
                violations.add("threading.Thread")
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(qualified_name(target) == "threading.Thread" for target in targets):
                violations.add("threading.Thread")
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if qualified_name(node.target) == "threading.Thread":
                violations.add("threading.Thread")
    return violations


def test_unit_tests_do_not_depend_on_component_resources() -> None:
    offenders = {
        path.relative_to(TESTS).as_posix(): sorted(violations)
        for path in tracked_python_files(TESTS / "unit")
        if (violations := _unit_dependency_violations(path))
    }

    assert offenders == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import subprocess as process", {"subprocess"}),
        ("from socket import socket as connect", {"socket"}),
        ("from .socket import fake_socket", set()),
        ("import time as clock\nclock.sleep(1)", set()),
        ("import asyncio as aio\naio.sleep(delay=0)", set()),
        ("import asyncio as aio\naio.sleep(0.1)", set()),
        ("from asyncio import sleep as pause", {"asyncio.sleep"}),
        (
            "from starlette.testclient import TestClient as Client\nClient(app)",
            {"TestClient"},
        ),
        (
            "import threading as t\n"
            'monkeypatch.setattr(t, "Thread", fake)',
            {"threading.Thread"},
        ),
        (
            "from openprogram.agent.job import runner as runner_mod\n"
            'monkeypatch.setattr(runner_mod.threading, "Thread", fake)',
            {"threading.Thread"},
        ),
        ('monkeypatch.setattr("threading.Thread", fake)', {"threading.Thread"}),
        ("import threading\nthreading.Thread = fake", {"threading.Thread"}),
        ("import threading\nthreading.Thread: object", set()),
        ("import time\npause = time.sleep", {"time.sleep"}),
        (
            "import asyncio\npause: object = asyncio.sleep",
            {"asyncio.sleep"},
        ),
        (
            "import time\ndef probe(pause=time.sleep):\n    pass",
            {"time.sleep"},
        ),
        (
            "import asyncio\nprobe = lambda pause=asyncio.sleep: pause(0)",
            {"asyncio.sleep"},
        ),
        (
            "import time as clock\nclock = fake_clock\npause = clock.sleep",
            set(),
        ),
        ("import time\ntime: object\npause = time.sleep", {"time.sleep"}),
        ("import time\nimport local as time\npause = time.sleep", set()),
        ("import time\nfrom local import time\npause = time.sleep", set()),
        (
            "import time\nclock = time\npause = clock.sleep",
            {"time.sleep"},
        ),
    ],
)
def test_unit_dependency_parser_enforces_structural_boundaries(
    tmp_path: Path, source: str, expected: set[str]
) -> None:
    path = tmp_path / "test_probe.py"
    path.write_text(source, encoding="utf-8")
    assert _unit_dependency_violations(path) == expected


def test_unit_runtime_guard_rejects_real_thread_and_pool() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        with reject_unit_background_threads(monkeypatch):
            with pytest.raises(pytest.fail.Exception, match="background thread"):
                threading.Thread().start()
            with pytest.raises(pytest.fail.Exception, match="thread pool"):
                ThreadPoolExecutor()
            with pytest.raises(pytest.fail.Exception, match="time.sleep"):
                time.sleep(0.01)
            with pytest.raises(pytest.fail.Exception, match="asyncio.sleep"):
                asyncio.sleep(0.01)
            zero_yield = asyncio.sleep(delay=0)
            zero_yield.close()
    finally:
        monkeypatch.undo()


def test_unit_runtime_guard_rejects_process_global_thread_replacement() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        with pytest.raises(pytest.fail.Exception, match="threading.Thread"):
            with reject_unit_background_threads(monkeypatch):
                threading.Thread = object
        assert threading.Thread is not object
    finally:
        monkeypatch.undo()


def _production_probe(source: str, name: str):
    namespace: dict[str, object] = {}
    path = ROOT / "openprogram" / "_test_runtime_probe.py"
    exec(compile(source, str(path), "exec"), namespace)
    return namespace[name]


def test_unit_runtime_guard_accepts_clean_production_resources() -> None:
    use_resources = _production_probe(
        "from concurrent.futures import ThreadPoolExecutor\n"
        "import threading\n"
        "def use_resources():\n"
        "    thread = threading.Thread(target=lambda: None)\n"
        "    thread.start()\n"
        "    thread.join()\n"
        "    with ThreadPoolExecutor(max_workers=1) as pool:\n"
        "        pool.submit(lambda: None).result()\n",
        "use_resources",
    )
    monkeypatch = pytest.MonkeyPatch()
    try:
        with reject_unit_background_threads(monkeypatch, direct_calls_only=True):
            use_resources()
    finally:
        monkeypatch.undo()


def test_unit_runtime_guard_rejects_leaked_production_thread() -> None:
    start_thread = _production_probe(
        "import threading\n"
        "def start_thread(event):\n"
        "    thread = threading.Thread(target=event.wait, daemon=True)\n"
        "    thread.start()\n"
        "    return thread\n",
        "start_thread",
    )
    event = threading.Event()
    monkeypatch = pytest.MonkeyPatch()
    thread = None
    try:
        with pytest.raises(pytest.fail.Exception, match="threads running"):
            with reject_unit_background_threads(monkeypatch, direct_calls_only=True):
                thread = start_thread(event)
    finally:
        event.set()
        if thread is not None:
            thread.join(timeout=1)
        monkeypatch.undo()


def test_unit_runtime_guard_rejects_leaked_production_pool() -> None:
    start_pool = _production_probe(
        "from concurrent.futures import ThreadPoolExecutor\n"
        "def start_pool(event):\n"
        "    pool = ThreadPoolExecutor(max_workers=1)\n"
        "    pool.submit(event.wait)\n"
        "    return pool\n",
        "start_pool",
    )
    event = threading.Event()
    monkeypatch = pytest.MonkeyPatch()
    pool = None
    try:
        with pytest.raises(pytest.fail.Exception, match="thread pool open"):
            with reject_unit_background_threads(monkeypatch, direct_calls_only=True):
                pool = start_pool(event)
    finally:
        event.set()
        if pool is not None:
            pool.shutdown(wait=True)
        monkeypatch.undo()
