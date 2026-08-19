"""One skill registry, one prompt renderer.

Skills used to be loaded twice — a flat two-directory scanner feeding the
system prompt and a multi-source loader feeding everything else — with two
different renderers and opposite conflict rules. These tests pin the
merged behaviour: which sources are read, who wins a name clash, and that
every path into the model produces the same block.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.skills import loader as L


def _write_skill(root: Path, rel: str, description: str, body: str = "body\n") -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(
        f"---\nname: {rel.split('/')[-1]}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return md


@pytest.fixture()
def sources(tmp_path, monkeypatch):
    """Point all filesystem source roots at throwaway directories."""
    roots = {
        name: tmp_path / name
        for name in ("remote_cache", "user", "project")
    }
    for p in roots.values():
        p.mkdir()
    monkeypatch.setattr(L, "remote_cache_dir", lambda: roots["remote_cache"])
    monkeypatch.setattr(L, "user_dir", lambda: roots["user"])
    monkeypatch.setattr(L, "project_dir", lambda cwd=None: roots["project"])
    monkeypatch.setattr(L, "_PLUGIN_SKILL_DIRS", {})
    return roots


def test_one_entry_per_name_across_sources(sources):
    for root in ("remote_cache", "user", "project"):
        _write_skill(sources[root], "deploy", f"from {root}")
    skills = L.list_skills()
    assert [s.name for s in skills] == ["deploy"]
    assert L.format_skills_for_prompt(skills).count("<skill>") == 1


def test_only_external_skill_sources_are_registered(sources):
    assert [source for source, _ in L._source_dirs()] == [
        "remote-cache",
        "user",
        "project",
    ]
    assert "bundled" not in L.SOURCES


@pytest.mark.parametrize(
    "present, winner",
    [
        (("remote_cache", "user"), "user"),
        (("user", "project"), "project"),
        (("remote_cache", "user", "project"), "project"),
    ],
)
def test_the_more_local_source_wins(sources, present, winner):
    """What the user wrote beats what was installed for them, and the more
    specific location beats the more general one."""
    for root in present:
        _write_skill(sources[root], "deploy", f"from {root}")
    (skill,) = L.list_skills()
    assert skill.description == f"from {winner}"


def test_plugin_loses_to_user_and_project(tmp_path, sources, monkeypatch):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_skill(plugin_root, "deploy", "from plugin")
    monkeypatch.setattr(L, "_PLUGIN_SKILL_DIRS", {"p": plugin_root})

    assert L.list_skills()[0].description == "from plugin"
    _write_skill(sources["user"], "deploy", "from user")
    assert L.list_skills()[0].description == "from user"


def test_plugin_source_is_discovered(tmp_path, sources, monkeypatch):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_skill(plugin_root, "review", "from plugin")
    monkeypatch.setattr(L, "_PLUGIN_SKILL_DIRS", {"example": plugin_root})

    (skill,) = L.list_skills()
    assert skill.source == "plugin:example"
    assert skill.description == "from plugin"


def test_hierarchical_names_come_from_the_directory(sources):
    _write_skill(sources["remote_cache"], "anthropic-skills/docx", "word files")
    _write_skill(sources["project"], "docx", "our own")
    names = sorted(s.name for s in L.list_skills())
    assert names == ["anthropic-skills/docx", "docx"]


def test_listing_carries_name_description_and_path(sources):
    md = _write_skill(sources["project"], "deploy", "ship it")
    block = L.format_skills_for_prompt(L.list_skills())
    assert "<name>deploy</name>" in block
    assert "<description>ship it</description>" in block
    assert f"<location>{md}</location>" in block


def test_listing_caps_the_description_but_never_the_skill_count(sources):
    long_desc = "x" * (L.MAX_LISTING_DESC_CHARS + 500)
    for i in range(40):
        _write_skill(sources["project"], f"skill-{i:02d}", long_desc)
    block = L.format_skills_for_prompt(L.list_skills())
    assert block.count("<skill>") == 40, "every skill is listed; no silent cutoff"
    assert "…" in block
    assert long_desc not in block


def test_listing_escapes_xml(sources):
    _write_skill(sources["project"], "deploy", "handles <tags> & \"quotes\"")
    block = L.format_skills_for_prompt(L.list_skills())
    assert "&lt;tags&gt;" in block
    assert "&amp;" in block


def test_empty_input_renders_nothing():
    assert L.format_skills_for_prompt([]) == ""


def test_explicit_dirs_use_the_same_parser_and_rule(tmp_path):
    low, high = tmp_path / "low", tmp_path / "high"
    _write_skill(low, "deploy", "from low")
    _write_skill(high, "deploy", "from high")
    (skill,) = L.load_skills([low, high, tmp_path / "missing"])
    assert skill.description == "from high"
    assert skill.body.strip() == "body"


def test_chat_and_exec_render_the_same_block(sources, monkeypatch):
    """context.components (chat turns) and Runtime._skills_block (inside an
    agentic function) go through the one loader and the one renderer."""
    _write_skill(sources["remote_cache"], "distill", "turn a session into a skill")
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.context import components

    rt = Runtime.__new__(Runtime)
    rt._skills_config = True
    rt._skills_cache_key = None
    rt._skills_prompt_block = ""

    assert components._build_skills(None) == rt._skills_block().strip("\n")
    assert "<name>distill</name>" in rt._skills_block()


def test_disabled_skills_drop_out_of_the_chat_listing(sources):
    _write_skill(sources["remote_cache"], "distill", "turn a session into a skill")
    _write_skill(sources["project"], "deploy", "ship it")
    from openprogram.context import components

    block = components._build_skills({"skills": {"disabled": ["distill"]}})
    assert "<name>deploy</name>" in block
    assert "distill" not in block


def test_skill_tool_is_registered_and_deferred():
    """The load verb exists as a tool, and costs one catalog line per turn
    rather than a schema."""
    from openprogram.programs import agent_tools, get_agent_tool, split_tools_for_dispatch

    tool = get_agent_tool("skill")
    assert tool is not None
    assert getattr(tool, "_defer", False) is True
    _loaded, catalog = split_tools_for_dispatch(list(agent_tools(toolset="default")))
    assert "skill" in [name for name, _desc in catalog]


def _run_skill_tool(**args) -> str:
    import asyncio

    from openprogram.programs import get_agent_tool

    result = asyncio.run(get_agent_tool("skill").execute("call-test", args, None, None))
    return "".join(getattr(c, "text", "") for c in result.content)


def test_skill_tool_loads_a_body(sources):
    md = _write_skill(sources["project"], "deploy", "ship it", body="step one\n")
    out = _run_skill_tool(name="deploy")
    assert "step one" in out
    assert str(md.parent) in out


def test_skill_tool_resolves_a_short_name(sources):
    _write_skill(sources["remote_cache"], "anthropic-skills/docx", "word", body="open it\n")
    assert "open it" in _run_skill_tool(name="docx")


def test_skill_tool_reports_a_bad_name(sources):
    _write_skill(sources["project"], "deploy", "ship it")
    assert "no skill named" in _run_skill_tool(name="nope")
    assert "pass a skill name" in _run_skill_tool(name="")
