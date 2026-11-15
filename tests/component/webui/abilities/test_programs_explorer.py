from __future__ import annotations

import json
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _isolate(
    monkeypatch,
    root: Path,
    *,
    user_root: Path,
    harnesses: list[Path] = (),
    catalog_rows: list[dict] = (),
    disabled: set[str] = frozenset(),
) -> None:
    """Point the explorer at ``root`` (``openprogram/programs``) and ``user_root``."""
    from openprogram.programs._host import registry
    from openprogram.programs._host import sources as program_sources
    from openprogram.programs._host.ui import catalog
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    monkeypatch.setattr(programs_routes, "PROGRAMS_ROOT", root)
    monkeypatch.setattr(registry, "_builtin_applications_root", lambda: str(root / "applications"))
    monkeypatch.setattr(registry, "builtin_application_enabled", lambda name: name not in disabled)
    monkeypatch.setattr("openprogram.state.program_directory.programs_root", lambda state_dir=None: user_root)
    monkeypatch.setattr(
        program_sources,
        "owner_controlled_program_sources",
        lambda base=None: [{"path": str(path)} for path in harnesses],
    )
    monkeypatch.setattr(catalog, "records", lambda: list(catalog_rows))


def _client(tmp_path: Path, monkeypatch, **options) -> TestClient:
    root = tmp_path / "openprogram" / "programs"
    _write(root / "tools" / "web" / "__init__.py", "")
    _write(root / "tools" / "web" / "web_fetch.py", "")
    _write(root / "tools" / "web" / "browser" / "__init__.py", "")
    _write(root / "applications" / "__init__.py", "")
    _write(root / "applications" / "alpha" / "__init__.py", "")
    for name in ("sample_pipeline", "literature_review", "paper_search"):
        _write(root / "applications" / name / "__init__.py", "")
    _write(
        root / "applications" / "sample_pipeline" / "workflow.py",
        "from openprogram.programs.applications.literature_review import literature_review\n"
        "\n"
        "def sample_pipeline(task: str):\n"
        "    return literature_review(task)\n",
    )
    _write(
        root / "applications" / "literature_review" / "workflow.py",
        "from openprogram.programs.applications.paper_search import run\n"
        "\n"
        "def literature_review(task: str):\n"
        "    return run()\n",
    )
    _write(
        root / "applications" / "paper_search" / "workflow.py",
        "def paper_search():\n    return run()\n\ndef run():\n    pass\n",
    )
    # Neither ``__init__.py`` nor ``application.json``: not an application.
    _write(root / "applications" / "research_app" / "pyproject.toml", "")
    _write(root / ".hidden", "ignored")
    _write(root / "__pycache__" / "ignored.py", "")

    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    options.setdefault("user_root", tmp_path / "state" / "programs")
    _isolate(monkeypatch, root, **options)
    app = FastAPI()
    programs_routes.register(app)
    return TestClient(app)


def _listing(client: TestClient, path: str) -> list[dict]:
    response = client.get("/api/programs/explorer", params={"path": path})
    assert response.status_code == 200, response.json()
    return response.json()["entries"]


def test_programs_explorer_lists_two_roots_lazily(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, disabled={"paper_search"})

    root = client.get("/api/programs/explorer").json()
    assert [entry["path"] for entry in root["entries"]] == ["tools", "applications"]
    assert root["default_selection"] == "applications/alpha"

    assert [
        (entry["name"], entry["path"], entry["program_kind"], entry["has_children"])
        for entry in _listing(client, "tools")
    ] == [("web", "tools/web", None, True)]
    assert [
        (
            entry["name"],
            entry["path"],
            entry["program_kind"],
            entry["has_children"],
            entry["logic_path"],
        )
        for entry in _listing(client, "tools/web")
    ] == [("web_fetch", "tools/web/web_fetch", "tool", False, "tools/web/web_fetch")]

    applications = {entry["name"]: entry for entry in _listing(client, "applications")}
    assert list(applications) == [
        "alpha",
        "literature_review",
        "paper_search",
        "sample_pipeline",
    ]
    assert {entry["path"] for entry in applications.values()} == {
        f"applications/{name}" for name in applications
    }
    assert {entry["program_kind"] for entry in applications.values()} == {"application"}
    assert {entry["origin"] for entry in applications.values()} == {"builtin"}
    assert applications["paper_search"]["enabled"] is False
    assert applications["alpha"]["enabled"] is True
    assert applications["alpha"]["has_children"] is False
    # An application expands to its source files.
    assert applications["sample_pipeline"]["has_children"] is True
    assert [
        (entry["name"], entry["path"], entry["program_kind"])
        for entry in _listing(client, "applications/sample_pipeline")
    ] == [("workflow", "applications/sample_pipeline/workflow", None)]
    assert (
        client.get("/api/programs/explorer", params={"path": "applications/alpha"}).status_code
        == 404
    )


