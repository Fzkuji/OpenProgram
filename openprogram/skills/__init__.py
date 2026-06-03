"""Skills module — five-source skill loader, remote discovery, watcher, tool.

See ``docs/design/integrations/skills-and-plugins.md`` section 2 for the design.
"""
from __future__ import annotations

from .loader import (
    Skill,
    list_skills,
    get_skill,
    register_plugin_skills,
    bundled_dir,
    user_dir,
    project_dir,
    remote_cache_dir,
)
from .discovery import IndexSkill, Index, pull
from .watcher import start_watcher, stop_watcher
from .tool import SkillTool, invoke

__all__ = [
    "Skill",
    "list_skills",
    "get_skill",
    "register_plugin_skills",
    "bundled_dir",
    "user_dir",
    "project_dir",
    "remote_cache_dir",
    "IndexSkill",
    "Index",
    "pull",
    "start_watcher",
    "stop_watcher",
    "SkillTool",
    "invoke",
]
