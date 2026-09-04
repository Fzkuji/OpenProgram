"""Public layout contract for shipped Programs source code."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import openprogram.programs as programs
from openprogram.programs import _registry


def test_programs_use_the_three_category_source_layout() -> None:
    root = Path(programs.__file__).resolve().parent

    assert (root / "tools" / "__init__.py").is_file()
    assert (root / "workflow" / "__init__.py").is_file()
    assert (root / "applications" / "__init__.py").is_file()
    # Old harness imports retain only the shared parser compatibility surface.
    legacy = root / "agentic_functions"
    assert sorted(path.relative_to(legacy).as_posix() for path in legacy.rglob("*.py")) == [
        "__init__.py", "_utils.py", "json_parsing.py",
    ]


def test_function_namespaces_and_legacy_parser_exports() -> None:
    assert importlib.util.find_spec(
        "openprogram.programs.tools"
    ) is not None
    assert importlib.util.find_spec(
        "openprogram.programs.workflow"
    ) is not None
    from openprogram.programs.workflow.json_parsing import parse_json

    for suffix in ("", "._utils", ".json_parsing"):
        legacy = importlib.import_module("openprogram.programs.agentic_functions" + suffix)
        assert legacy.__all__ == ["parse_json"]
        assert legacy.parse_json is parse_json


def test_agentic_rescan_uses_the_new_source_directory() -> None:
    root = Path(programs.__file__).resolve().parent

    assert Path(_registry._default_agentic_functions_dir()).resolve() == (
        root / "workflow"
    )
