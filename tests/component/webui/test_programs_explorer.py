from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    root = tmp_path / "openprogram" / "programs"
    _write(root / "functions" / "vanilla" / "read_file.py", "def read_file(): pass\n")
    _write(root / "functions" / "agentic" / "alpha" / "__init__.py", "")
    _write(root / "workflows" / "__init__.py", "")
    _write(
        root / "workflows" / "research_pipeline" / "workflow.py",
        "from openprogram.programs.workflows import literature_review\n",
    )
    _write(
        root / "workflows" / "literature_review" / "workflow.py",
        "from openprogram.programs.workflows.paper_search import run\n",
    )
    _write(root / "workflows" / "paper_search" / "workflow.py", "def run(): pass\n")
    _write(root / "applications" / "research_app" / "pyproject.toml", "")
    _write(root / "applications" / "gui_harness" / "pyproject.toml", "")
    _write(root / ".hidden", "ignored")
    _write(root / "__pycache__" / "ignored.py", "")

    from openprogram.webui.routes import programs

    monkeypatch.setattr(programs, "PROGRAMS_ROOT", root)
    app = FastAPI()
    programs.register(app)
    return TestClient(app)


def test_programs_explorer_lists_program_catalog_lazily(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    root = client.get("/api/programs/explorer").json()
    assert [entry["path"] for entry in root["entries"]] == [
        "functions",
        "workflows",
        "applications",
    ]
    assert root["default_selection"] == "workflows/literature_review"

    functions = client.get(
        "/api/programs/explorer", params={"path": "functions"}
    ).json()
    assert [entry["path"] for entry in functions["entries"]] == [
        "functions/vanilla",
        "functions/agentic",
    ]
    vanilla = client.get(
        "/api/programs/explorer", params={"path": "functions/vanilla"}
    ).json()
    assert [(entry["name"], entry["program_kind"]) for entry in vanilla["entries"]] == [
        ("read_file", "vanilla_function"),
    ]

    workflows = client.get(
        "/api/programs/explorer", params={"path": "workflows"}
    ).json()
    assert [entry["name"] for entry in workflows["entries"]] == [
        "literature_review",
        "paper_search",
        "research_pipeline",
    ]
    assert [entry["program_kind"] for entry in workflows["entries"]] == [
        "workflow",
        "workflow",
        "workflow",
    ]
    assert all(entry["has_children"] is False for entry in workflows["entries"])

    workflow = client.get(
        "/api/programs/explorer", params={"path": "workflows/research_pipeline"}
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


def test_known_application_directories_resolve_to_registered_callables() -> None:
    from openprogram.webui.routes.programs import _callable_name

    assert _callable_name("applications/gui_harness") == "gui_agent"
    assert _callable_name("applications/research_harness") == "research_agent"
    assert _callable_name("applications/wiki_agent_harness") == "wiki_agent"


def test_programs_logic_builds_transitive_workflow_calls(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert [(node["name"], node["depth"]) for node in payload["nodes"]] == [
        ("research_pipeline", 0),
        ("literature_review", 1),
        ("paper_search", 2),
    ]
    assert payload["edges"] == [
        {"source": "workflows/research_pipeline", "target": "workflows/literature_review"},
        {"source": "workflows/literature_review", "target": "workflows/paper_search"},
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
    _write(external / "workflow.py", "import openprogram.programs.workflows.paper_search\n")
    (root / "workflows" / "leak").symlink_to(external, target_is_directory=True)
    (root / "workflows" / "leak.py").symlink_to(external / "workflow.py")

    workflows = client.get(
        "/api/programs/explorer", params={"path": "workflows"}
    ).json()

    assert "leak" not in {entry["name"] for entry in workflows["entries"]}
    assert "leak.py" not in {entry["name"] for entry in workflows["entries"]}
    assert client.get(
        "/api/programs/logic", params={"path": "workflows/leak"}
    ).status_code == 404

    _write(
        external / "linked.py",
        "import openprogram.programs.functions.agentic.alpha\n",
    )
    (root / "workflows" / "research_pipeline" / "linked.py").symlink_to(
        external / "linked.py"
    )
    payload = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    ).json()
    assert "functions/agentic/alpha" not in {
        edge["target"] for edge in payload["edges"]
    }
    assert payload["analysis_complete"] is True


def test_programs_logic_resolves_relative_workflow_imports(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "workflows" / "research_pipeline" / "workflow.py",
        "from ..literature_review import run\n",
    )
    _write(
        root / "workflows" / "literature_review" / "__init__.py",
        "from ..paper_search import run\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    ).json()

    assert payload["edges"] == [
        {"source": "workflows/research_pipeline", "target": "workflows/literature_review"},
        {"source": "workflows/literature_review", "target": "workflows/paper_search"},
    ]


def test_programs_logic_skips_oversized_python_sources(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs"
    _write(
        root / "workflows" / "research_pipeline" / "large.py",
        "import openprogram.programs.functions.agentic.alpha\n#" + "x" * 1_000_000,
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    ).json()

    assert "functions/agentic/alpha" not in {
        edge["target"] for edge in payload["edges"]
    }
    assert payload["analysis_complete"] is False
    assert payload["analysis_warnings"] == ["oversized_source"]


def test_programs_logic_reports_source_file_limit(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "workflows" / "research_pipeline"
    for index in range(201):
        _write(root / f"part_{index:03d}.py", "")

    payload = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_file_limit" in payload["analysis_warnings"]


def test_programs_logic_reports_unparseable_source(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    root = tmp_path / "openprogram" / "programs" / "workflows" / "research_pipeline"
    _write(
        root / "broken.py",
        "import openprogram.programs.functions.agentic.alpha\ndef broken(:\n",
    )

    payload = client.get(
        "/api/programs/logic", params={"path": "workflows/research_pipeline"}
    ).json()

    assert payload["analysis_complete"] is False
    assert "source_parse_failed" in payload["analysis_warnings"]
