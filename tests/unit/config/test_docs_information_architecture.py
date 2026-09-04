from __future__ import annotations

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


def test_gui_agent_design_covers_flow_boundaries_and_file_ownership() -> None:
    source = (ROOT / "docs/reference/design/ui/gui-agent.html").read_text(
        encoding="utf-8"
    )

    for section_id in (
        "current-api",
        "target-api",
        "architecture",
        "invocation",
        "comparison",
        "runner",
        "lifecycle",
        "results",
        "boundaries",
        "ownership",
        "migration",
        "evidence",
    ):
        assert f'id="{section_id}"' in source
    for path in (
        "openprogram/programs/gui_harness_bridge.py",
        "openprogram/agent/process_runner.py",
        "openprogram/agent/surface_context.py",
        "openprogram/programs/workflow/browser/__init__.py",
        "apps/server/openprogram_server/_webui/ws_actions/webtab.py",
        "apps/web/lib/desktop-bridge.ts",
        "apps/desktop/main.js",
    ):
        assert path in source
    assert "padding: clamp(20px,3vw,36px)" in source
    assert "@media (max-width:1120px)" in source
    for contract_term in (
        "browser_control",
        "run_browser_task",
        "run_computer_task",
        "browser_agent",
        "web_use",
        "must not call a decorated task function",
    ):
        assert contract_term in source


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
    artifact = ROOT / "docs/reference/design/ui/gui-agent.html"
    source = artifact.read_text(encoding="utf-8")

    for required in (
        "computer_use",
        "browser_use",
        "vm_use",
        "capability history",
        "Background application behavior",
        "adapters/mac_indicator.py",
    ):
        assert required in source
