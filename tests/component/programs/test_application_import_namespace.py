from __future__ import annotations

import importlib
import sys
from pathlib import Path


APPLICATIONS = (
    Path(__file__).parents[3] / "openprogram" / "programs" / "applications"
)
REMOVED_NAMESPACE = "openprogram.programs.agentic_functions"


def test_gui_application_uses_current_agentic_namespace(monkeypatch):
    monkeypatch.syspath_prepend(str(APPLICATIONS / "gui_harness"))
    sys.modules.pop("gui_harness.utils", None)

    utils = importlib.import_module("gui_harness.utils")

    assert utils.parse_json('{"ok": true}') == {"ok": True}


def test_application_production_code_has_no_removed_agentic_namespace():
    offenders = []
    for package in ("gui_harness", "research_harness", "wiki_agent_harness"):
        source_root = APPLICATIONS / package / package
        offenders.extend(
            str(path.relative_to(APPLICATIONS))
            for path in source_root.rglob("*.py")
            if REMOVED_NAMESPACE in path.read_text(encoding="utf-8")
        )

    assert offenders == []
