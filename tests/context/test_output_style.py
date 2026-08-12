"""Output styles: discovery, config resolution, and system-prompt effect."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from openprogram.context import output_style as os_mod
from openprogram.context.components import build_system_prompt


@pytest.fixture
def _no_user_styles(tmp_path):
    """Point both discovery roots at empty dirs so a real ``~/.openprogram``
    or a repo-level ``output-styles/`` cannot leak into the assertions."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with patch.object(os_mod, "user_dir", return_value=empty):
        yield tmp_path


# discovery


def test_builtin_styles_are_listed(_no_user_styles):
    styles = os_mod.list_styles(cwd=_no_user_styles)
    for name in ("default", "concise", "explanatory", "direct", "detailed"):
        assert name in styles


def test_default_style_is_empty(_no_user_styles):
    assert os_mod.list_styles(cwd=_no_user_styles)["default"] == ""


def test_user_dir_md_file_is_discovered(tmp_path):
    user = tmp_path / "user-styles"
    user.mkdir()
    (user / "lab-notes.md").write_text("Write like a lab notebook.", encoding="utf-8")
    with patch.object(os_mod, "user_dir", return_value=user):
        styles = os_mod.list_styles(cwd=tmp_path)
    assert styles["lab-notes"] == "Write like a lab notebook."


def test_project_dir_overrides_user_and_builtin(tmp_path):
    user = tmp_path / "user-styles"
    user.mkdir()
    (user / "concise.md").write_text("USER VERSION", encoding="utf-8")
    project = tmp_path / "output-styles"
    project.mkdir()
    (project / "concise.md").write_text("PROJECT VERSION", encoding="utf-8")
    with patch.object(os_mod, "user_dir", return_value=user):
        styles = os_mod.list_styles(cwd=tmp_path)
    assert styles["concise"] == "PROJECT VERSION"


def test_frontmatter_is_stripped(tmp_path):
    project = tmp_path / "output-styles"
    project.mkdir()
    (project / "fm.md").write_text(
        "---\ndescription: a style\n---\nBODY ONLY", encoding="utf-8",
    )
    styles = os_mod.list_styles(cwd=tmp_path)
    assert styles["fm"] == "BODY ONLY"


def test_empty_file_is_not_registered(tmp_path):
    project = tmp_path / "output-styles"
    project.mkdir()
    (project / "blank.md").write_text("   \n", encoding="utf-8")
    assert "blank" not in os_mod.list_styles(cwd=tmp_path)


# active style resolution


def test_active_style_defaults_when_unset():
    with patch("openprogram.setup._read_config", return_value={}):
        assert os_mod.get_active_style() == "default"


def test_active_style_reads_config():
    cfg = {"agent": {"output_style": "concise"}}
    with patch("openprogram.setup._read_config", return_value=cfg):
        assert os_mod.get_active_style() == "concise"


def test_blank_config_value_falls_back_to_default():
    cfg = {"agent": {"output_style": "  "}}
    with patch("openprogram.setup._read_config", return_value=cfg):
        assert os_mod.get_active_style() == "default"


def test_unknown_style_resolves_to_empty_text(_no_user_styles):
    assert os_mod.style_text("no-such-style", cwd=_no_user_styles) == ""


def test_style_text_default_is_empty(_no_user_styles):
    assert os_mod.style_text("default", cwd=_no_user_styles) == ""


# system-prompt effect


def _prompt_for(style: str) -> str:
    with patch.object(os_mod, "get_active_style", return_value=style):
        return build_system_prompt({"system_prompt": "INLINE_MARKER"})


def test_default_appends_nothing():
    baseline = _prompt_for("default")
    assert "Output style" not in baseline


def test_selected_style_is_appended():
    prompt = _prompt_for("concise")
    assert os_mod.BUILTIN_STYLES["concise"] in prompt


def test_style_adds_only_its_own_text():
    baseline = _prompt_for("default")
    styled = _prompt_for("concise")
    added = len(styled) - len(baseline)
    # The block plus the "\n\n" join separator.
    assert added == len(os_mod.BUILTIN_STYLES["concise"]) + 2


def test_style_precedes_the_agent_inline_prompt():
    """The agent's own instructions come after the style, so they win on
    a conflict."""
    prompt = _prompt_for("direct")
    assert prompt.index("Output style: direct") < prompt.index("INLINE_MARKER")


def test_broken_style_lookup_does_not_break_the_prompt():
    with patch.object(os_mod, "style_text", side_effect=RuntimeError("boom")):
        prompt = build_system_prompt({"system_prompt": "INLINE_MARKER"})
    assert "INLINE_MARKER" in prompt


# config schema wiring


def test_output_style_is_a_declared_setting():
    from openprogram.config_schema import _BY_KEY
    spec = _BY_KEY["agent.output_style"]
    assert spec.widget == "enum"
    assert spec.default == "default"
    assert "default" in spec.choices()
