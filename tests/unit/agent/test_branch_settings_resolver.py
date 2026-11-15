"""One resolver answers what a setting is on a branch and where it came from.

Every branch, the selected one too, keeps its settings on its own record in
``branch_refs``; the top-level fields are the selected branch's projection.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.conversation import session_config
from openprogram.runtime.conversation.session_config import resolve_branch_settings
from openprogram.state.store.session.branch_config import (
    BranchConfigConflict,
    config_for_branch,
    write_branch_config,
)


def _row() -> dict:
    row = {"id": "s", "head_id": "a1", "active_branch_id": "branch_m",
           "branch_refs": {"branch_m": {"branch_id": "branch_m", "head_id": "a1", "config": {}},
                           "branch_t": {"branch_id": "branch_t", "head_id": "t1",
                                        "config": {"thinking_effort": "low", "agent_id": "night",
                                                   "permission_mode": "ask"}}}}
    write_branch_config(row, "s", None, {"thinking_effort": "high", "agent_id": "main",
                                         "web_search": True})
    return row


def test_the_selected_branch_reads_its_own_record_and_the_top_level_is_its_projection(monkeypatch):
    monkeypatch.setattr(session_config, "_agent_settings", lambda _agent: {"model": "p/m"})
    row = _row()
    assert row["thinking_effort"] == "high"
    assert row["branch_refs"]["branch_m"]["config"]["thinking_effort"] == "high"
    # The record decides, even where an old projection says otherwise.
    stale = {**row, "thinking_effort": "minimal"}
    assert config_for_branch(stale, "branch_m")["thinking_effort"] == "high"
    settings = resolve_branch_settings("s", row=row)
    assert settings.branch_id == "branch_m"
    assert (settings.values["thinking_effort"], settings.sources["thinking_effort"]) == ("high", "branch")
    assert settings.values["web_search"] is True and settings.config.web_search is True
    assert settings.sources["model_override"] == "agent"


def test_another_branch_resolves_its_own_settings_whatever_is_selected(monkeypatch):
    monkeypatch.setattr(session_config, "_agent_settings",
                        lambda agent: {"web_search": agent != "night", "model": f"p/{agent}"})
    settings = resolve_branch_settings("s", "branch_t", row=_row())
    assert settings.values["agent_id"] == "night" and settings.values["thinking_effort"] == "low"
    assert settings.config.permission_mode == "ask"
    # Unset on the branch: the branch's own Agent supplies it, as it is now.
    assert settings.config.web_search is None
    assert (settings.values["web_search"], settings.sources["web_search"]) == (False, "agent")
    assert (settings.values["model"], settings.sources["model"]) == ("p/night", "agent")
    assert settings.inherited["web_search"] is False


def test_a_selected_branch_recorded_before_its_settings_moved_reads_the_projection():
    row = {"active_branch_id": "branch_m", "thinking_effort": "medium",
           "branch_refs": {"branch_m": {"branch_id": "branch_m", "head_id": "a1"}}}
    assert resolve_branch_settings("s", row=row).values["thinking_effort"] == "medium"
    # A conversation with no branch recorded yet reads its own fields.
    assert resolve_branch_settings("s", row={"thinking_effort": "low"}).values["thinking_effort"] == "low"


def test_a_named_branch_without_a_record_is_gone():
    with pytest.raises(BranchConfigConflict) as refused:
        resolve_branch_settings("s", "branch_missing", row=_row())
    assert refused.value.code == "branch_gone"


def _stub_layers(monkeypatch, *, agent=None, project=None, glob=None):
    monkeypatch.setattr(session_config, "_agent_settings", lambda _agent: dict(agent or {}))
    monkeypatch.setattr(session_config, "project_defaults", lambda _sid: dict(project or {}))
    monkeypatch.setattr(session_config, "_global_settings", lambda name: (glob or {}).get(name))
    monkeypatch.setattr(session_config, "sandbox_override_from_config",
                        lambda _cfg, _sid: (project or {}).get("sandbox_enabled"))


def _bare_t() -> dict:
    row = _row()
    row["branch_refs"] = {**row["branch_refs"], "branch_t": {"branch_id": "branch_t", "head_id": "t1",
                                                           "config": {}}}
    return row


@pytest.mark.parametrize("name, layers, expected", [
    # Every layer offers a value: the first in FALLBACK_ORDER wins.
    ("model", "apg", ("agent", "A")),
    ("model", "pg", ("global", "G")),
    ("thinking_effort", "apg", ("agent", "high")),
    ("thinking_effort", "pg", ("project", "low")),
    ("thinking_effort", "g", ("global", "medium")),
    ("web_search", "apg", ("agent", False)),
    ("web_search", "pg", ("global", True)),
    ("permission_mode", "apg", ("project", "bypass")),
    ("permission_mode", "ag", ("global", "ask")),
    ("sandbox_enabled", "apg", ("project", True)),
    ("service_tier", "apg", ("agent", "priority")),
])
def test_each_unset_setting_falls_back_through_its_own_order(monkeypatch, name, layers, expected):
    agent = {"model": "A", "thinking_effort": "high", "web_search": False, "service_tier": "priority",
             "permission_mode": "never-from-agent"}
    project = {"thinking_effort": "low", "permission_mode": "bypass", "sandbox_enabled": True,
               "model": "never-from-project"}
    glob = {"model": "G", "thinking_effort": "medium", "web_search": True, "permission_mode": "ask",
            "sandbox_enabled": False, "service_tier": "auto"}
    _stub_layers(monkeypatch, agent=agent if "a" in layers else {}, project=project if "p" in layers else {},
                 glob=glob if "g" in layers else {})
    settings = resolve_branch_settings("s", "branch_t", row=_bare_t())
    assert (settings.sources[name], settings.values[name]) == expected
    assert settings.inherited[name] == expected[1]


def test_a_branch_value_wins_and_inherited_still_says_what_it_would_follow(monkeypatch):
    _stub_layers(monkeypatch, agent={"thinking_effort": "high"}, project={"permission_mode": "bypass"})
    settings = resolve_branch_settings("s", "branch_t", row=_row())
    assert (settings.values["permission_mode"], settings.sources["permission_mode"]) == ("ask", "branch")
    assert (settings.values["thinking_effort"], settings.sources["thinking_effort"]) == ("low", "branch")
    assert settings.inherited["permission_mode"] == "bypass"
    assert settings.inherited["thinking_effort"] == "high"
    # The run configuration keeps the branch's own values.
    unset = resolve_branch_settings("s", "branch_t", row=_bare_t())
    assert unset.config.permission_mode is None and unset.config.thinking_effort is None


def test_a_channel_branch_takes_its_agents_effort_before_the_projects(monkeypatch):
    _stub_layers(monkeypatch, agent={"thinking_effort": "high"}, project={"thinking_effort": "low"})
    assert session_config.fallback_value("s", "thinking_effort", agent="high") == ("high", "agent")
    assert session_config.fallback_value("s", "thinking_effort") == ("low", "project")
    assert resolve_branch_settings("s", "branch_t", row=_bare_t()).values["thinking_effort"] == "high"


def test_load_session_run_config_goes_through_the_resolver(monkeypatch):
    row = _row()
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db",
                        lambda: type("DB", (), {"get_session": staticmethod(lambda _sid: row)})())
    assert session_config.load_session_run_config("s").thinking_effort == "high"
    assert session_config.load_session_run_config("s", "branch_t").thinking_effort == "low"
