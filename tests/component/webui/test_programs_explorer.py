from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    root = tmp_path / "openprogram" / "programs"
    _write(root / "tools" / "web" / "__init__.py", "")
    _write(root / "tools" / "web" / "agent_browser.py", "")
    _write(root / "tools" / "web" / "browser" / "__init__.py", "")
    _write(root / "workflow" / "alpha" / "__init__.py", "")
    _write(root / "workflow" / "__init__.py", "")
    _write(
        root / "workflow" / "research_pipeline" / "workflow.py",
        "from openprogram.programs.workflow.literature_review import literature_review\n"
        "\n"
        "def research_pipeline(task: str):\n"
        "    return literature_review(task)\n",
    )
    _write(
        root / "workflow" / "literature_review" / "workflow.py",
        "from openprogram.programs.workflow.paper_search import run\n"
        "\n"
        "def literature_review(task: str):\n"
        "    return run()\n",
    )
    _write(
        root / "workflow" / "paper_search" / "workflow.py",
        "def paper_search():\n"
        "    return run()\n"
        "\n"
        "def run():\n"
        "    pass\n",
    )
    _write(root / "applications" / "research_app" / "pyproject.toml", "")
    _write(root / "applications" / "gui_harness" / "pyproject.toml", "")
    _write(root / ".hidden", "ignored")
    _write(root / "__pycache__" / "ignored.py", "")

    from openprogram.webui.routes import programs
    from openprogram.programs import _programs

    monkeypatch.setattr(programs, "PROGRAMS_ROOT", root)
    monkeypatch.setattr(
        _programs, "owner_controlled_program_sources", lambda base=None: [],
    )
    app = FastAPI()
    programs.register(app)
    return TestClient(app)


