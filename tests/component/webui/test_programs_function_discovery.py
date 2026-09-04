from pathlib import Path

from openprogram.webui._functions import (
    _discover_workflow_functions,
    _extract_all_functions,
    _extract_function_info,
)


def test_workflow_category_exports_only_named_public_entries() -> None:
    workflow_dir = (
        Path(__file__).parents[3]
        / "openprogram"
        / "programs"
        / "workflow"
    )
    names = set()
    for filename in (
        "search_workflows.py",
        "create_workflow.py",
        "revise_workflow.py",
    ):
        programs = _extract_all_functions(str(workflow_dir / filename), "agentic")
        names.update(program["name"] for program in programs)
    auto_source = Path(__file__).parents[3] / "openprogram/programs/workflow/auto_workflow.py"
    names.update(
        program["name"]
        for program in _extract_all_functions(str(auto_source), "workflow")
    )

    assert "agentic_workflow" not in names
    assert {
        "search_workflows",
        "create_workflow",
        "revise_workflow",
        "auto_workflow",
    } <= names


def test_programs_treats_workflow_as_category_not_program() -> None:
    from openprogram.webui.routes.programs import _list_entries, _program_logic

    root_entries = _list_entries("")
    workflow = next(
        entry for entry in root_entries["entries"]
        if entry["path"] == "workflow"
    )
    assert workflow["program_kind"] is None
    assert workflow["has_children"] is True
    assert not any(
        entry["path"] == "workflow/agentic_workflow"
        for entry in root_entries["entries"]
    )

    children = {
        entry["path"]: entry
        for entry in _list_entries("workflow")["entries"]
    }
    assert children["workflow/search_workflows"]["has_children"] is False
    assert children["workflow/create_workflow"]["callable_name"] == "create_workflow"
    assert children["workflow/revise_workflow"]["program_kind"] == "workflow"
    assert "workflow/auto_workflow" in children
    assert "workflow/authoring" not in children
    assert "workflow/errors" not in children
    assert "workflow/json_parsing" not in children
    assert "workflow/ask_user" not in children
    assert "workflow/resume_workflow" not in children
    assert not any(path.endswith(".py") for path in children)
    assert not any(entry["name"].startswith("_") for entry in children.values())
    assert len(children) == len({entry["name"] for entry in children.values()})
    assert "workflow/browser" in children
    assert children["workflow/browser"]["has_children"] is True
    assert children["workflow/docs_question"]["has_children"] is True
    assert children["workflow/docs_question"]["program_kind"] is None
    assert children["workflow/docs_question"]["name"] == "docs_question"
    assert children["workflow/goal"]["has_children"] is True
    assert children["workflow/goal"]["program_kind"] is None
    assert children["workflow/goal"]["name"] == "goal"
    assert children["workflow/goal"]["callable_name"] == "goal"
    assert children["workflow/goal"]["logic_path"] == "workflow/goal"
    assert children["workflow/security_review"]["has_children"] is True
    assert children["workflow/security_review"]["name"] == "security_review"

    goal_entry = _list_entries("workflow/goal")["entries"]
    goal_by_name = {entry["name"]: entry for entry in goal_entry}
    assert goal_by_name["goal"]["callable_name"] == "goal"
    assert goal_by_name["goal"]["logic_path"] == "workflow/goal"
    assert goal_by_name["goal"]["program_kind"] == "workflow"
    assert {
        "command", "judge", "loop", "notices", "refinement", "state",
    } <= set(goal_by_name)
    command = goal_by_name["command"]
    assert "callable_name" not in command
    assert command["path"] == "workflow/goal/command"
    assert command["logic_path"] == "workflow/goal/command"
    assert command["program_kind"] is None
    command_logic = _program_logic("workflow/goal/command")
    assert command_logic["root"] == "workflow/goal/command"
    assert command_logic["nodes"][0]["program_kind"] is None
    notices_logic = _program_logic("workflow/goal/notices")
    notice_ids = {node["id"] for node in notices_logic["nodes"]}
    assert "agentic_programming/agent" not in notice_ids
    assert "agentic_programming/llm" not in notice_ids
    assert "workflow/goal" not in notice_ids
    assert "workflow/goal/state" in notice_ids
    goal_logic = _program_logic("workflow/goal")
    goal_ids = {node["id"] for node in goal_logic["nodes"]}
    goal_edges = {(edge["source"], edge["target"]) for edge in goal_logic["edges"]}
    assert "agentic_programming/agent" in goal_ids
    assert "workflow/goal/judge" in goal_ids
    assert "workflow/goal/refinement" in goal_ids
    assert "workflow/goal/loop" in goal_ids
    assert "workflow/goal/notices" in goal_ids
    assert "workflow/goal/command" not in goal_ids
    assert ("workflow/goal/loop", "workflow/goal/judge") not in goal_edges
    assert ("workflow/goal", "workflow/goal/judge") in goal_edges
    docs_entry = _list_entries("workflow/docs_question")["entries"]
    assert [
        (entry["callable_name"], entry["logic_path"])
        for entry in docs_entry
    ] == [("run_docs_question", "workflow/docs_question")]

    management_entries = [
        children[path]
        for path in (
            "workflow/search_workflows",
            "workflow/create_workflow",
            "workflow/revise_workflow",
        )
    ]
    workflow_entries = {
        entry["path"]: entry for entry in _list_entries("workflow")["entries"]
    }
    auto_entry = workflow_entries["workflow/auto_workflow"]
    entries = [*management_entries, auto_entry]
    assert {
        (entry["name"], entry["callable_name"], entry["program_kind"])
        for entry in entries
    } == {
        ("search_workflows", "search_workflows", "workflow"),
        ("create_workflow", "create_workflow", "workflow"),
        ("revise_workflow", "revise_workflow", "workflow"),
        ("auto_workflow", "auto_workflow", "workflow"),
    }
    for entry in entries:
        assert entry["logic_path"] == entry["path"]
        logic = _program_logic(entry["logic_path"])
        assert logic["root"] == entry["path"]
        root = next(node for node in logic["nodes"] if node["id"] == logic["root"])
        assert root["name"] == entry["name"]

    search_logic = _program_logic(
        "workflow/search_workflows"
    )
    assert search_logic["edges"] == []
    auto_logic = _program_logic("workflow/auto_workflow")
    assert {
        edge["target"] for edge in auto_logic["edges"]
        if edge["source"] == auto_logic["root"]
    } >= {
        "workflow/search_workflows",
        "workflow/create_workflow",
    }

    old_source = (
        Path(__file__).parents[3]
        / "openprogram/programs/workflow/agentic_workflow"
    )
    assert not old_source.exists()


