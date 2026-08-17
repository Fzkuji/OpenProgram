from pathlib import Path

from openprogram.webui._functions import _extract_all_functions


def test_agentic_workflow_is_available_to_the_chat_program_catalog() -> None:
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
    workflow = next(program for program in programs if program["name"] == "agentic_workflow")

    assert workflow["params"] == ["task"]
    assert workflow["params_detail"] == [
        {
            "name": "task",
            "type": "str",
            "default": None,
            "required": True,
            "description": "The task to plan and execute",
            "multiline": True,
        }
    ]
