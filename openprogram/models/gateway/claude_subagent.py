"""Claude Code's Explore and Plan subagents moved onto the model set for its subagents.

After yetone/magpie ``internal/gateway/claude_subagent.go`` and
``internal/agent/claude_subagent.go`` (MIT License, Copyright (c) yetone).
Claude Code's built-in Explore and Plan declare model ``inherit``, which
Claude Code reads before ``CLAUDE_CODE_SUBAGENT_MODEL``, so they ran on the
lead's model whatever was set there (magpie #7176), while general-purpose,
which names none, followed the variable. A custom agent has a prompt of its
own, and its ``model:`` line stays Claude Code's to apply.
``CLAUDE_CODE_SUBAGENT_MODEL_FORCE`` would move those too, so it is not
written. The gateway moves only a tool turn of Explore or Plan (the
``x-claude-code-agent-id`` header and the opening of their system prompt),
and only when Claude Code's settings send it to this gateway and name one of
OpenProgram's models for subagents.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

#: The openings of the system prompts of Claude Code's built-in subagents
#: whose definitions say model ``inherit``: Explore and Plan.
INHERIT_PROMPTS = (
    "You are a file search specialist for Claude Code",
    "You are a software architect and planning specialist for Claude Code",
)


def _set_in(path: Path) -> str:
    """The subagent model of the settings at ``path`` when they send Claude
    Code to this gateway and it names a model OpenProgram serves; a value of
    the user's own (``inherit``, a tier alias) is Claude Code's to read
    (magpie ``claudeSubagentIn``)."""
    from .agents.lifecycle import on_gateway
    from .conversion import enabled_model_refs
    from .edit import ConfigText, ConfigUnreadable, load
    try:
        doc = load("json", ConfigText.read(path).text, str(path))
    except (ConfigUnreadable, OSError):
        return ""
    env = doc.get("env") if isinstance(doc.get("env"), dict) else {}
    value = env.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if not isinstance(value, str) or not value.strip():
        return ""
    if not on_gateway(str(env.get("ANTHROPIC_BASE_URL") or "")):
        return ""
    model = value.strip().removesuffix("[1m]")
    try:
        served = {row["id"] for row in enabled_model_refs()}
    except Exception:  # noqa: BLE001 - no catalog: nothing to move to
        return ""
    return model if model in served else ""


def subagent_model(headers: Mapping[str, str] | None = None) -> str:
    """The model set for Claude Code's subagents (magpie ``ClaudeSubagent``),
    read where the turn's Claude Code was set: an OpenProgram chat's launch
    file when the request names one (``x-openprogram-launch``), and only that
    file, since it lies over the user's settings.json; else the user's
    settings.json."""
    from .agents.claude_code import LAUNCH_HEADER, config_path, launch_path
    launch = ((headers or {}).get(LAUNCH_HEADER) or "").strip()
    if launch:
        try:
            return _set_in(launch_path(launch))
        except ValueError:
            return ""
    return _set_in(config_path())


def _system_texts(body: Mapping[str, Any]) -> list[str]:
    system = body.get("system")
    if isinstance(system, str):
        return [system]
    if isinstance(system, list):
        return [str(block.get("text") or "") for block in system if isinstance(block, dict)]
    return []


def inherits(body: Mapping[str, Any]) -> bool:
    """``body`` is a tool turn whose system prompt is Explore's or Plan's (magpie ``claudeInherits``)."""
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    return any(prompt in text for text in _system_texts(body) for prompt in INHERIT_PROMPTS)


def move(body: Any, headers: Mapping[str, str]) -> Any:
    """``body`` with its model moved to the subagents' model, when it is an
    Explore or Plan turn and one is set that isn't the one asked (magpie
    ``claudeSubagentPick``). The cheap checks come first: a lead's turn
    costs a header read, a subagent's a scan for the two prompts before the
    settings are read."""
    if not isinstance(body, dict):
        return body
    if not (headers.get("x-claude-code-agent-id") or "").strip() or not inherits(body):
        return body
    target = subagent_model(headers)
    if not target or target == body.get("model"):
        return body
    return {**body, "model": target}