def test_goal_form_exposes_only_prompt_and_condition() -> None:
    source = (
        Path(__file__).parents[3]
        / "openprogram/programs/workflow/goal/goal.py"
    )
    goal = next(
        info for info in _extract_all_functions(str(source), "workflow")
        if info["name"] == "goal"
    )
    visible = [p["name"] for p in goal["params_detail"] if not p.get("hidden")]
    assert visible == ["prompt"]


def test_gui_agent_form_exposes_primary_and_advanced_parameters() -> None:
    source = (
        Path(__file__).parents[3]
        / "openprogram/programs/gui_harness_bridge.py"
    )
    gui = next(
        info for info in _extract_all_functions(str(source), "app")
        if info["name"] == "gui_agent"
    )

    primary = [
        param["name"] for param in gui["params_detail"]
        if not param.get("hidden")
    ]
    advanced = {
        param["name"] for param in gui["params_detail"]
        if param.get("advanced")
    }
    user_params = {
        param["name"] for param in gui["params_detail"]
        if not param.get("hidden") or param.get("advanced")
    }

    assert primary == ["task"]
    assert advanced == {
        "max_steps", "max_seconds", "app_name", "surface", "backend", "vm_url",
    }
    assert user_params == {*primary, *advanced}


def test_function_info_preserves_advanced_input_metadata(tmp_path: Path) -> None:
    source = tmp_path / "advanced_function.py"
    source.write_text(
        '@agentic_function(input={\n'
        '    "max_steps": {"hidden": True, "advanced": True},\n'
        '})\n'
        'def advanced_function(task: str, max_steps: int = 3) -> str:\n'
        '    """Run one task."""\n'
        '    return task\n',
        encoding="utf-8",
    )

    info = _extract_function_info(str(source), "advanced_function", "app")

    assert info is not None
    max_steps = next(
        param for param in info["params_detail"] if param["name"] == "max_steps"
    )
    assert max_steps["hidden"] is True
    assert max_steps["advanced"] is True


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