def test_installed_programs_are_applications(tmp_path: Path, monkeypatch) -> None:
    """A user package, a catalogued application and a recorded harness."""
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    user_root = tmp_path / "state" / "programs"
    _write(user_root / "weekly_report" / "__init__.py", "")
    _write(user_root / "weekly_report" / "steps.py", "def gather():\n    return []\n")
    calculator = tmp_path / "examples" / "calculator"
    _write(calculator / "application.json", '{"id": "example.calculator", "title": "Calculator", "version": "2.0.0", "icon": "calculator", "description": "Adds numbers."}')
    _write(calculator / "backend.py", "def add(a, b):\n    return a + b\n")
    inner = user_root / "weekly_report" / "app"
    _write(inner / "application.json", '{"id": "local.weekly-report", "title": "Weekly report", "icon": "calendar", "description": "Drafts weekly reports."}')
    harness = user_root / "gui_harness"
    _write(harness / "pyproject.toml", "")
    _write(harness / "gui_harness" / "__init__.py", "")
    demo = user_root / "demo_harness"
    _write(demo / "pyproject.toml", "")
    _write(demo / "demo_harness" / "__init__.py", "")
    _write(demo / "demo_harness" / "agentics" / "__init__.py", "")
    from openprogram.programs._host import registry
    program_tools = registry.program_tools
    monkeypatch.setattr(
        registry, "program_tools",
        lambda owner: ["demo_agent"] if owner == "external:demo_harness" else program_tools(owner),
    )
    rows = [
        {"kind": "application", "source": str(calculator),
         "application": {"id": "example.calculator", "title": "Calculator", "enabled": False}},
        {"kind": "application", "source": str(inner),
         "application": {"id": "local.weekly-report", "title": "Weekly report"}},
        {"kind": "application", "source": str(calculator), "uninstalled": True,
         "application": {"id": "example.removed"}},
    ]
    monkeypatch.setattr(
        "openprogram.programs._host.packages.package_statuses",
        lambda: [{"name": "weekly_report", "path": str(user_root / "weekly_report"),
                  "state": "pending", "error": "", "note": "not checked",
                  "entries": [], "revision": ""}],
    )
    monkeypatch.setattr(
        "openprogram.programs._host.ui.catalog.source_root",
        lambda row: Path(row["source"]),
    )
    client = _client(
        tmp_path, monkeypatch, user_root=user_root, harnesses=[harness, demo],
        catalog_rows=rows,
    )

    # A plain directory listing: every row is named by its directory and
    # the rows are alphabetical, built in and installed alike.
    applications = {entry["name"]: entry for entry in _listing(client, "applications")}
    assert [(entry["name"], entry["origin"]) for entry in applications.values()] == [
        ("alpha", "builtin"),
        ("calculator", "installed"),
        ("demo_harness", "installed"),
        ("gui_harness", "installed"),
        ("literature_review", "builtin"),
        ("paper_search", "builtin"),
        ("sample_pipeline", "builtin"),
        ("weekly_report", "installed"),
    ]
    for entry in applications.values():
        assert entry["kind"] == "folder"
        assert not {"label", "part_of"} & set(entry)
    assert client.get("/api/programs/explorer").json()["default_selection"] == (
        "applications/alpha"
    )
    package = applications["weekly_report"]
    assert package["origin"] == "installed"
    assert package["program_kind"] == "application"
    assert package["load_state"] == "pending"
    assert package["load_note"] == "not checked"
    assert package["callable_name"] == "weekly_report"
    assert package["source_path"] == str((user_root / "weekly_report").resolve())
    assert "enabled" not in package
    # The package shows, and opens, the application in its ``app`` folder.
    assert (package["title"], package["description"], package["icon"], package["application_id"]) == (
        "Weekly report", "Drafts weekly reports.", "calendar", "local.weekly-report",
    )
    # A catalogued application inside the package is its real ``app``
    # subfolder, not a row of its own.
    assert [
        (entry["path"], entry["kind"])
        for entry in _listing(client, "applications/weekly_report")
    ] == [
        ("applications/weekly_report/app", "folder"),
        ("applications/weekly_report/steps", "file"),
    ]

    calculator_entry = applications["calculator"]
    assert calculator_entry["origin"] == "installed"
    assert calculator_entry["application_id"] == "example.calculator"
    assert calculator_entry["title"] == "Calculator"
    assert (calculator_entry["description"], calculator_entry["icon"], calculator_entry["version"]) == (
        "Adds numbers.", "calculator", "2.0.0",
    )
    assert calculator_entry["agent_entries"] == []
    harness_title = {entry["name"]: entry for entry in _listing(client, "applications")}["gui_harness"]["title"]
    assert harness_title == "gui_harness"
    assert calculator_entry["enabled"] is False
    assert "callable_name" not in calculator_entry
    assert calculator_entry["note"] == {"kind": "ui_only"}
    # A package that has not loaded yet explains itself through its load state.
    assert "note" not in package
    assert [entry["path"] for entry in _listing(client, "applications/calculator")] == [
        "applications/calculator/backend",
    ]
    logic = client.get(
        "/api/programs/logic", params={"path": "applications/calculator"},
    ).json()
    assert logic["nodes"][0]["name"] == "calculator"
    assert logic["nodes"][0]["program_kind"] == "application"

    assert "local.weekly-report" not in applications

    harness_entry = applications["gui_harness"]
    assert harness_entry["origin"] == "installed"
    assert harness_entry["has_children"] is False
    assert harness_entry["callable_name"] == "gui_harness"
    assert programs_routes._source_for("applications/gui_harness") == harness.resolve()
    # A harness that registered an Agent entry lists it as a child.
    demo_entry = applications["demo_harness"]
    assert demo_entry["callable_name"] == "demo_agent"
    assert demo_entry["has_children"] is True
    assert [
        (entry["name"], entry["path"], entry["callable_name"], entry["logic_path"])
        for entry in _listing(client, "applications/demo_harness")
    ] == [(
        "demo_agent", "applications/demo_harness/demo_agent",
        "demo_agent", "applications/demo_harness",
    )]
    assert "example.removed" not in applications


