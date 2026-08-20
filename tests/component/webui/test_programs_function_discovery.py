from pathlib import Path

from openprogram.webui._functions import (
    _discover_workflow_functions,
    _extract_all_functions,
)


def test_agentic_workflow_is_not_in_the_chat_program_catalog() -> None:
    source = (
        Path(__file__).parents[3]
        / "openprogram"
        / "programs"
        / "functions"
        / "agentic"
        / "agentic_workflow"
        / "__init__.py"
    )

    programs = _extract_all_functions(str(source), "agentic")
    names = {program["name"] for program in programs}

    assert "agentic_workflow" not in names
    assert {
        "search_workflows",
        "create_workflow",
        "revise_workflow",
        "auto_workflow",
    } <= names


def test_registered_workflow_is_available_to_favorites(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from openprogram.agentic_programming.function import _registry

    source = tmp_path / "favorite_workflow.py"
    source.write_text(
        'def favorite_workflow(task: str) -> str:\n'
        '    """Prepare a report."""\n'
        '    return task\n',
        encoding="utf-8",
    )
    namespace = {"__name__": "workflows.favorite_workflow"}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    monkeypatch.setitem(
        _registry,
        "favorite_workflow",
        SimpleNamespace(_fn=namespace["favorite_workflow"]),
    )

    workflows = _discover_workflow_functions(set())

    workflow = next(
        program for program in workflows if program["name"] == "favorite_workflow"
    )
    assert workflow["category"] == "workflow"
    assert workflow["params"] == ["task"]
