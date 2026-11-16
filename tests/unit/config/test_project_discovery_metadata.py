from __future__ import annotations

import tomllib
from pathlib import Path


def test_package_metadata_exposes_discovery_links_and_keywords() -> None:
    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert {
        "Issues": "https://github.com/Fzkuji/OpenProgram/issues",
        "Changelog": "https://github.com/Fzkuji/OpenProgram/releases",
        "Discussions": "https://github.com/Fzkuji/OpenProgram/discussions",
        "Paper": "https://arxiv.org/abs/2606.15874",
    }.items() <= project["urls"].items()
    assert {
        "agent-framework",
        "workflow-automation",
        "autonomous-agents",
    } <= set(project["keywords"])
