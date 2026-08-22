from __future__ import annotations

import json
from pathlib import Path
import re
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[3]
TOP_LEVEL_DIRECTORIES = {
    ".codegraph",
    ".github",
    ".superpowers",
    "apps",
    "docs",
    "openprogram",
    "references",
    "scripts",
    "website",
    "tests",
}

TOP_LEVEL_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CITATION.cff",
    "CLAUDE.md",
    "LICENSE",
    "package-lock.json",
    "package.json",
    "README.md",
    "pyproject.toml",
    "uv.lock",
}

MAINTENANCE_TOOL_PATHS = (
    "scripts/dag_dump.py",
    "scripts/docs_site/__init__.py",
    "scripts/docs_site/build.py",
)
CURRENT_STRUCTURE_GUIDES = (
    "README.md",
    "docs/README.md",
    "docs/README.zh.md",
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


def test_tracked_top_level_files_are_intentional() -> None:
    actual = {path for path in _tracked_paths() if "/" not in path}

    assert actual == TOP_LEVEL_FILES


def test_repository_maintenance_tools_live_under_scripts() -> None:
    tracked = set(_tracked_paths())

    assert set(MAINTENANCE_TOOL_PATHS) <= tracked
    assert not any(path == "tools" or path.startswith("tools/") for path in tracked)


def test_context_git_lives_inside_the_context_domain() -> None:
    tracked = set(_tracked_paths())

    assert "openprogram/context/git/__init__.py" in tracked
    assert "openprogram/context/git/dag.py" in tracked
    assert not any(path.startswith("openprogram/contextgit/") for path in tracked)


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


def _finder_numbered_duplicates(root: Path) -> list[str]:
    numbered_copy = re.compile(r".* [2-9][0-9]*(?:\..*)?$")
    source_roots = ("openprogram", "apps", "scripts", "tests", "docs/reference")
    generated_roots = (
        "openprogram/programs/applications/gui_harness/",
        "apps/server/openprogram_server/_webui/_frontend/",
    )

    def belongs_to_nested_git(path: Path) -> bool:
        for parent in path.parents:
            if parent == root:
                return False
            if (parent / ".git").exists():
                return True
        return False

    duplicates = sorted(
        path.relative_to(root).as_posix()
        for source_root in source_roots
        for path in (root / source_root).rglob("*")
        if not belongs_to_nested_git(path)
        and "__pycache__" not in path.parts
        and "node_modules" not in path.parts
        and not path.relative_to(root).as_posix().startswith(generated_roots)
        and numbered_copy.fullmatch(path.name)
    )

    return duplicates


def test_source_tree_has_no_finder_numbered_duplicates() -> None:
    duplicates = _finder_numbered_duplicates(ROOT)

    assert duplicates == []


def test_finder_duplicate_check_covers_files_and_directories_but_skips_nested_git(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "Makefile 2").touch()
    (tmp_path / "tests" / "fixtures 3").mkdir(parents=True)
    runtime = tmp_path / "openprogram/programs/workflow/user_workflow"
    runtime.mkdir(parents=True)
    (runtime / ".git").write_text("gitdir: /tmp/user-workflow.git\n")
    (runtime / "workflow 2.py").touch()

    assert _finder_numbered_duplicates(tmp_path) == [
        "scripts/Makefile 2",
        "tests/fixtures 3",
    ]


def test_finder_duplicate_check_does_not_escape_repository_root(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    checkout = tmp_path / "checkout"
    (checkout / "scripts/helpers 2").mkdir(parents=True)

    assert _finder_numbered_duplicates(checkout) == ["scripts/helpers 2"]


def test_current_structure_guides_do_not_reference_removed_roots() -> None:
    removed_roots = (
        "openprogram/functions/",
        "openprogram/programs/workflow/<Repo-Name>",
        "openprogram/programs/workflow/{GUI,Research}",
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
    assert "skills_bundled/" not in python_readme
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

    assert "cli" in first_level_packages
    assert all(relative in tracked for relative in PYTHON_CLI_PATHS)
    assert all(
        relative not in tracked
        and not any(path.startswith(f"{relative}/") for path in tracked)
        for relative in REMOVED_PYTHON_CLI_PATHS
    )


def test_generated_cli_reference_names_the_current_parser_source(tmp_path) -> None:
    generator = runpy.run_path(
        str(ROOT / "scripts" / "docs_site" / "generate_reference.py")
    )
    generator["generate_cli"](tmp_path)
    pages = sorted((tmp_path / "reference" / "cli").glob("*.md"))

    assert pages
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "from apps/cli/python/openprogram_cli/_impl/parser.py" in text
        assert "from openprogram/cli.py" not in text


def test_programs_route_uses_the_programs_workspace_component() -> None:
    route = (ROOT / "apps/web/app/(shell)/programs/page.tsx").read_text(encoding="utf-8")

    assert "@/components/capabilities/capabilities-page" in route
    hub = (ROOT / "apps/web/components/capabilities/capabilities-page.tsx").read_text(encoding="utf-8")
    assert "@/components/programs/programs-page" in hub
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


def test_user_docs_do_not_advertise_removed_bundled_skills() -> None:
    user_docs = [
        ROOT / "README.md",
        ROOT / "docs/README.md",
        ROOT / "docs/README.zh.md",
        *sorted((ROOT / "docs/capabilities").glob("*.md")),
        *sorted((ROOT / "docs/start").glob("*.md")),
        ROOT / "docs/reference/design/runtime/session/distill.md",
        ROOT / "docs/reference/design/runtime/session/distill.zh.md",
        ROOT / "openprogram/__init__.py",
        ROOT / "openprogram/skills/loader.py",
        ROOT / "openprogram/agentic_programming/runtime.py",
        ROOT / "apps/cli/python/openprogram_cli/_impl/parser.py",
        ROOT / "apps/cli/python/openprogram_cli/_impl/commands/skills.py",
        ROOT / "apps/server/openprogram_server/_webui/routes/functions.py",
        ROOT / "apps/server/openprogram_server/_webui/routes/skills.py",
    ]
    stale_markers = (
        "skills_bundled/",
        "five sources",
        "five standard sources",
        "五个来源",
        "load the agentic-programming skill",
    )
    stale = []
    for path in user_docs:
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in stale_markers):
            stale.append(path.relative_to(ROOT).as_posix())
    assert stale == []


def test_formal_distribution_implementation_is_grouped_under_release() -> None:
    release_files = {
        "archive-product-runtime.sh",
        "build-product-runtime.sh",
        "create-release-manifest.py",
        "install-release.sh",
        "prepare-desktop-runtime.sh",
        "product-runtime.json",
        "smoke-packaged-runtime.sh",
        "stage-release-assets.sh",
        "verify-product-runtime.py",
        "verify-release-version.py",
    }

    assert all(
        (ROOT / "scripts" / "release" / name).is_file() for name in release_files
    )
    assert all(
        not (ROOT / "scripts" / name).exists()
        for name in release_files - {"install-release.sh"}
    )
    assert (ROOT / "scripts" / "install-release.sh").is_file()


def test_node_apps_share_one_root_npm_workspace_lock() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["private"] is True
    assert package["workspaces"] == ["apps/web", "apps/desktop", "apps/cli"]
    assert (ROOT / "package-lock.json").is_file()
    assert all(
        not (ROOT / "apps" / app / "package-lock.json").exists()
        for app in ("web", "desktop", "cli")
    )