def test_catalogued_application_in_a_package_directory_shares_its_row(
    tmp_path: Path, monkeypatch,
) -> None:
    user_root = tmp_path / "state" / "programs"
    both = user_root / "notes"
    _write(both / "__init__.py", "")
    _write(both / "application.json", '{"id": "local.notes"}')
    rows = [{"kind": "application", "source": str(both),
             "application": {"id": "local.notes", "title": "Notes", "enabled": False}}]
    monkeypatch.setattr(
        "openprogram.programs._host.ui.catalog.source_root",
        lambda row: Path(row["source"]),
    )
    client = _client(tmp_path, monkeypatch, user_root=user_root, catalog_rows=rows)

    applications = {entry["name"]: entry for entry in _listing(client, "applications")}
    assert "local.notes" not in applications
    assert applications["notes"]["application_id"] == "local.notes"
    assert applications["notes"]["enabled"] is False


def test_multi_callable_package_remains_an_expandable_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(root / "tools" / "knowledge" / "__init__.py", "")
    _write(
        root / "tools" / "knowledge" / "memory" / "__init__.py",
        "",
    )

    knowledge = _listing(client, "tools/knowledge")
    assert [
        (entry["name"], entry["program_kind"], entry["has_children"])
        for entry in knowledge
    ] == [("memory", None, True)]

    memory = _listing(client, "tools/knowledge/memory")
    assert {entry["name"] for entry in memory} == {
        "memory_get",
        "memory_search",
        "memory_update",
    }
    assert all(entry["program_kind"] == "tool" for entry in memory)


