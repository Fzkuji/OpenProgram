"""Which saved Agents other agents may start, and how.

Two settings on an Agent decide it (``AgentSpec.delegation`` and
``AgentSpec.isolation``); an empty value means the default here, so no
stored ``agent.json`` is rewritten when these settings appear.

* ``delegation``: "on" lists the Agent in the ``agent`` tool's description
  and lets ``agent(agent=…)`` start it. The default is on for an Agent
  with tools and off for one without (``tools.mode == "none"``: Decision,
  Lightweight helper), whose use is a single model call, not a task. This
  is OpenCode's ``mode: primary | subagent | all`` reduced to one switch.
* ``isolation``: "worktree" or "none" fixes where it works when another
  agent starts it (Claude Code's agent frontmatter ``isolation``). The
  default rule: a background helper that can edit files works in its own
  worktree when the folder is a git repository; a foreground helper (the
  caller waits, so there is one writer) and a read-only one share the
  folder. A call's own ``isolation`` wins over both.

An Agent never gains permission by being delegated to: the helper runs
under its caller's permission mode and rules, and its own tool policy can
only narrow what it may use (Codex ``role.rs``: a role narrows, never
widens).
"""
from __future__ import annotations

from typing import Iterable, Optional

from openprogram.runtime.agent_registry import manager

# Tools that change files or run commands; an Agent with any of them is a
# writer for the default isolation rule.
WRITE_TOOLS = frozenset({
    "write", "edit", "apply_patch", "bash", "exec", "shell", "execute_code",
    "write_stdin", "notebook_edit",
})
ROSTER_MAX = 30


def delegation_enabled(spec: manager.AgentSpec) -> bool:
    if spec.delegation in ("on", "off"):
        return spec.delegation == "on"
    return (spec.tools or {}).get("mode") != "none"


def writes_files(spec: manager.AgentSpec) -> bool:
    """Whether the Agent's tool policy lets it change files or run commands."""
    tools = spec.tools or {}
    mode = tools.get("mode") or "automatic"
    if mode == "none":
        return False
    disabled = set(tools.get("disabled") or ())
    if mode == "selected":
        allowed = set(tools.get("allowed") or ())
        return bool((allowed - disabled) & WRITE_TOOLS)
    return not WRITE_TOOLS <= disabled


def isolation_for(
    spec: Optional[manager.AgentSpec], *, requested: str, background: bool,
) -> tuple[str, bool]:
    """``(isolation, from_default_rule)`` for a helper about to start.

    ``isolation`` is "worktree" or "" (shared folder). The second value is
    True when only the default rule asked for a worktree, in which case a
    folder that is not a git repository falls back to sharing instead of
    failing the call.
    """
    requested = (requested or "").strip().lower()
    if requested in ("worktree", "none"):
        return ("worktree" if requested == "worktree" else ""), False
    if spec is not None and spec.isolation in ("worktree", "none"):
        return ("worktree" if spec.isolation == "worktree" else ""), False
    if background and spec is not None and writes_files(spec):
        return "worktree", True
    return "", False


def delegate_model(spec: Optional[manager.AgentSpec], *, caller_agent: str) -> Optional[str]:
    """``provider/model`` a helper started as ``spec`` runs on, or None to
    run on its caller's model.

    A helper runs in its caller's conversation, whose model would otherwise
    win, so a saved Agent's own model is passed with the call: its own
    model, else for the utility tier (Explorer) the Lightweight helper's
    (``builtin_agents.utility_tier_model``). Running the caller's own Agent
    keeps the caller's model, as a model the person picked for this
    conversation should."""
    if spec is None or spec.id == caller_agent:
        return None
    provider, model_id = (spec.model.provider or "").strip(), (spec.model.id or "").strip()
    if provider and model_id:
        return f"{provider}/{model_id}"
    from openprogram.runtime.agent_registry.builtin_agents import utility_tier_model
    pair = utility_tier_model(spec)
    return f"{pair[0]}/{pair[1]}" if pair else None


def delegatable() -> list[manager.AgentSpec]:
    """The saved Agents, then the Agents defined as Markdown files in the
    project or home folder (``markdown_imports``) whose name no saved Agent
    already has: a saved Agent wins a name."""
    saved = manager.list_all()
    try:
        from openprogram.runtime.agent_registry.markdown_imports import list_imports
        imported = list_imports()
    except Exception:  # noqa: BLE001 - the saved Agents still serve
        imported = []
    names = {(spec.name or "").casefold() for spec in saved}
    return saved + [spec for spec in imported if spec.name.casefold() not in names]


def find(ref: str, specs: Optional[Iterable[manager.AgentSpec]] = None) -> Optional[manager.AgentSpec]:
    """The Agent ``ref`` names: its id, else its name (case-insensitive)."""
    needle = (ref or "").strip()
    if not needle:
        return None
    rows = list(specs) if specs is not None else delegatable()
    for spec in rows:
        if spec.id == needle:
            return spec
    folded = needle.casefold()
    named = [spec for spec in rows if (spec.name or "").casefold() == folded]
    return named[0] if len(named) == 1 else None


def roster(specs: Optional[Iterable[manager.AgentSpec]] = None) -> list[tuple[str, str, str]]:
    """``(id, name, description)`` of every Agent other agents may start,
    sorted by id so one Agent's edit moves no other line."""
    rows = list(specs) if specs is not None else delegatable()
    listed = sorted((s for s in rows if delegation_enabled(s)), key=lambda s: s.id)
    return [(s.id, s.name, s.description) for s in listed[:ROSTER_MAX]]
