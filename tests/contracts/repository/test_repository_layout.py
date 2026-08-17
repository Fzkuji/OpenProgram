from __future__ import annotations

from pathlib import Path
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TOP_LEVEL_DIRECTORIES = {
    ".codegraph",
    ".github",
    ".superpowers",
    "apps",
    "config",
    "docs",
    "examples",
    "experiments",
    "openprogram",
    "promo",
    "references",
    "scripts",
    "site",
    "skills",
    "tests",
    "tools",
}
CURRENT_STRUCTURE_GUIDES = (
    "README.md",
    "docs/README.md",
    "docs/README.zh.md",
    "skills/agentic-programming/SKILL.md",
    "openprogram/skills_bundled/agentic-programming/SKILL.md",
    "docs/capabilities/installing-harnesses.md",
    "docs/capabilities/installing-harnesses.zh.md",
    "docs/server/troubleshooting.md",
    "docs/server/troubleshooting.zh.md",
)

WORKSPACE_READMES = (
    "openprogram/README.md",
    "apps/server/README.md",
    "apps/web/README.md",
    "apps/desktop/README.md",
    "apps/cli/README.md",
)

PYTHON_CLI_PATHS = (
    "apps/cli/python/openprogram_cli/__init__.py",
    "apps/cli/python/openprogram_cli/__main__.py",
    "apps/cli/python/openprogram_cli/_impl/application.py",
    "apps/cli/python/openprogram_cli/_impl/parser.py",
    "apps/cli/python/openprogram_cli/_impl/chat.py",
    "apps/cli/python/openprogram_cli/_impl/ink.py",
    "apps/cli/python/openprogram_cli/_impl/commands/__init__.py",
    "apps/cli/python/openprogram_cli/_impl/repl/__init__.py",
    "apps/cli/python/openprogram_cli/_impl/setup_sections/__init__.py",
    "openprogram/cli/__init__.py",
    "openprogram/cli/__main__.py",
)

REMOVED_PYTHON_CLI_PATHS = (
    "openprogram/cli.py",
    "openprogram/_cli_parser.py",
    "openprogram/cli_chat.py",
    "openprogram/cli_ink.py",
    "openprogram/_cli_chat",
    "openprogram/_cli_cmds",
    "openprogram/_setup_sections",
    "openprogram/cli/parser.py",
    "openprogram/cli/chat.py",
    "openprogram/cli/ink.py",
    "openprogram/cli/commands",
    "openprogram/cli/repl",
    "openprogram/cli/setup_sections",
)


def _tracked_paths() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(raw.decode() for raw in result.stdout.split(b"\0") if raw)


def test_tracked_top_level_directories_are_declared() -> None:
    actual = {path.split("/", 1)[0] for path in _tracked_paths() if "/" in path}

    assert actual == TOP_LEVEL_DIRECTORIES


def test_developer_scripts_do_not_live_at_the_repository_root() -> None:
    script_suffixes = {".py", ".ps1", ".sh"}
    misplaced = sorted(
        relative
        for relative in _tracked_paths()
        if "/" not in relative
        and (ROOT / relative).is_file()
        and Path(relative).suffix in script_suffixes
    )

    assert misplaced == []


def test_current_structure_guides_do_not_reference_removed_roots() -> None:
    removed_roots = (
        "openprogram/functions/",
        "openprogram/programs/functions/agentic/<Repo-Name>",
        "openprogram/programs/functions/agentic/{GUI,Research}",
    )
    stale_references = sorted(
        f"{relative}: {removed}"
        for relative in CURRENT_STRUCTURE_GUIDES
        for removed in removed_roots
        if removed in (ROOT / relative).read_text(encoding="utf-8")
    )

    assert stale_references == []


def test_workspace_entry_readmes_describe_current_ownership() -> None:
    missing = [
        relative for relative in WORKSPACE_READMES if not (ROOT / relative).is_file()
    ]

    assert missing == []

    python_readme = (ROOT / "openprogram/README.md").read_text(encoding="utf-8")
    server_readme = (ROOT / "apps/server/README.md").read_text(encoding="utf-8")
    web_readme = (ROOT / "apps/web/README.md").read_text(encoding="utf-8")
    desktop_readme = (ROOT / "apps/desktop/README.md").read_text(encoding="utf-8")
    cli_readme = (ROOT / "apps/cli/README.md").read_text(encoding="utf-8")

    assert "programs/" in python_readme
    assert "skills_bundled/" in python_readme
    assert "FastAPI" in server_readme
    assert "openprogram_server" in server_readme
    assert "OpenProgram Web workspace" in web_readme
    assert "create-next-app" not in web_readme
    assert "Electron" in desktop_readme
    assert "Ink" in cli_readme
    assert "dist/index.js" in cli_readme


def test_python_cli_implementation_is_owned_by_the_cli_app() -> None:
    tracked = set(_tracked_paths())
    first_level_packages = {
        path.split("/", 2)[1]
        for path in tracked
        if path.startswith("openprogram/") and path.count("/") >= 2
    }

    assert len(first_level_packages) == 31
    assert all(relative in tracked for relative in PYTHON_CLI_PATHS)
    assert all(
        relative not in tracked
        and not any(path.startswith(f"{relative}/") for path in tracked)
        for relative in REMOVED_PYTHON_CLI_PATHS
    )


def test_generated_cli_reference_names_the_current_parser_source(tmp_path) -> None:
    generator = runpy.run_path(str(ROOT / "tools" / "docs_site" / "generate_reference.py"))
    generator["generate_cli"](tmp_path)
    pages = sorted((tmp_path / "reference" / "cli").glob("*.md"))

    assert pages
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "from apps/cli/python/openprogram_cli/_impl/parser.py" in text
        assert "from openprogram/cli.py" not in text


def test_programs_route_uses_the_programs_workspace_component() -> None:
    route = (ROOT / "apps/web/app/(shell)/programs/page.tsx").read_text(encoding="utf-8")

    assert "@/components/programs/programs-page" in route
    assert (ROOT / "apps/web/components/programs/programs-page.tsx").is_file()
    assert (ROOT / "apps/web/components/programs/programs-page.module.css").is_file()


def test_generated_package_readmes_are_current() -> None:
    generator = runpy.run_path(str(ROOT / "scripts/gen_dir_readmes.py"))
    render = generator["_readme_for"]
    marker = "Auto-generated from `__init__.py`"
    stale = []

    for readme in sorted((ROOT / "openprogram").glob("*/README.md")):
        current = readme.read_text(encoding="utf-8")
        if marker in current and current != render(readme.parent):
            stale.append(readme.relative_to(ROOT).as_posix())

    assert stale == []
