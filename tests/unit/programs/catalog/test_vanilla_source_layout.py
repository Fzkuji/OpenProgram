from __future__ import annotations

from pathlib import Path

import openprogram


EXPECTED_SOURCES = {
    "files": {
        "apply_patch", "bash", "edit", "glob", "grep", "list",
        "read", "terminal_use", "worktree", "write", "write_stdin",
    },
    "web": {
        "image_analyze", "image_generate", "pdf",
        "web_fetch", "web_search",
    },
    "knowledge": {"memory", "read_conversation"},
    "planning": {"goal", "open_work", "plan_mode", "todo"},
    "agents": {"agent", "mixture_of_agents", "send_message"},
    "jobs": {"cron", "routine"},
    "code": {"execute_code", "lsp", "semble"},
    "interaction": {"always_on_preference", "always_on_report", "always_on_schedule", "ask_user", "canvas", "chat_send", "clarify", "send_file"},
    "runtime": {"framework", "help", "mcp_meta", "plugin", "program", "resource", "skill"},
    "computer_use": {"computer_use"},
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

    assert not (root / "web/browser").exists()
    assert not (root / "web/agent_browser.py").exists()
    assert not (root / "browser").exists()


def test_single_file_tools_are_modules_and_multi_file_tools_are_packages() -> None:
    root = Path(openprogram.__file__).resolve().parent / "programs/tools"

    assert (root / "files/read.py").is_file()
    assert not (root / "files/read").exists()
    application = root.parent / "applications/browser"
    assert (application / "tabs.py").is_file()
    assert not (application / "engine").exists()
    assert not (application / "web_use").exists()