def test_nested_multi_entry_agentic_file_expands_as_virtual_group(
    tmp_path: Path, monkeypatch,
) -> None:
    from openprogram.webui.routes import programs

    source = tmp_path / "workflow/deep/category/tools.py"
    source.parent.mkdir(parents=True)
    source.write_text("# test source\n", encoding="utf-8")
    indexed = {
        "workflow/deep/category/tools": [
            {"name": "alpha", "description": "", "source": source},
            {"name": "beta", "description": "", "source": source},
        ],
    }
    monkeypatch.setattr(programs, "PROGRAMS_ROOT", tmp_path)
    monkeypatch.setattr(
        programs, "_registered_agentic_callables", lambda: indexed,
    )

    category = programs._list_entries("workflow/deep/category")
    group = next(entry for entry in category["entries"] if entry["name"] == "tools")
    assert group["program_kind"] is None
    assert group["has_children"] is True
    entries = programs._list_entries(
        "workflow/deep/category/tools"
    )["entries"]
    assert [entry["name"] for entry in entries] == ["alpha", "beta"]


def test_agentic_registry_discovery_uses_a_stable_snapshot(monkeypatch) -> None:
    import importlib

    from openprogram.webui.routes.programs import _registered_agentic_callables

    function = importlib.import_module("openprogram.agentic_programming.function")
    original = function._registry

    class MutatingRegistry(dict):
        def items(self):
            iterator = super().items()

            def mutate_during_iteration():
                first = next(iterator)
                yield first
                self["late_registration"] = first[1]
                yield from iterator

            return mutate_during_iteration()

    monkeypatch.setattr(function, "_registry", MutatingRegistry(original))

    indexed = _registered_agentic_callables()

    assert "workflow/search_workflows" in indexed
    assert "workflow/auto_workflow" in indexed


def test_multi_entry_call_graph_scopes_imports_to_selected_function(
    tmp_path: Path, monkeypatch,
) -> None:
    from openprogram.webui.routes import programs

    group = tmp_path / "group.py"
    group.write_text(
        "from openprogram.programs.workflow.dep import zz_dep\n"
        "def zz_alpha():\n"
        "    return zz_dep()\n"
        "def zz_beta():\n"
        "    return None\n",
        encoding="utf-8",
    )
    dependency = tmp_path / "dep.py"
    dependency.write_text("def zz_dep():\n    return None\n", encoding="utf-8")
    indexed = {
        "workflow/group": [
            {"name": "zz_alpha", "description": "", "source": group},
            {"name": "zz_beta", "description": "", "source": group},
        ],
        "workflow/dep": [
            {"name": "zz_dep", "description": "", "source": dependency},
        ],
    }
    entities = {
        "workflow/group/zz_alpha": group,
        "workflow/group/zz_beta": group,
        "workflow/dep": dependency,
    }
    monkeypatch.setattr(programs, "_inside_programs", lambda _path: True)
    monkeypatch.setattr(
        programs, "_registered_agentic_callables", lambda: indexed,
    )
    monkeypatch.setattr(programs, "_entity_paths", lambda: entities)

    alpha = programs._program_logic("workflow/group/zz_alpha")
    beta = programs._program_logic("workflow/group/zz_beta")

    assert {
        (edge["source"], edge["target"]) for edge in alpha["edges"]
    } == {
        (
            "workflow/group/zz_alpha",
            "workflow/dep",
        ),
    }
    assert beta["edges"] == []