def test_unknown_application_directory_keeps_its_name() -> None:
    from openprogram.interfaces.server.routes.catalog.programs import _callable_name

    assert _callable_name("applications/notes_harness") == "notes_harness"


def test_programs_logic_builds_transitive_application_calls(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert [(node["name"], node["depth"]) for node in payload["nodes"]] == [
        ("sample_pipeline", 0),
        ("literature_review", 1),
        ("paper_search", 2),
    ]
    assert payload["edges"] == [
        {
            "source": "applications/sample_pipeline",
            "target": "applications/literature_review",
        },
        {"source": "applications/literature_review", "target": "applications/paper_search"},
    ]


def test_programs_logic_includes_agentic_programming_primitive_chain(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "applications" / "sample_pipeline" / "workflow.py",
        "from openprogram.runtime.agent import agent\nfrom openprogram import Agent\n\nclass SamplePipelineAgent(Agent):\n    method_options = {\n        'sample_pipeline': {\n            'name': 'sample_pipeline',\n            'tool': True\n        },\n    }\n\n    def sample_pipeline(self, task: str):\n        return agent(task)\n\n\nsample_pipeline = SamplePipelineAgent().sample_pipeline\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()

    assert [
        (node["name"], node["program_kind"], node["depth"]) for node in payload["nodes"]
    ] == [
        ("sample_pipeline", "application", 0),
        ("agent", "runtime_primitive", 1),
        ("llm", "runtime_primitive", 2),
    ]
    assert payload["edges"] == [
        {"source": "applications/sample_pipeline", "target": "agentic_programming/agent"},
        {"source": "agentic_programming/agent", "target": "agentic_programming/llm"},
    ]


def test_programs_explorer_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/programs/explorer", params={"path": "../outside"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid programs path"


def test_old_roots_are_gone(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    for path in ("workflow", "packages", "workflow/alpha", "packages/alpha"):
        assert client.get("/api/programs/explorer", params={"path": path}).status_code == 404
    assert client.get(
        "/api/programs/logic", params={"path": "workflow/sample_pipeline"},
    ).status_code == 404


def test_programs_explorer_ignores_symlinks_outside_root(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    external = tmp_path / "external"
    _write(external / "__init__.py", "")
    _write(
        external / "workflow.py", "import openprogram.programs.applications.paper_search\n"
    )
    (root / "applications" / "leak").symlink_to(external, target_is_directory=True)
    (root / "applications" / "leak.py").symlink_to(external / "workflow.py")

    names = {entry["name"] for entry in _listing(client, "applications")}

    assert "leak" not in names
    assert "leak.py" not in names
    assert (
        client.get("/api/programs/logic", params={"path": "applications/leak"}).status_code
        == 404
    )

    _write(
        external / "linked.py",
        "import openprogram.programs.applications.alpha\n",
    )
    (root / "applications" / "sample_pipeline" / "linked.py").symlink_to(
        external / "linked.py"
    )
    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()
    assert "applications/alpha" not in {edge["target"] for edge in payload["edges"]}
    assert payload["analysis_complete"] is True
    assert "linked" not in {
        entry["name"] for entry in _listing(client, "applications/sample_pipeline")
    }


def test_programs_logic_resolves_relative_imports(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "applications" / "sample_pipeline" / "workflow.py",
        "from ..literature_review import literature_review as run\n"
        "\n"
        "def sample_pipeline(task: str):\n"
        "    return run()\n",
    )
    _write(
        root / "applications" / "literature_review" / "__init__.py",
        "from ..paper_search import run\n"
        "\n"
        "def literature_review(task: str):\n"
        "    return run()\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()

    assert payload["edges"] == [
        {
            "source": "applications/sample_pipeline",
            "target": "applications/literature_review",
        },
        {"source": "applications/literature_review", "target": "applications/paper_search"},
    ]


def test_programs_logic_skips_oversized_python_sources(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "applications" / "sample_pipeline" / "large.py",
        "import openprogram.programs.applications.alpha\n#" + "x" * 1_000_000,
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()

    assert "applications/alpha" not in {edge["target"] for edge in payload["edges"]}
    assert payload["analysis_complete"] is False
    assert payload["analysis_warnings"] == ["oversized_source"]


def test_programs_logic_reports_source_file_limit(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "applications" / "sample_pipeline"
    for index in range(201):
        _write(root / f"part_{index:03d}.py", "")

    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_file_limit" in payload["analysis_warnings"]


def test_programs_logic_reports_unparseable_source(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "applications" / "sample_pipeline"
    _write(
        root / "broken.py",
        "import openprogram.programs.applications.alpha\ndef broken(:\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_parse_failed" in payload["analysis_warnings"]


def test_tool_package_lists_supporting_source_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    root = tmp_path / "openprogram" / "programs"
    package = root / "tools" / "planning" / "goal"
    _write(package / "__init__.py", "")
    _write(package / "goal.py", "def goal():\n    return None\n")
    _write(package / "command.py", "def handle_goal_command():\n    return None\n")
    _write(package / "judge.py", "def judge_goal():\n    return None\n")
    indexed = {
        "tools/planning/goal": [
            {
                "name": "goal",
                "description": "Goal loop",
                "source": package / "goal.py",
            },
        ],
    }
    monkeypatch.setattr(programs_routes, "PROGRAMS_ROOT", root)
    monkeypatch.setattr(programs_routes, "_registered_agent_methods", lambda: indexed)

    entries = {
        entry["name"]: entry
        for entry in programs_routes._list_entries("tools/planning/goal")["entries"]
    }
    assert entries["goal"]["callable_name"] == "goal"
    assert entries["goal"]["program_kind"] == "tool"
    assert "callable_name" not in entries["command"]
    assert entries["command"]["path"] == "tools/planning/goal/command"
    assert entries["command"]["program_kind"] is None
    assert set(entries) == {"goal", "command", "judge"}

    logic = programs_routes._program_logic("tools/planning/goal/command")
    assert logic["root"] == "tools/planning/goal/command"
    assert logic["nodes"][0]["program_kind"] is None


def test_user_packages_are_listed_with_state_and_import_edges(tmp_path, monkeypatch):
    user_root = tmp_path / "user-programs"
    names = (
        "weekly_report",
        "personal_weekly_report",
        "personal_chat_weekly_report",
        "group_weekly_report",
        "tencent_weekly_report",
    )
    client = _client(tmp_path, monkeypatch, user_root=user_root)
    monkeypatch.setattr(
        "openprogram.programs._host.packages.package_statuses",
        lambda: [{"name": name, "path": str(user_root / name), "state": "loaded",
                  "error": "", "note": "", "entries": [name], "revision": "abc"}
                 for name in names],
    )
    monkeypatch.setattr("openprogram.programs._host.runtime._registry", registered := {})
    for name in names:
        directory = user_root / name
        source = directory / "workflow.py"
        code = f"def {name}(task: str):\n    return task\n"
        if name == "weekly_report":
            code = (
                "from personal_weekly_report import personal_weekly_report\n"
                "from personal_chat_weekly_report import personal_chat_weekly_report\n"
                "from group_weekly_report import group_weekly_report\n"
                "from tencent_weekly_report import tencent_weekly_report\n"
                "def weekly_report(task: str):\n    personal_weekly_report(task)\n"
                "    personal_chat_weekly_report(task)\n    group_weekly_report(task)\n"
                "    tencent_weekly_report(task)\n"
            )
        _write(directory / "__init__.py", f"from .workflow import {name}\nPROGRAM_ENTRIES = [{name}]\n")
        _write(source, code)
        namespace = {"__name__": f"{name}.workflow"}
        exec(
            compile(f"def {name}(task):\n    return task\n", str(source), "exec"),
            namespace,
        )
        registered[name] = types.SimpleNamespace(
            name=name,
            _is_agent_method=True,
            _python_callable=namespace[name],
            _source_module=namespace["__name__"],
            _workflow_owner=name,
            description=name,
        )

    entries = {row["name"]: row for row in _listing(client, "applications")}
    for name in names:
        assert entries[name]["origin"] == "installed"
        assert entries[name]["load_state"] == "loaded"
        assert entries[name]["callable_name"] == name
        assert entries[name]["source_path"] == str((user_root / name).resolve())
    assert [
        (row["name"], row["program_kind"], row["logic_path"])
        for row in _listing(client, "applications/weekly_report")
    ] == [("weekly_report", "application", "applications/weekly_report")]

    logic = client.get(
        "/api/programs/logic", params={"path": "applications/weekly_report"}
    ).json()
    assert {
        edge["target"] for edge in logic["edges"] if edge["source"] == logic["root"]
    } == {"applications/" + name for name in names if name != "weekly_report"}


def test_folder_without_a_program_marker_is_not_an_application(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(root / "applications" / "weekly_report" / "README.md", "docs only\n")

    names = {entry["name"] for entry in _listing(client, "applications")}
    assert "weekly_report" not in names
    assert (
        client.get(
            "/api/programs/explorer",
            params={"path": "applications/weekly_report"},
        ).status_code
        == 404
    )


def test_incremental_logic_reuses_unchanged_source_and_detects_edits(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    source_reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.suffix == ".py" and path.is_relative_to(tmp_path):
            source_reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    url = "/api/programs/logic?path=applications/sample_pipeline"
    cold = client.get(url).json()
    assert source_reads
    source_reads.clear()
    warm = client.get(
        url,
        params={
            "path": "applications/sample_pipeline",
            "revision": cold.get("revision", "missing"),
        },
    ).json()
    assert source_reads == []
    assert warm == {"revision": cold["revision"], "unchanged": True}
    source = programs_routes.PROGRAMS_ROOT / "applications/sample_pipeline/workflow.py"
    source.write_text("def sample_pipeline(task):\n    return task\n")
    changed = client.get(
        url, params={"path": "applications/sample_pipeline", "revision": cold["revision"]}
    ).json()
    assert changed["revision"] != cold["revision"]
    assert changed["edges"] == []
    assert source_reads == [source]


def test_incremental_explorer_detects_additions_and_deletions(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    url = "/api/programs/explorer?path=applications"
    first = client.get(url).json()
    assert "revision" in first
    added = programs_routes.PROGRAMS_ROOT / "applications/new_item/__init__.py"
    _write(added, "def new_item(): pass\n")
    changed = client.get(
        url, params={"path": "applications", "revision": first["revision"]}
    ).json()
    assert any(row["path"] == "applications/new_item" for row in changed["entries"])
    added.unlink()
    added.parent.rmdir()
    removed = client.get(
        url, params={"path": "applications", "revision": changed["revision"]}
    ).json()
    assert all(row["path"] != "applications/new_item" for row in removed["entries"])


def test_incremental_loading_measurement(tmp_path, monkeypatch):
    from time import perf_counter
    from statistics import median

    client = _client(tmp_path, monkeypatch)
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    for i in range(60):
        _write(
            programs_routes.PROGRAMS_ROOT / f"applications/sample_{i}/__init__.py",
            f"def sample_{i}(): return {i}\n",
        )
    reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.suffix == ".py" and path.is_relative_to(tmp_path):
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    start = perf_counter()
    cold = client.get(
        "/api/programs/logic", params={"path": "applications/sample_pipeline"}
    )
    cold_ms = (perf_counter() - start) * 1000
    cold_reads = len(reads)
    reads.clear()
    timings = []
    for _ in range(10):
        start = perf_counter()
        result = client.get(
            "/api/programs/logic",
            params={
                "path": "applications/sample_pipeline",
                "revision": cold.json()["revision"],
            },
        )
        timings.append((perf_counter() - start) * 1000)
        assert result.json()["unchanged"] is True
    assert reads == []
    print(
        f"Programs fixture: cold={cold_ms:.2f}ms/{cold_reads} source reads; warm median={median(timings):.2f}ms/0 reads; body={len(cold.content)}->{len(result.content)} bytes"
    )


def test_cached_source_rechecks_symlink_containment(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    link = programs_routes.PROGRAMS_ROOT / "applications/linked"
    link.symlink_to(
        programs_routes.PROGRAMS_ROOT / "applications/paper_search", target_is_directory=True,
    )
    first = client.get("/api/programs/logic", params={"path": "applications/linked"})
    assert first.status_code == 200
    outside = tmp_path / "outside"
    _write(outside / "workflow.py", "def secret(): pass\n")
    link.unlink()
    link.symlink_to(outside, target_is_directory=True)
    second = client.get(
        "/api/programs/logic",
        params={"path": "applications/linked", "revision": first.json()["revision"]},
    )
    assert second.status_code == 404


def _register_entry(registered: dict, name: str, source: Path, module: str) -> None:
    namespace = {"__name__": module}
    exec(compile(f"def {name}(task):\n    return task\n", str(source), "exec"), namespace)
    registered[name] = types.SimpleNamespace(
        name=name, _is_agent_method=True, _python_callable=namespace[name],
        _source_module=module, description=f"{name} entry",
    )


def test_application_logic_is_the_union_of_its_registered_entries(tmp_path, monkeypatch):
    """An entry in a subpackage (``shop/checkout``) stands for the application."""
    client = _client(tmp_path, monkeypatch)
    applications = tmp_path / "openprogram" / "programs" / "applications"
    _write(applications / "shop" / "__init__.py", "")
    _write(
        applications / "shop" / "service.py",
        "class Service:\n    def start(self):\n        return helper()\n\n"
        "def helper():\n    pass\n",
    )
    _write(applications / "shop" / "pages.py", "def render():\n    pass\n")
    _write(applications / "shop" / "privacy.py", "def unused():\n    pass\n")
    entry_source = applications / "shop" / "checkout" / "__init__.py"
    _write(
        entry_source,
        "from openprogram.programs.applications.shop.service import Service\n"
        "from openprogram.programs.applications.shop.pages import render\n\n"
        "def checkout(task: str):\n    render()\n    return Service().start()\n",
    )
    monkeypatch.setattr("openprogram.programs._host.runtime._registry", registered := {})
    _register_entry(registered, "checkout", entry_source, "openprogram.programs.applications.shop.checkout")

    shop = {entry["name"]: entry for entry in _listing(client, "applications")}["shop"]
    assert shop["callable_name"] == "checkout"
    assert shop["description"] == "checkout entry"
    assert shop["agent_entries"] == [{"name": "checkout", "description": "checkout entry"}]
    assert "note" not in shop

    logic = client.get("/api/programs/logic", params={"path": "applications/shop"}).json()
    assert logic["entries"] == ["checkout"]
    # Calling a class analyses its methods; it is not an unresolved entry.
    assert logic["analysis_complete"] is True, logic["analysis_warnings"]
    assert {
        edge["target"] for edge in logic["edges"] if edge["source"] == "applications/shop"
    } == {"applications/shop/pages", "applications/shop/service"}


def test_application_without_an_agent_entry_gets_a_neutral_note(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    applications = tmp_path / "openprogram" / "programs" / "applications"
    _write(
        applications / "toolkit" / "__init__.py",
        "from openprogram.programs.applications.toolkit.stages import run_stage, STAGES\n"
        "__all__ = ['run_stage', 'STAGES']\n",
    )
    _write(
        applications / "toolkit" / "stages.py",
        "STAGES = ['init', 'review']\n\ndef run_stage(stage: str):\n    return stage\n",
    )
    _write(
        applications / "toolkit" / "evaluate.py",
        "from openprogram.programs.applications.toolkit.stages import STAGES\n\n"
        "def compete(stage: str):\n    return STAGES.index(stage)\n",
    )
    monkeypatch.setattr("openprogram.programs._host.runtime._registry", {})

    toolkit = {entry["name"]: entry for entry in _listing(client, "applications")}["toolkit"]
    assert "callable_name" not in toolkit
    assert toolkit["note"] == {
        "kind": "library",
        "exports": ["run_stage", "STAGES"],
    }
    logic = client.get("/api/programs/logic", params={"path": "applications/toolkit"}).json()
    assert logic["entries"] == []
    # ``STAGES`` is a module value: reaching it is not an unresolved entry.
    assert logic["analysis_complete"] is True, logic["analysis_warnings"]
    assert {
        edge["target"] for edge in logic["edges"] if edge["source"] == "applications/toolkit"
    } == {"applications/toolkit/stages"}


def test_a_builtin_application_without_entries_lists_as_a_library() -> None:
    from openprogram.interfaces.server.routes.catalog.programs import _list_entries, _program_logic

    applications = {entry["name"]: entry for entry in _list_entries("applications")["entries"]}

    browser = _program_logic("applications/browser")
    assert browser["entries"] == []
    assert browser["analysis_complete"] is True, browser["analysis_warnings"]
    assert any(edge["source"] == "applications/browser" for edge in browser["edges"])
    assert "callable_name" not in applications["browser"]
    assert applications["browser"]["note"] == {"kind": "library", "exports": ["setup"]}


def test_tool_rows_list_their_parameters() -> None:
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    assert programs_routes._tool_parameters({
        "type": "object",
        "properties": {"path": {"type": "string", "description": "A  file\npath"}, "limit": {"type": "integer"}},
        "required": ["path"],
    }) == [
        {"name": "path", "description": "A file path", "required": True},
        {"name": "limit", "description": "", "required": False},
    ]
    assert programs_routes._tool_parameters(None) == []


def test_readme_names_an_installed_package(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "state" / "programs"
    _write(user_root / "text_rewriting" / "__init__.py", "")
    _write(user_root / "text_rewriting" / "README.md", "# Text rewriting\n\nPolishes text. More here.\n")
    monkeypatch.setattr("openprogram.programs._host.packages.package_statuses", lambda: [])
    client = _client(tmp_path, monkeypatch, user_root=user_root)
    entry = {row["name"]: row for row in _listing(client, "applications")}["text_rewriting"]
    assert (entry["title"], entry["description"]) == ("Text rewriting", "Polishes text.")
    assert "icon" not in entry


def test_a_catalogued_application_lists_its_agent_operations(tmp_path: Path, monkeypatch) -> None:
    """``agent: true`` operations of application.json are what the Agent can call."""
    user_root = tmp_path / "state" / "programs"
    user_root.mkdir(parents=True)
    editor = tmp_path / "examples" / "pdf_editor"
    _write(editor / "application.json", json.dumps({
        "id": "example.pdf-editor", "title": "PDF editor", "operations": {
            "list_files": {"agent": True, "description": "List the PDF files. Newest first."},
            "annotate": {"agent": True, "description": "Add an annotation and save."},
            "chunk": {"agent": False, "description": "Editor only: a base64 slice."},
            "upload": {"agent": True, "description": "Editor only: one chunk."},
        },
    }))
    single = tmp_path / "examples" / "single"
    _write(single / "application.json", json.dumps({
        "id": "example.single", "title": "Single", "operations": {"run": {"agent": True}},
    }))
    calculator = tmp_path / "examples" / "calculator"
    _write(calculator / "application.json", json.dumps({"id": "example.calculator", "title": "Calculator"}))
    rows = [
        {"kind": "application", "source": str(path), "application": {"id": app_id, "title": title}}
        for path, app_id, title in (
            (editor, "example.pdf-editor", "PDF editor"),
            (single, "example.single", "Single"),
            (calculator, "example.calculator", "Calculator"),
        )
    ]
    monkeypatch.setattr("openprogram.programs._host.packages.package_statuses", lambda: [])
    monkeypatch.setattr("openprogram.programs._host.ui.catalog.source_root", lambda row: Path(row["source"]))
    client = _client(tmp_path, monkeypatch, user_root=user_root, catalog_rows=rows)

    applications = {entry["name"]: entry for entry in _listing(client, "applications")}
    pdf = applications["pdf_editor"]
    assert pdf["agent_entries"] == [
        {"name": "list_files", "description": "List the PDF files.", "tool": "example_pdf-editor__list_files"},
        {"name": "annotate", "description": "Add an annotation and save.", "tool": "example_pdf-editor__annotate"},
    ]
    assert "note" not in pdf
    assert "callable_name" not in pdf
    # One operation: the application is that tool.
    assert applications["single"]["callable_name"] == "example_single__run"
    assert applications["calculator"]["agent_entries"] == []
    assert applications["calculator"]["note"] == {"kind": "ui_only"}


def test_locations_are_shortened() -> None:
    from openprogram.interfaces.server.routes.catalog import programs as programs_routes

    checkout = Path(programs_routes.openprogram.__file__).resolve().parent.parent
    assert programs_routes._short_location(checkout / "examples" / "applications" / "pdf_editor") == "examples/applications/pdf_editor"
    assert programs_routes._short_location(Path.home() / ".openprogram" / "programs" / "x") == "~/.openprogram/programs/x"
    assert programs_routes._short_location(Path("/opt/apps/x")) == "/opt/apps/x"