def test_programs_explorer_lists_program_catalog_lazily(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    root = client.get("/api/programs/explorer").json()
    assert [entry["path"] for entry in root["entries"]] == [
        "tools",
        "workflow",
        "applications",
    ]
    assert root["default_selection"] == "workflow/alpha"

    vanilla = client.get(
        "/api/programs/explorer", params={"path": "tools"}
    ).json()
    assert [
        (entry["name"], entry["path"], entry["program_kind"], entry["has_children"])
        for entry in vanilla["entries"]
    ] == [
        ("web", "tools/web", None, True),
    ]
    web = client.get(
        "/api/programs/explorer", params={"path": "tools/web"}
    ).json()
    assert [
        (
            entry["name"], entry["path"], entry["program_kind"],
            entry["has_children"], entry["logic_path"],
        )
        for entry in web["entries"]
    ] == [
        (
            "agent_browser",
            "tools/web/agent_browser",
            "vanilla_function",
            False,
            "tools/web/agent_browser",
        ),
        (
            "playwright_browser",
            "tools/web/browser",
            "vanilla_function",
            False,
            "tools/web/browser",
        ),
    ]

    workflows = client.get(
        "/api/programs/explorer", params={"path": "workflow"}
    ).json()
    assert [entry["name"] for entry in workflows["entries"]] == [
        "alpha",
        "literature_review",
        "paper_search",
        "research_pipeline",
    ]
    assert [entry["program_kind"] for entry in workflows["entries"]] == [
        "workflow",
        "workflow",
        "workflow",
        "workflow",
    ]
    assert all(entry["has_children"] is False for entry in workflows["entries"])

    workflow = client.get(
        "/api/programs/explorer", params={"path": "workflow/research_pipeline"}
    )
    assert workflow.status_code == 404

    applications = client.get(
        "/api/programs/explorer", params={"path": "applications"}
    ).json()
    assert [
        (entry["name"], entry["program_kind"], entry["callable_name"])
        for entry in applications["entries"]
    ] == [
        ("gui_harness", "application", "gui_agent"),
        ("research_app", "application", "research_app"),
    ]


def test_multi_callable_package_remains_an_expandable_group(
    tmp_path: Path, monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(root / "tools" / "knowledge" / "__init__.py", "")
    _write(
        root / "tools" / "knowledge" / "memory" / "__init__.py",
        "",
    )

    knowledge = client.get(
        "/api/programs/explorer",
        params={"path": "tools/knowledge"},
    ).json()
    assert [
        (entry["name"], entry["program_kind"], entry["has_children"])
        for entry in knowledge["entries"]
    ] == [("memory", None, True)]

    memory = client.get(
        "/api/programs/explorer",
        params={"path": "tools/knowledge/memory"},
    ).json()
    assert {entry["name"] for entry in memory["entries"]} == {
        "memory_browse",
        "memory_get",
        "memory_grep",
        "memory_promote",
        "memory_search",
        "memory_status",
        "memory_update",
    }
    assert all(entry["program_kind"] == "vanilla_function" for entry in memory["entries"])


def test_known_application_directories_resolve_to_registered_callables() -> None:
    from openprogram.webui.routes.programs import _callable_name

    assert _callable_name("applications/gui_harness") == "gui_agent"
    assert _callable_name("applications/research_harness") == "research_agent"
    assert _callable_name("applications/wiki_agent_harness") == "wiki_agent"


def test_agentic_source_directory_resolves_to_registered_callable() -> None:
    from openprogram.webui.routes.programs import _callable_name

    assert _callable_name("workflow/docs_question") == "run_docs_question"
    assert _callable_name("workflow/security_review") == "run_security_review"


def test_installed_app_uses_owner_recorded_program_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    installed_root = tmp_path / "installed" / "openprogram" / "programs"
    _write(installed_root / "workflow" / "alpha" / "__init__.py", "")
    _write(installed_root / "workflow" / "__init__.py", "")
    _write(installed_root / "applications" / "__init__.py", "")

    source_root = tmp_path / "checkout" / "openprogram" / "programs"
    application = source_root / "applications" / "gui_harness"
    _write(application / "pyproject.toml", "")
    _write(source_root / "workflow" / "literature_review" / "workflow.py", "")

    from openprogram.programs import _programs
    from openprogram.webui.routes import programs

    monkeypatch.setattr(programs, "PROGRAMS_ROOT", installed_root)
    monkeypatch.setattr(
        _programs,
        "owner_controlled_program_sources",
        lambda base=None: [{"path": str(application)}],
    )
    app = FastAPI()
    programs.register(app)
    client = TestClient(app)

    applications = client.get(
        "/api/programs/explorer", params={"path": "applications"}
    ).json()
    workflows = client.get(
        "/api/programs/explorer", params={"path": "workflow"}
    ).json()

    assert [entry["name"] for entry in applications["entries"]] == ["gui_harness"]
    assert [entry["name"] for entry in workflows["entries"]] == [
        "alpha",
        "literature_review",
    ]


def test_programs_logic_builds_transitive_workflow_calls(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert [(node["name"], node["depth"]) for node in payload["nodes"]] == [
        ("research_pipeline", 0),
        ("literature_review", 1),
        ("paper_search", 2),
    ]
    assert payload["edges"] == [
        {"source": "workflow/research_pipeline", "target": "workflow/literature_review"},
        {"source": "workflow/literature_review", "target": "workflow/paper_search"},
    ]


def test_programs_logic_includes_agentic_programming_primitive_chain(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "workflow" / "research_pipeline" / "workflow.py",
        "from openprogram.agentic_programming import agentic_function, agent\n"
        "\n"
        "@agentic_function\n"
        "def research_pipeline(task: str):\n"
        "    return agent(task)\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()

    assert [
        (node["name"], node["program_kind"], node["depth"])
        for node in payload["nodes"]
    ] == [
        ("research_pipeline", "workflow", 0),
        ("agent", "runtime_primitive", 1),
        ("llm", "runtime_primitive", 2),
    ]
    assert payload["edges"] == [
        {"source": "workflow/research_pipeline", "target": "agentic_programming/agent"},
        {"source": "agentic_programming/agent", "target": "agentic_programming/llm"},
    ]


def test_programs_explorer_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/programs/explorer", params={"path": "../outside"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid programs path"


def test_programs_explorer_ignores_symlinks_outside_root(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    external = tmp_path / "external"
    _write(external / "workflow.py", "import openprogram.programs.workflow.paper_search\n")
    (root / "workflow" / "leak").symlink_to(external, target_is_directory=True)
    (root / "workflow" / "leak.py").symlink_to(external / "workflow.py")

    workflows = client.get(
        "/api/programs/explorer", params={"path": "workflow"}
    ).json()

    assert "leak" not in {entry["name"] for entry in workflows["entries"]}
    assert "leak.py" not in {entry["name"] for entry in workflows["entries"]}
    assert client.get(
        "/api/programs/logic", params={"path": "workflow/leak"}
    ).status_code == 404

    _write(
        external / "linked.py",
        "import openprogram.programs.workflow.alpha\n",
    )
    (root / "workflow" / "research_pipeline" / "linked.py").symlink_to(
        external / "linked.py"
    )
    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()
    assert "workflow/alpha" not in {
        edge["target"] for edge in payload["edges"]
    }
    assert payload["analysis_complete"] is True


def test_programs_logic_resolves_relative_workflow_imports(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "workflow" / "research_pipeline" / "workflow.py",
        "from ..literature_review import run\n"
        "\n"
        "def research_pipeline(task: str):\n"
        "    return run()\n",
    )
    _write(
        root / "workflow" / "literature_review" / "__init__.py",
        "from ..paper_search import run\n"
        "\n"
        "def literature_review(task: str):\n"
        "    return run()\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()

    assert payload["edges"] == [
        {"source": "workflow/research_pipeline", "target": "workflow/literature_review"},
        {"source": "workflow/literature_review", "target": "workflow/paper_search"},
    ]


def test_programs_logic_skips_oversized_python_sources(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "workflow" / "research_pipeline" / "large.py",
        "import openprogram.programs.workflow.alpha\n#" + "x" * 1_000_000,
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()

    assert "workflow/alpha" not in {
        edge["target"] for edge in payload["edges"]
    }
    assert payload["analysis_complete"] is False
    assert payload["analysis_warnings"] == ["oversized_source"]


def test_programs_logic_reports_source_file_limit(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "workflow" / "research_pipeline"
    for index in range(201):
        _write(root / f"part_{index:03d}.py", "")

    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_file_limit" in payload["analysis_warnings"]


def test_programs_logic_reports_unparseable_source(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "workflow" / "research_pipeline"
    _write(
        root / "broken.py",
        "import openprogram.programs.workflow.alpha\ndef broken(:\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflow/research_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_parse_failed" in payload["analysis_warnings"]
