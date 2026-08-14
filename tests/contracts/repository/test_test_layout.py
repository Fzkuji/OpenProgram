from __future__ import annotations

import ast
from pathlib import Path

from tests.support.repository import tracked_python_files


TESTS = Path(__file__).resolve().parents[2]
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
                if alias.name in {"subprocess", "socket", "playwright"}:
                    violations.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            if node.module.startswith(("subprocess", "socket", "playwright")):
                violations.add(node.module.split(".", 1)[0])
            if node.module in {"fastapi.testclient", "starlette.testclient"}:
                violations.add("TestClient")
        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = aliases.get(node.func.id, node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
                owner = aliases.get(node.func.value.id, node.func.value.id)
                name = f"{owner}.{node.func.attr}"
            if name in {"time.sleep", "threading.Thread"}:
                violations.add(name)
            if name.endswith(".TestClient") or name == "TestClient":
                violations.add("TestClient")
    return violations


def test_unit_tests_do_not_depend_on_component_resources() -> None:
    offenders = {
        path.relative_to(TESTS).as_posix(): sorted(violations)
        for path in tracked_python_files(TESTS / "unit")
        if (violations := _unit_dependency_violations(path))
    }

    assert offenders == {}
