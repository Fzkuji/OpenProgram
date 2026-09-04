from __future__ import annotations

import json
from pathlib import Path

from scripts.docs_site import checklang
from scripts.docs_site.nav import build_tabs, discover


ROOT = Path(__file__).resolve().parents[3]


def test_internal_plans_are_not_part_of_the_public_docs_build() -> None:
    pages = discover(ROOT / "docs")
    paths = {page.rel.as_posix() for page in pages}

    assert not any(path.startswith("superpowers/") for path in paths)
    assert any(path.startswith("reference/design/plans/") for path in paths)
    assert "reference/design/repository-structure.html" in paths
    assert "reference/design/repository-structure-implementation.md" in paths


def test_language_check_uses_the_same_public_docs_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    internal = tmp_path / "superpowers" / "plans" / "internal.md"
    internal.parent.mkdir(parents=True)
    internal.write_text("# 内部计划\n", encoding="utf-8")
    public = tmp_path / "start" / "public.md"
    public.parent.mkdir(parents=True)
    public.write_text("# Public documentation\n", encoding="utf-8")
    monkeypatch.setattr(checklang, "DOCS", tmp_path)

    assert checklang.main() == 0


def test_ui_design_navigation_is_grouped_without_losing_pages() -> None:
    pages = discover(ROOT / "docs")
    design = next(tab for tab in build_tabs(ROOT / "docs", pages) if tab.key == "design")
    assert design.landing == Path("reference/design/README.html")
    ui_sections = {
        section.title: {page.rel.as_posix() for page in section.pages}
        for section in design.sections
        if section.title.startswith("UI ·")
    }

    assert set(ui_sections) == {
        "UI · Foundations",
        "UI · Chat and composer",
        "UI · Browser and tabs",
        "UI · Settings and catalog",
        "UI · Workspace and sidebar",
    }

    expected = {
        page.rel.as_posix()
        for page in pages
        if page.rel.parent.as_posix() == "reference/design/ui"
    }
    grouped = set().union(*ui_sections.values())
    assert grouped == expected


def test_editorial_navigation_does_not_list_a_page_twice() -> None:
    pages = discover(ROOT / "docs")
    for tab in build_tabs(ROOT / "docs", pages):
        paths = [page.rel.as_posix() for section in tab.sections for page in section.pages]
        assert len(paths) == len(set(paths)), tab.key


def test_gui_agent_design_keeps_the_capability_loop_and_context_contract() -> None:
    for suffix, title in (
        ("", "GUI Agent autonomous capability loop"),
        (".zh", "GUI Agent 自主能力调用循环"),
    ):
        design_dir = ROOT / "docs" / "reference" / "design" / "ui"
        source = design_dir / f"gui-agent{suffix}.archify.json"
        artifact = design_dir / f"gui-agent{suffix}.html"
        specification = json.loads(source.read_text(encoding="utf-8"))

        node_ids = {node["id"] for node in specification["nodes"]}
        edge_ids = {edge["id"] for edge in specification["edges"]}
        assert {
            "computer_use",
            "browser_use",
            "vm_use",
            "record_result",
            "terminal_decision",
            "normalized_result",
            "runtime_stop",
        } <= node_ids
        assert {"next_iteration", "decision_terminal", "runtime_boundary"} <= edge_ids
        assert title in artifact.read_text(encoding="utf-8")

    gui_page = next(
        page
        for page in discover(ROOT / "docs")
        if page.rel.as_posix() == "reference/design/ui/gui-agent.html"
    )
    assert gui_page.zh_src == (
        ROOT / "docs" / "reference" / "design" / "ui" / "gui-agent.zh.html"
    )
    assert gui_page.zh_out == Path("reference/design/ui/gui-agent.zh.html")
