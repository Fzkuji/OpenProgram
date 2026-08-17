"""Public layout contract for shipped Programs source code."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import openprogram.programs as programs
from openprogram.programs import _registry


def test_programs_use_the_four_layer_source_layout() -> None:
    root = Path(programs.__file__).resolve().parent

    assert (root / "functions" / "vanilla" / "__init__.py").is_file()
    assert (root / "functions" / "agentic" / "__init__.py").is_file()
    assert (root / "workflows" / "__init__.py").is_file()
    assert (root / "applications" / "__init__.py").is_file()
    assert not (root / "agentic_functions").exists()


def test_new_function_namespaces_are_importable_without_old_alias() -> None:
    assert importlib.util.find_spec(
        "openprogram.programs.functions.vanilla"
    ) is not None
    assert importlib.util.find_spec(
        "openprogram.programs.functions.agentic"
    ) is not None
    assert importlib.util.find_spec(
        "openprogram.programs.agentic_functions"
    ) is None


def test_agentic_rescan_uses_the_new_source_directory() -> None:
    root = Path(programs.__file__).resolve().parent

    assert Path(_registry._default_agentic_functions_dir()).resolve() == (
        root / "functions" / "agentic"
    )
