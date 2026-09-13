from __future__ import annotations

from pathlib import Path

import openprogram


EXPECTED_SOURCES = {
    "files": {
        "apply_patch", "bash", "edit", "glob", "grep", "list", "process",
        "read", "worktree", "write",
    },
    "web": {
        "agent_browser", "browser", "image_analyze", "image_generate", "pdf",
        "web_fetch", "web_search",
    },
    "knowledge": {"memory", "read_conversation"},
    "planning": {"plan_mode", "todo"},
    "agents": {"agent", "mixture_of_agents", "send_message"},
    "jobs": {"cron"},
    "code": {"execute_code", "lsp", "semble"},
    "interaction": {"canvas", "clarify", "send_file"},
    "runtime": {"mcp_meta", "program", "skill"},
    "system": {"self_update"},
}


def test_shipped_vanilla_sources_use_physical_purpose_directories() -> None:
    root = Path(openprogram.__file__).resolve().parent / "programs/tools"

    categories = {
        child.name
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith("_")
    }
    assert categories == set(EXPECTED_SOURCES)

    for category, expected in EXPECTED_SOURCES.items():
        sources = {
            child.stem
            for child in (root / category).iterdir()
            if not child.name.startswith("_")
            and (child.is_dir() or child.suffix == ".py")
            and child.name != "file_safety.py"
        }
        assert sources == expected

    assert (root / "web/browser").is_dir()
    assert (root / "web/agent_browser.py").is_file()
    assert not (root / "browser").exists()


def test_single_file_tools_are_modules_and_multi_file_tools_are_packages() -> None:
    root = Path(openprogram.__file__).resolve().parent / "programs/tools"

    assert (root / "files/read.py").is_file()
    assert not (root / "files/read").exists()
    assert (root / "web/browser/browser.py").is_file()
    assert (root / "web/browser/_actions").is_dir()
