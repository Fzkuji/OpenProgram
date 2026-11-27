"""What the extensions hand down to the runtime when they load.

``openprogram.runtime`` sits below the extensions and never imports them.
What it needs from them arrives through ports it owns, filled in here:

* ``runtime.extension_link`` — the skills a prompt lists (their loader and
  the ``<available_skills>`` renderer);
* ``runtime.commands.registry`` — the plugin and skill command sources,
  re-snapshotted on every reload. MCP servers push their prompts with
  ``sync_mcp_prompts`` when they connect (``mcp.registry``).

Each provider imports its extension module when it is called, never here,
so loading this module stays cheap and a test that replaces
``skills.loader.list_skills`` is what the runtime sees.
"""
from __future__ import annotations


class _Skills:
    """The skills source of ``runtime.extension_link``."""

    def list_skills(self):
        from openprogram.extensions.skills import loader

        return loader.list_skills()

    def load_skills(self, dirs):
        from openprogram.extensions.skills import loader

        return loader.load_skills(dirs)

    def format_skills_for_prompt(self, skills):
        from openprogram.extensions.skills import loader

        return loader.format_skills_for_prompt(skills)


def _sync_plugin_commands() -> None:
    from openprogram.extensions.plugins import commands

    commands.sync_into_registry()


def _sync_skill_commands() -> None:
    from openprogram.extensions.skills import commands

    commands.sync_into_registry()


def register() -> None:
    from openprogram.runtime import extension_link
    from openprogram.runtime.commands import registry as command_registry

    extension_link.register_skills(_Skills())
    command_registry.register_source("plugin", _sync_plugin_commands)
    command_registry.register_source("skill", _sync_skill_commands)


__all__ = ["register"]
