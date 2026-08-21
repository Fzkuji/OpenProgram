from pathlib import Path

from openprogram.webui._functions import (
    _discover_workflow_functions,
    _extract_all_functions,
)


def test_workflow_category_exports_only_named_public_entries() -> None:
    source = (
        Path(__file__).parents[3]
        / "openprogram"
        / "programs"
        / "functions"
        / "agentic"
        / "workflow"
        / "authoring"
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


def test_programs_treats_workflow_as_category_not_program() -> None:
    from openprogram.webui.routes.programs import _list_entries, _program_logic

    agentic = _list_entries("functions/agentic")
    workflow = next(
        entry for entry in agentic["entries"]
        if entry["path"] == "functions/agentic/workflow"
    )
    assert workflow["program_kind"] is None
    assert workflow["has_children"] is True
    assert not any(
        entry["path"] == "functions/agentic/agentic_workflow"
        for entry in agentic["entries"]
    )

    children = {
        entry["path"]: entry
        for entry in _list_entries("functions/agentic/workflow")["entries"]
    }
    assert children["functions/agentic/workflow/authoring"]["has_children"] is True
    assert "functions/agentic/workflow/browser" in children
    assert "functions/agentic/workflow/docs_question" in children
    assert "functions/agentic/workflow/goal" in children
    assert "functions/agentic/workflow/security_review" in children

    entries = _list_entries("functions/agentic/workflow/authoring")["entries"]
    assert {
        (entry["name"], entry["callable_name"], entry["program_kind"])
        for entry in entries
    } == {
        ("search_workflows", "search_workflows", "agentic_function"),
        ("create_workflow", "create_workflow", "agentic_function"),
        ("revise_workflow", "revise_workflow", "agentic_function"),
        ("auto_workflow", "auto_workflow", "agentic_function"),
    }
    for entry in entries:
        assert entry["logic_path"] == entry["path"]
        logic = _program_logic(entry["logic_path"])
        assert logic["root"] == entry["path"]
        root = next(node for node in logic["nodes"] if node["id"] == logic["root"])
        assert root["name"] == entry["name"]

    search_logic = _program_logic(
        "functions/agentic/workflow/authoring/search_workflows"
    )
    assert search_logic["edges"] == []
    auto_logic = _program_logic(
        "functions/agentic/workflow/authoring/auto_workflow"
    )
    assert {
        edge["target"] for edge in auto_logic["edges"]
        if edge["source"] == auto_logic["root"]
    } == {
        "functions/agentic/workflow/authoring/search_workflows",
        "functions/agentic/workflow/authoring/create_workflow",
        "agentic_programming/agent",
    }

    old_source = (
        Path(__file__).parents[3]
        / "openprogram/programs/functions/agentic/agentic_workflow"
    )
    assert not old_source.exists()


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

    source = tmp_path / "functions/agentic/deep/category/tools.py"
    source.parent.mkdir(parents=True)
    source.write_text("# test source\n", encoding="utf-8")
    indexed = {
        "functions/agentic/deep/category/tools": [
            {"name": "alpha", "description": "", "source": source},
            {"name": "beta", "description": "", "source": source},
        ],
    }
    monkeypatch.setattr(programs, "PROGRAMS_ROOT", tmp_path)
    monkeypatch.setattr(
        programs, "_registered_agentic_callables", lambda: indexed,
    )

    category = programs._list_entries("functions/agentic/deep/category")
    group = next(entry for entry in category["entries"] if entry["name"] == "tools")
    assert group["program_kind"] is None
    assert group["has_children"] is True
    entries = programs._list_entries(
        "functions/agentic/deep/category/tools"
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

    assert "functions/agentic/workflow/authoring" in indexed


def test_multi_entry_call_graph_scopes_imports_to_selected_function(
    tmp_path: Path, monkeypatch,
) -> None:
    from openprogram.webui.routes import programs

    group = tmp_path / "group.py"
    group.write_text(
        "from openprogram.programs.functions.agentic.dep import zz_dep\n"
        "def zz_alpha():\n"
        "    return zz_dep()\n"
        "def zz_beta():\n"
        "    return None\n",
        encoding="utf-8",
    )
    dependency = tmp_path / "dep.py"
    dependency.write_text("def zz_dep():\n    return None\n", encoding="utf-8")
    indexed = {
        "functions/agentic/group": [
            {"name": "zz_alpha", "description": "", "source": group},
            {"name": "zz_beta", "description": "", "source": group},
        ],
        "functions/agentic/dep": [
            {"name": "zz_dep", "description": "", "source": dependency},
        ],
    }
    entities = {
        "functions/agentic/group/zz_alpha": group,
        "functions/agentic/group/zz_beta": group,
        "functions/agentic/dep": dependency,
    }
    monkeypatch.setattr(programs, "_inside_programs", lambda _path: True)
    monkeypatch.setattr(
        programs, "_registered_agentic_callables", lambda: indexed,
    )
    monkeypatch.setattr(programs, "_entity_paths", lambda: entities)

    alpha = programs._program_logic("functions/agentic/group/zz_alpha")
    beta = programs._program_logic("functions/agentic/group/zz_beta")

    assert {
        (edge["source"], edge["target"]) for edge in alpha["edges"]
    } == {
        (
            "functions/agentic/group/zz_alpha",
            "functions/agentic/dep",
        ),
    }
    assert beta["edges"] == []
