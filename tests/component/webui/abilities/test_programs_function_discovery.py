from pathlib import Path

from openprogram.interfaces.server.function_catalog import (
    _discover_package_functions,
    _extract_all_functions,
    _extract_function_info,
)


def test_goal_launcher_keeps_original_prompt_schema_through_ownership_wrapper():
    import openprogram.programs.tools.planning.goal  # noqa: F401 - import side effect: registers the public entry

    row = next(
        row for row in _discover_package_functions(set()) if row["name"] == "goal"
    )
    assert row["category"] == "tool"
    assert "prompt" in row["params"]
    assert row["filepath"].endswith("/goal/goal.py")


def test_goal_source_endpoint_returns_goal_instead_of_ownership_wrapper(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.programs.tools.planning.goal.goal import goal
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.catalog import functions

    monkeypatch.setattr(server, "_load_function", lambda _name: goal)
    app = FastAPI()
    functions.register(app)
    response = TestClient(app).get("/api/function/goal/source")
    assert response.status_code == 200
    data = response.json()
    assert data["filepath"].endswith("/goal/goal.py")
    assert "def goal(" in data["source"]
    assert "def run(" not in data["source"]


def test_workflow_category_has_no_authoring_entries() -> None:
    workflow_dir = Path(__file__).parents[4] / "openprogram" / "programs" / "workflow"
    for filename in (
        "search_workflows.py",
        "create_workflow.py",
        "revise_workflow.py",
        "auto_workflow.py",
        "resume_workflow.py",
    ):
        assert not (workflow_dir / filename).exists()


def test_programs_lists_tools_and_applications_from_the_source_tree() -> None:
    from openprogram.interfaces.server.routes.catalog.programs import _list_entries, _program_logic

    root_entries = _list_entries("")
    assert [entry["path"] for entry in root_entries["entries"]] == ["tools", "applications"]
    assert all(entry["program_kind"] is None for entry in root_entries["entries"])

    children = {entry["path"]: entry for entry in _list_entries("applications")["entries"]}
    for removed in ("search_workflows", "create_workflow", "revise_workflow", "auto_workflow"):
        assert f"applications/{removed}" not in children
    for helper in ("authoring", "errors", "json_parsing", "ask_user", "resume_workflow"):
        assert f"applications/{helper}" not in children
    assert not any(path.endswith(".py") for path in children)
    assert not any(entry["name"].startswith("_") for entry in children.values())
    assert len(children) == len({entry["name"] for entry in children.values()})
    browser = children["applications/browser"]
    assert browser["origin"] == "builtin"
    assert browser["program_kind"] == "application"
    assert browser["enabled"] is True
    assert browser["has_children"] is True
    assert "callable_name" not in browser

    planning = {entry["path"]: entry for entry in _list_entries("tools/planning")["entries"]}
    goal = planning["tools/planning/goal"]
    assert goal["has_children"] is True
    assert goal["program_kind"] == "tool"
    assert goal["callable_name"] == "goal"
    assert goal["logic_path"] == "tools/planning/goal"

    goal_by_name = {entry["name"]: entry for entry in _list_entries("tools/planning/goal")["entries"]}
    assert goal_by_name["goal"]["callable_name"] == "goal"
    assert goal_by_name["goal"]["logic_path"] == "tools/planning/goal"
    assert goal_by_name["goal"]["program_kind"] == "tool"
    assert {
        "chat",
        "command",
        "notices",
        "state",
        "verification",
    } <= set(goal_by_name)
    command = goal_by_name["command"]
    assert "callable_name" not in command
    assert command["path"] == "tools/planning/goal/command"
    assert command["logic_path"] == "tools/planning/goal/command"
    assert command["program_kind"] is None
    command_logic = _program_logic("tools/planning/goal/command")
    assert command_logic["root"] == "tools/planning/goal/command"
    assert command_logic["nodes"][0]["program_kind"] is None
    # Package attribute calls (``_goal.positive_int``) must resolve to the
    # defining helper module, not to the goal() entry.
    command_targets = {
        edge["target"]
        for edge in command_logic["edges"]
        if edge["source"] == "tools/planning/goal/command"
    }
    assert "tools/planning/goal/state" in command_targets
    assert "tools/planning/goal" not in command_targets
    goal_logic = _program_logic("tools/planning/goal")
    goal_ids = {node["id"] for node in goal_logic["nodes"]}
    goal_edges = {(edge["source"], edge["target"]) for edge in goal_logic["edges"]}
    assert ("tools/planning/goal", "tools/planning/goal/chat") in goal_edges
    assert "tools/planning/goal/command" not in goal_ids
    browser_entries = [
        (entry["callable_name"], entry["logic_path"])
        for entry in _list_entries("applications/browser")["entries"]
        if entry.get("callable_name")
    ]
    assert browser_entries == []


def test_goal_form_takes_only_the_objective() -> None:
    source = Path(__file__).parents[4] / "openprogram/programs/tools/planning/goal/goal.py"
    goal = next(
        info
        for info in _extract_all_functions(str(source), "tool")
        if info["name"] == "goal"
    )
    visible = [p["name"] for p in goal["params_detail"] if not p.get("hidden")]
    assert visible == ["prompt"]
    assert not any(p.get("advanced") for p in goal["params_detail"])


def test_function_info_preserves_advanced_input_metadata(tmp_path: Path) -> None:
    source = tmp_path / "advanced_function.py"
    source.write_text(
        'from openprogram import Agent\nclass AdvancedFunctionAgent(Agent):\n    method_options = {\n        \'advanced_function\': {\n            \'input\': {\n    "max_steps": {"hidden": True, "advanced": True},\n},\n            \'name\': \'advanced_function\',\n            \'tool\': True\n        },\n    }\n\n    def advanced_function(self, task: str, max_steps: int = 3) -> str:\n        """Run one task."""\n        return task\n\n\nadvanced_function = AdvancedFunctionAgent().advanced_function\n',
        encoding="utf-8",
    )

    info = _extract_function_info(str(source), "advanced_function", "application")

    assert info is not None
    max_steps = next(
        param for param in info["params_detail"] if param["name"] == "max_steps"
    )
    assert max_steps["hidden"] is True
    assert max_steps["advanced"] is True


def test_function_info_is_reused_until_its_source_changes(tmp_path: Path, monkeypatch) -> None:
    import os
    from openprogram.interfaces.server import function_catalog

    source = tmp_path / "cached_function.py"
    source.write_text(
        "def cached_function(task: str) -> str:\n    \"\"\"First.\"\"\"\n    return task\n\n"
        "PROGRAM_ENTRIES = [cached_function]\n",
        encoding="utf-8",
    )
    parses = []
    read = function_catalog._read_function_info
    monkeypatch.setattr(function_catalog, "_read_function_info", lambda *args: parses.append(args) or read(*args))

    first = _extract_all_functions(str(source), "application")
    first[0]["description"] = "changed by a caller"
    second = _extract_all_functions(str(source), "application")
    assert len(parses) == 1
    assert second[0]["description"] == "First."

    source.write_text(source.read_text(encoding="utf-8").replace("First.", "Second."), encoding="utf-8")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert _extract_all_functions(str(source), "application")[0]["description"] == "Second."
    assert len(parses) == 2


def test_registered_user_package_is_available_to_favorites(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from openprogram.programs._host.runtime import _registry

    source = tmp_path / "favorite_workflow.py"
    source.write_text(
        "def favorite_workflow(task: str) -> str:\n"
        '    """Prepare a report."""\n'
        "    return task\n",
        encoding="utf-8",
    )
    # A package in ~/.openprogram/programs imports under its own name
    # (favorite_workflow.workflow); the loader tags the tool with its owner.
    namespace = {"__name__": "favorite_workflow.workflow"}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    monkeypatch.setitem(
        _registry,
        "favorite_workflow",
        SimpleNamespace(
            _is_agent_method=True,
            _python_callable=namespace["favorite_workflow"],
            _workflow_owner="favorite_workflow",
        ),
    )

    rows = _discover_package_functions(set())

    row = next(program for program in rows if program["name"] == "favorite_workflow")
    assert row["category"] == "application"
    assert row["params"] == ["task"]


def test_nested_multi_entry_agentic_file_expands_as_virtual_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    source = tmp_path / "tools/deep/category/tools.py"
    source.parent.mkdir(parents=True)
    source.write_text("# test source\n", encoding="utf-8")
    indexed = {
        "tools/deep/category/tools": [
            {"name": "alpha", "description": "", "source": source},
            {"name": "beta", "description": "", "source": source},
        ],
    }
    monkeypatch.setattr(programs_routes, "PROGRAMS_ROOT", tmp_path)
    monkeypatch.setattr(
        programs_routes,
        "_registered_agent_methods",
        lambda: indexed,
    )

    category = programs_routes._list_entries("tools/deep/category")
    group = next(entry for entry in category["entries"] if entry["name"] == "tools")
    assert group["program_kind"] is None
    assert group["has_children"] is True
    entries = programs_routes._list_entries("tools/deep/category/tools")["entries"]
    assert [entry["name"] for entry in entries] == ["alpha", "beta"]


def test_agentic_registry_discovery_uses_a_stable_snapshot(monkeypatch) -> None:
    import importlib

    from openprogram.interfaces.server.routes.catalog.programs import _registered_agent_methods

    function = importlib.import_module("openprogram.programs._host.runtime")
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

    indexed = _registered_agent_methods()

    assert "tools/planning/goal" in indexed


def test_multi_entry_call_graph_scopes_imports_to_selected_function(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    group = tmp_path / "group.py"
    group.write_text(
        "from openprogram.programs.tools.planning.dep import zz_dep\n"
        "def zz_alpha():\n"
        "    return zz_dep()\n"
        "def zz_beta():\n"
        "    return None\n",
        encoding="utf-8",
    )
    dependency = tmp_path / "dep.py"
    dependency.write_text("def zz_dep():\n    return None\n", encoding="utf-8")
    indexed = {
        "tools/planning/group": [
            {"name": "zz_alpha", "description": "", "source": group},
            {"name": "zz_beta", "description": "", "source": group},
        ],
        "tools/planning/dep": [
            {"name": "zz_dep", "description": "", "source": dependency},
        ],
    }
    entities = {
        "tools/planning/group/zz_alpha": group,
        "tools/planning/group/zz_beta": group,
        "tools/planning/dep": dependency,
    }
    monkeypatch.setattr(programs_routes, "_inside_programs", lambda _path: True)
    monkeypatch.setattr(
        programs_routes,
        "_registered_agent_methods",
        lambda: indexed,
    )
    monkeypatch.setattr(programs_routes, "_entity_paths", lambda: entities)

    alpha = programs_routes._program_logic("tools/planning/group/zz_alpha")
    beta = programs_routes._program_logic("tools/planning/group/zz_beta")

    assert {(edge["source"], edge["target"]) for edge in alpha["edges"]} == {
        (
            "tools/planning/group/zz_alpha",
            "tools/planning/dep",
        ),
    }
    assert beta["edges"] == []
