from __future__ import annotations

from pathlib import Path
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TOP_LEVEL_DIRECTORIES = {
    ".codegraph",
    ".github",
    ".superpowers",
    "cli",
    "config",
    "desktop",
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
    "web",
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
    "web/README.md",
    "cli/README.md",
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
    web_readme = (ROOT / "web/README.md").read_text(encoding="utf-8")
    cli_readme = (ROOT / "cli/README.md").read_text(encoding="utf-8")

    assert "programs/" in python_readme
    assert "skills_bundled/" in python_readme
    assert "OpenProgram Web workspace" in web_readme
    assert "create-next-app" not in web_readme
    assert "Ink" in cli_readme
    assert "dist/index.js" in cli_readme


def test_programs_route_has_no_reexport_only_component_directory() -> None:
    route = (ROOT / "web/app/(shell)/programs/page.tsx").read_text(encoding="utf-8")

    assert "@/components/functions/functions-page" in route
    assert not (ROOT / "web/components/programs/programs-page.tsx").exists()
    assert not (ROOT / "web/components/programs/programs-page.module.css").exists()


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
