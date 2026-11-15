"""What a plugin can contain, and how entries are exposed, read from the live registries.

``describe()`` is the machine form the ``plugin`` tool, ``GET
/api/plugins/inspect`` and ``openprogram plugins inspect --json`` return;
``guide()`` is the same facts as a short authoring guide. A plugin is
declarative: its manifest names skills, slash commands, agents, hooks, MCP
servers and provider directories, and the host mounts them. Code that adds
tools or a UI is an application. The events (for hooks) are listed from
the event registry, and every registered entry with its exposure tier.
"""
from __future__ import annotations

from typing import Any

#: Manifest ``entrypoints`` keys a plugin may use (also accepted at the top level).
MANIFEST_KEYS: dict[str, str] = {
    "skills": "A directory of SKILL.md skills.",
    "commands": "Slash commands: {name: {description, prompt}} or a directory of .md files.",
    "agents": "A list of {name, description, prompt, tools} or a directory of .md agents.",
    "hooks": "Shell commands on events: {event: [{command, timeout}]} or Claude Code's "
             "hooks.json shape ({hooks: {PreToolUse: [{matcher, hooks: [{type: command, "
             "command}]}]}}), inline or as a .json path.",
    "mcpServers": "{name: {command, args, env} | {url}} inline, or a .mcp.json path.",
    "providers": "A directory whose children each hold a provider.json.",
    "requires": "Top-level list of plugins that must load first.",
    "options": "Top-level JSON schema of options saved for this plugin.",
}

#: Keys that name code; a manifest with any of them is refused.
REFUSED_KEYS: dict[str, str] = {
    "python / module / setup / main": "Setup code run in the worker.",
    "tools": "A module whose PROGRAM_ENTRIES would become tools.",
    "client / web / sidebar": "A Web module, page or sidebar entry of its own.",
    "hooks as 'module:name'": "A Python mapping of event handlers.",
    "__init__.py beside plugin.yaml": "A Hermes Python plugin.",
}

#: The exposure tiers of a registered entry (program-model.md §5).
TIERS: dict[str, str] = {
    "resident": "Its schema is in every tools array.",
    "deferred": "Only its name is listed; tool_search loads the schema when needed. "
                "An application's entries are deferred and become resident for a "
                "session while the application is open in one of its windows.",
    "hidden": "Callable from Python, never shown to the model.",
}

APPLICATION_HINT = (
    "Code that adds tools or a UI is an application: a directory with application.json "
    "(UI and an optional backend.py) or a Python package with PROGRAM_ENTRIES, installed "
    "with `openprogram programs install <dir>`."
)


def entries() -> list[dict[str, Any]]:
    """Every registered entry with its owner, tier and whether it needs approval."""
    from openprogram.programs._host.runtime import all_tools, tool_owner, tool_tier
    rows = []
    for tool in sorted(all_tools(), key=lambda t: t.name):
        rows.append({"name": tool.name, "owner": tool_owner(tool) or "tool",
                     "tier": tool_tier(tool).value,
                     "needs_approval": getattr(tool, "_requires_approval", None) not in (None, False)})
    return rows


def describe() -> dict[str, Any]:
    from openprogram.runtime.events.registry import EVENTS
    from openprogram.extensions.plugins.hooks import CLAUDE_EVENTS
    return {
        "plugin": "A declarative package; the host mounts its manifest contents through a "
                  "ProgramContext owned by plugin:<name> and disposes it on disable or reload.",
        "manifest": MANIFEST_KEYS,
        "refused": REFUSED_KEYS,
        "applications": APPLICATION_HINT,
        "events": [{"name": name, "kind": spec.kind, "payload": spec.payload_doc,
                    "hooks": spec.kind != "transform"}
                   for name, spec in sorted(EVENTS.items())],
        "claude_hook_events": CLAUDE_EVENTS,
        "tiers": TIERS,
        "entries": entries(),
    }


def guide() -> str:
    facts = describe()
    lines = [
        "OpenProgram plugin",
        "",
        "A directory with a manifest (.claude-plugin/plugin.json, .codex-plugin/plugin.json,",
        "plugin.json, package.json#openprogram|opencode|hermes, plugin.yaml or",
        "[tool.openprogram.plugin] in pyproject.toml), for example:",
        '  {"name": "house-style", "skills": "./skills", "commands": "./commands",',
        '   "hooks": "./hooks/hooks.json", "mcpServers": "./.mcp.json"}',
        "Install a local directory with `openprogram plugins install path <dir>`, then",
        "enable and reload it. Everything it adds is removed on disable or reload.",
        "",
        "Manifest keys:",
        *[f"  {key}: {doc}" for key, doc in facts["manifest"].items()],
        "",
        "Refused (code):",
        *[f"  {key}: {doc}" for key, doc in facts["refused"].items()],
        f"  {facts['applications']}",
        "",
        "Hook events (a gate hook denies with exit 2 and stderr as the reason):",
        *[f"  {row['name']} [{row['kind']}]: {row['payload']}"
          for row in facts["events"] if row["hooks"]],
        "Claude Code hook events: " + ", ".join(f"{k} = {v}" for k, v in facts["claude_hook_events"].items()),
        "",
        "Exposure tiers of registered entries:",
        *[f"  {key}: {doc}" for key, doc in facts["tiers"].items()],
    ]
    return "\n".join(lines)
