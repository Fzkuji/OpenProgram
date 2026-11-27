"""Registry-based context assembly (registry of ContextComponents).

Design: docs/design/context/composition.md. Instead of hardcoding the
system-prompt blocks in one function, each piece of context is a registered
``ContextComponent`` declaring its layer (L0/L1/L2), in-layer order, an
appearance condition, and a builder. The assembler collects the registered
components for a layer, sorts by order, drops the ones whose condition is
false, builds the rest, and joins them.

This file is step 1 of the refactor: it reproduces the existing 5 system-prompt
blocks (identity / AGENTS.md / inline prompt / skills / memory) as L0/L1
components and assembles them **byte-for-byte identical** to the old
``system_prompt._compose``. Call tree (L1), situation (L2), and the missing
components are added in later steps.

Layers (see design doc §一):
    L0  system-level  — always present, constant for the whole session
    L1  session/project-level — carried forward; project files + call tree
    L2  task-level — this-call only (situation + current input/output)
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from openprogram.state.memory.policy import MemoryPolicyError

_log = logging.getLogger(__name__)

Layer = Literal["L0", "L1", "L2"]

# Per-call context: builders that need turn-specific info (e.g. channel) read
# these instead of requiring a signature change.  Set by callers of assemble().
_channel_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_channel_var", default="",
)
# Only a conversation turn sets this (loop_runner): "" for a chat read in the
# App, a channel name when the reply also goes there, None for everything else
# that builds a prompt (Programs, verifiers, sub-agents, accounting).
_chat_delivery_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_chat_delivery_var", default=None,
)
# Session-dag.md §7: the tool-runtime block, the deferred-tool catalog and the
# plan-mode reminder used to be hand-appended by the dispatcher after calling
# the assembler. They are components like everything else; the per-call inputs
# they need (the resolved tool list, extra working dirs, plan-mode flag) ride
# these contextvars so builder signatures stay single-arg.
_tools_var: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "_tools_var", default=None,
)
_working_dirs_var: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "_working_dirs_var", default=None,
)
_plan_mode_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_plan_mode_var", default=False,
)
# A conversation's own turn gets the deferred-tool catalog beside its message
# (``turn_context`` section ``deferred_catalog``), not in its prompt.
_catalog_beside_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_catalog_beside_var", default=False,
)
# L2 builders describe this one call; what they read about it (the turn's
# surface context, whether its browser tools are on …) rides this var.
_turn_var: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_turn_var", default=None,
)


def turn_inputs() -> dict:
    """The inputs of the call whose L2 sections are being built."""
    return dict(_turn_var.get() or {})


def _attr(obj: Any, name: str, default: Any) -> Any:
    """Read ``name`` off an AgentSpec object or a plain dict (webui passes
    profile dicts). Mirrors system_prompt._attr."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True)
class ContextComponent:
    """One registered piece of context.

    name       identifier (for debugging / dedup).
    layer      which layer it belongs to (L0/L1/L2).
    order      in-layer sort key — smaller = earlier = more stable (cache).
    condition  returns True when this component should appear this turn.
               Defaults to always-on.
    build      produces the component's text; "" / None ⇒ contributes nothing.
    """
    name: str
    layer: Layer
    order: int
    build: Callable[[Any], Optional[str]]
    condition: Callable[[Any], bool] = field(default=lambda ctx: True)


# Three registries — one per layer. Populated at import time below.
_REGISTRY: dict[Layer, list[ContextComponent]] = {"L0": [], "L1": [], "L2": []}


def register(component: ContextComponent) -> None:
    """Register a component into its layer. Idempotent on name within a layer
    (re-registering the same name replaces the old one — handy for tests)."""
    bucket = _REGISTRY[component.layer]
    for i, existing in enumerate(bucket):
        if existing.name == component.name:
            bucket[i] = component
            return
    bucket.append(component)


def unregister(name: str, layer: Optional[Layer] = None) -> None:
    """Remove a component by name, from one layer or every layer — the
    inverse of :func:`register`, used when a plugin unloads."""
    for key in ([layer] if layer else list(_REGISTRY)):
        _REGISTRY[key][:] = [c for c in _REGISTRY[key] if c.name != name]


def assemble(
    agent: Any,
    layers: list[Layer],
    *,
    channel: str = "",
    tools: Optional[list] = None,
    additional_working_dirs: Optional[list] = None,
    plan_mode: bool = False,
) -> list[str]:
    """Collect → sort by order → filter by condition → build → drop empties.

    Returns the list of non-empty block strings, in order, across the given
    layers (layers are concatenated in the order requested; within each layer
    components are ordered by ``order``).

    ``channel`` / ``tools`` / ``additional_working_dirs`` / ``plan_mode`` are
    exposed to builders via contextvars so existing single-arg builders need
    no signature change."""
    return [text for _name, text in assemble_sections(
        agent, layers, channel=channel, tools=tools,
        additional_working_dirs=additional_working_dirs, plan_mode=plan_mode,
    )]


def assemble_sections(
    agent: Any,
    layers: list[Layer],
    *,
    channel: str = "",
    tools: Optional[list] = None,
    additional_working_dirs: Optional[list] = None,
    plan_mode: bool = False,
    chat_delivery: Optional[str] = None,
) -> list[tuple[str, str]]:
    """:func:`assemble`, with each block paired with its component name."""
    token = _channel_var.set(channel)
    t_delivery = _chat_delivery_var.set(chat_delivery)
    t_tools = _tools_var.set(tools)
    t_dirs = _working_dirs_var.set(additional_working_dirs)
    t_plan = _plan_mode_var.set(plan_mode)
    try:
        parts: list[tuple[str, str]] = []
        for layer in layers:
            for comp in sorted(_REGISTRY[layer], key=lambda c: c.order):
                try:
                    if not comp.condition(agent):
                        continue
                    text = comp.build(agent)
                except MemoryPolicyError:
                    raise
                except Exception:
                    continue
                if text and str(text).strip():
                    parts.append((comp.name, str(text)))
        return parts
    finally:
        _plan_mode_var.reset(t_plan)
        _working_dirs_var.reset(t_dirs)
        _tools_var.reset(t_tools)
        _chat_delivery_var.reset(t_delivery)
        _channel_var.reset(token)


def build_turn_sections(
    agent: Any,
    turn: dict,
    *,
    channel: str = "",
    tools: Optional[list] = None,
) -> str:
    """The L2 (this-call) sections, joined. Builders read ``turn`` through
    :func:`turn_inputs`; the caller appends the text to the call's prompt."""
    token = _turn_var.set(dict(turn))
    try:
        return _SEPARATOR.join(assemble(agent, ["L2"], channel=channel, tools=tools))
    finally:
        _turn_var.reset(token)


# System prompt: same fence as the legacy _compose

_FENCE_OPEN = ""
_FENCE_CLOSE = ""


_SEPARATOR = "\n\n"
#: Prompts built recently, by text, with the component behind each block:
#: the turn records these lengths next to the prompt so /context can show
#: the system prompt by component.
_RECENT_SECTIONS: dict[str, list[dict]] = {}
_RECENT_SECTIONS_MAX = 32


def _remember_sections(text: str, sections: list[tuple[str, str]]) -> None:
    _RECENT_SECTIONS.pop(text, None)
    _RECENT_SECTIONS[text] = [
        {"name": name, "chars": len(block)} for name, block in sections
    ]
    while len(_RECENT_SECTIONS) > _RECENT_SECTIONS_MAX:
        _RECENT_SECTIONS.pop(next(iter(_RECENT_SECTIONS)))


def prompt_sections(text: str) -> Optional[list[dict]]:
    """``[{"name", "chars"}]`` for a prompt :func:`build_system_prompt` made
    in this process, in order; None for any other text."""
    found = _RECENT_SECTIONS.get(text)
    return [dict(row) for row in found] if found else None


#: Prompts a turn composed from a built prompt and text it appended, by
#: text, as ``(name, part)`` pairs that join back to the text.
_RECENT_PARTS: dict[str, list[tuple[str, str]]] = {}


def prompt_parts(text: str) -> Optional[list[tuple[str, str]]]:
    """``text`` cut into the named parts it was composed from in this
    process, joining back to it exactly; None for any other text. The
    prompt-cache check names a changed part by these
    (``models/providers/recording/capture.py``)."""
    composed = _RECENT_PARTS.get(text)
    if composed is not None:
        return list(composed)
    sections = _RECENT_SECTIONS.get(text)
    if not sections:
        return None
    parts: list[tuple[str, str]] = []
    pos = len(_FENCE_OPEN)
    for index, row in enumerate(sections):
        start = pos if index == 0 else pos - len(_SEPARATOR)
        pos += int(row.get("chars") or 0)
        parts.append((str(row.get("name") or "other"), text[start:pos]))
        pos += len(_SEPARATOR)
    if parts:
        name, first = parts[0]
        parts[0] = (name, text[:len(_FENCE_OPEN)] + first)
        consumed = sum(len(part) for _name, part in parts)
        if consumed < len(text):
            parts.append(("other", text[consumed:]))
    return parts


def remember_composed(parts: list[tuple[str, str]]) -> str:
    """Join ``parts`` into the prompt a turn sends and remember its parts; a
    part named ``base`` that is a built prompt is cut into its sections."""
    named: list[tuple[str, str]] = []
    for name, part in parts:
        if not part:
            continue
        built = prompt_parts(part) if name == "base" else None
        named.extend(built or [(name, part)])
    text = "".join(part for _name, part in named)
    _RECENT_PARTS.pop(text, None)
    _RECENT_PARTS[text] = named
    while len(_RECENT_PARTS) > _RECENT_SECTIONS_MAX:
        _RECENT_PARTS.pop(next(iter(_RECENT_PARTS)))
    return text


def split_sections(text: str, sections: list[dict]) -> list[tuple[str, str]]:
    """Cut a recorded prompt back into ``(component, block)`` pairs using
    the lengths :func:`prompt_sections` gave. Text past the last section
    (a turn may append to the recorded prompt) comes back as ``other``."""
    out: list[tuple[str, str]] = []
    pos = len(_FENCE_OPEN)
    for row in sections:
        chars = max(0, int(row.get("chars") or 0))
        out.append((str(row.get("name") or "other"), text[pos:pos + chars]))
        pos += chars + len(_SEPARATOR)
    rest = text[pos - len(_SEPARATOR):].strip() if sections else text.strip()
    if rest:
        out.append(("other", rest))
    return out


def build_system_prompt(
    agent: Any,
    *,
    channel: str = "",
    tools: Optional[list] = None,
    additional_working_dirs: Optional[list] = None,
    plan_mode: bool = False,
    chat_delivery: Optional[str] = None,
    catalog_beside: bool = False,
) -> str:
    """Compose the system prompt from registered L0 + L1-project components.

    The ONE assembler (dag/overview.md §7): every model call — top-level chat,
    function body, budget accounting — goes through here, so the counted
    string is the wire string.

    ``channel`` (e.g. "telegram", "discord") is threaded through to builders
    that emit platform-specific rendering guidance. ``tools`` drives the
    tool-runtime block and the deferred-tool catalog; ``plan_mode`` appends
    the plan-mode reminder. ``chat_delivery`` marks a conversation turn (see
    ``_chat_delivery_var``); only those are told they are messaging someone.
    ``catalog_beside`` leaves the deferred-tool catalog out: the
    conversation's own turn sends it beside its message instead, so a tool
    that appears or goes never changes the prompt."""
    t_catalog = _catalog_beside_var.set(catalog_beside)
    try:
        sections = assemble_sections(
            agent, ["L0", "L1"],
            channel=channel,
            tools=tools,
            additional_working_dirs=additional_working_dirs,
            plan_mode=plan_mode,
            chat_delivery=chat_delivery,
        )
        if not sections:
            return ""
        text = _FENCE_OPEN + _SEPARATOR.join(t for _n, t in sections) + _FENCE_CLOSE
        _remember_sections(text, sections)
        return text
    except MemoryPolicyError:
        raise
    except Exception:
        inline = _attr(agent, "system_prompt", "") or ""
        return str(inline).strip()
    finally:
        _catalog_beside_var.reset(t_catalog)


# Builders for the existing 5 blocks (migrated from system_prompt)

_GUIDES = Path(__file__).with_name("guides")


def _build_base(agent: Any) -> str:
    """The base prompt: who the agent is, how it works, what it trusts.

    One file, ``guides/base.md``, in place of the identity line and the
    separate act-don't-describe, per-provider and prompt-injection snippets
    — the shape of Codex's and Claude Code's single base prompt."""
    agent_id = _attr(agent, "id", "") or ""
    identity = _attr(agent, "identity", None)
    name = (_attr(identity, "name", "") or _attr(agent, "name", "")
            or agent_id).strip()
    mentions = _attr(identity, "mention_patterns", None) or []
    text = (_GUIDES / "base.md").read_text(encoding="utf-8")
    return text.format(
        name=name, agent_id=agent_id,
        mentions=(" Users may address you via: " + ", ".join(mentions) + ".") if mentions else "",
    ).strip()


def _build_agents_md(agent: Any) -> str:
    """The AGENTS.md chain at the turn's working directory: the global file,
    then the project root down to the working directory (``agents_md``)."""
    from . import agents_md
    return agents_md.system_prompt_block()


def _build_inline(agent: Any) -> str:
    return (_attr(agent, "system_prompt", "") or "").strip()


def _build_output_style(agent: Any) -> str:
    """The active output style's text — how replies should be written.

    ``default`` (and an unknown style name) contributes nothing, so the
    prompt is byte-identical to what it was before styles existed."""
    try:
        from openprogram.state.output_style import style_text
        return style_text().strip()
    except Exception:
        _log.debug("output style unavailable", exc_info=True)
        return ""


def _build_skills(agent: Any) -> str:
    """The ``<available_skills>`` listing — all external sources, one renderer.

    Loader and renderer are the skills extension's (``extension_link``),
    the same pair ``Runtime._skills_block`` calls, so a skill reads
    identically whether the model is in a chat turn or inside an agentic
    function."""
    from openprogram.runtime import extension_link

    source = extension_link.skills()
    if source is None:
        return ""
    try:
        skills = source.list_skills()
    except Exception:
        return ""
    if not skills:
        return ""
    disabled_obj = _attr(agent, "skills", None) or {}
    if isinstance(disabled_obj, dict):
        disabled = set(disabled_obj.get("disabled") or [])
    else:
        disabled = set(_attr(disabled_obj, "disabled", None) or [])
    enabled = [s for s in skills if s.name not in disabled]
    return source.format_skills_for_prompt(enabled).strip("\n")


def _build_memory(agent: Any) -> str:
    try:
        from openprogram.state.memory.policy import prompt_for_agent
        mem_block = prompt_for_agent(agent)
        if mem_block.strip():
            return mem_block
    except Exception as exc:
        from openprogram.state.memory.policy import MemoryPolicyError, require_or_empty
        if isinstance(exc, MemoryPolicyError):
            raise
        require_or_empty(exc)
        # Optional memory may degrade; required memory errors propagate
        # through the assembler and terminate the turn before generation.
        _log.debug("memory system prompt unavailable", exc_info=True)
    return ""


def _git_root(cwd: str) -> str:
    try:
        from .agents_md import project_root
        root = project_root(Path(cwd).expanduser().resolve())
    except Exception:
        return ""
    return str(root) if root else ""


def _shell_line(profile: Any) -> str:
    syntax = profile.syntax
    return f"{profile.name} ({profile.path})" + (f" — {syntax}" if syntax else "")


def _command_shell_note(env: Any) -> str:
    """The shell the command tool runs in, as ``command_shell.resolve_shell``
    decides it for this environment and the sandbox in force.

    Not ``$SHELL`` or ``COMSPEC``: those name the user's terminal shell
    (zsh, cmd.exe), and a command never runs in cmd.exe. With the sandbox
    on it is the sandbox's bash, which on Windows is a Linux one in WSL;
    the host shell then runs only a command approved to leave the sandbox.
    A remote environment has its own line and its own shell.
    """
    try:
        from openprogram.host._platform.command_shell import resolve_shell
        profile = resolve_shell(env)
    except Exception:
        return ""
    if profile.kind == "remote":
        return ""
    if not profile.sandboxed:
        return f" · Shell: {_shell_line(profile)}"
    note = f" · Shell: {profile.name} ({profile.path})"
    if profile.kind != "wsl-bash":
        return note
    return (f"{note} — sandboxed commands are Linux commands: write bash syntax and Linux paths; "
            "the working directory is the WSL path of the Windows one (C:\\ is usually /mnt/c). "
            f"A command approved to run outside the sandbox runs in {_shell_line(profile.host)}")


def worktree_notice(cwd: str) -> str:
    """One line when the turn runs in an agent worktree, as Claude Code's
    ``buildWorktreeNotice`` tells a forked agent: where the checkout is,
    that it is untouched, and that earlier paths and reads refer to it."""
    try:
        from openprogram.state.worktree.store import active_at_path
        wt = active_at_path(cwd)
    except Exception:
        return ""
    if wt is None:
        return ""
    return (f"Isolated worktree: this work runs in a git worktree of {wt.source_repo} on branch "
            f"{wt.branch_name}. The checkout at {wt.source_repo} stays as it is until the person "
            "merges this work. Paths into that checkout from earlier messages name the same files "
            "here by their relative path; re-read a file here before editing it.")


def _remote_environment_lines(env: Any, tools: Optional[list]) -> list[str]:
    """Where a conversation on another machine runs, and that everything else stays here.

    The machine is named by its id and described by its connection method, so
    the text names no method. The sentence about this computer keeps a target
    without a machine from being read as the environment's.
    """
    where = env.describe()
    if env.remote_files:
        place = (f"Environment: machine {env.id} ({env.name}; {where}). This conversation's shell and file "
                 "tools act on that machine, and paths are its paths.")
    else:
        place = (f"Environment: machine {env.id} ({env.name}; {where}). This conversation's shell commands "
                 "run there; its working directories are mounted there at the same paths.")
    names = {getattr(tool, "name", tool) for tool in tools or ()}
    operated = (" computer_use calls without device act on this computer."
                if "computer_use" in names else "")
    here = (f"The chat itself and OpenProgram run on this computer, not on {env.id};" + operated
            if operated else f"The chat itself and OpenProgram run on this computer, not on {env.id}.")
    return [place, here]


def environment_block(tools: Optional[list] = None,
                      additional_working_dirs: Optional[list] = None,
                      channel: str = "") -> str:
    """Where the agent runs: machine, working directory, repository, remote
    environment, extra directories and how replies render — one block, as
    Codex's environment context is."""
    import os as _os
    import platform as _platform
    from openprogram.state.paths import get_default_workdir
    from openprogram.state.worktree.context import current_worktree_path

    try:
        osname = _platform.system() or _os.name
    except Exception:
        osname = _os.name
    try:
        from openprogram.host.machines import current_environment
        env = current_environment()
    except Exception:
        env = None
    cwd = current_worktree_path() or get_default_workdir()
    lines = [f"OS: {osname}" + _command_shell_note(env),
             f"Working directory: {cwd}"]
    root = _git_root(cwd)
    if root:
        lines.append(f"Git repository: {root}")
    notice = worktree_notice(str(cwd))
    if notice:
        lines.append(notice)
    if env is not None and env.remote_shell:
        lines.extend(_remote_environment_lines(env, tools))
    if additional_working_dirs:
        lines.append("Also writable: " + ", ".join(additional_working_dirs))
    rules = _PLATFORM_RULES.get(channel or "", "")
    if channel == "web":
        rules = _web_rules()
    if rules:
        lines.append(rules)
    return "<environment>\n" + "\n".join(lines) + "\n</environment>"


def _build_environment(agent: Any) -> str:
    return environment_block(_tools_var.get(), _working_dirs_var.get(), _channel_var.get())


def _always_on_chat_turn(agent: Any) -> bool:
    """A conversation turn of an always-on Agent."""
    if _chat_delivery_var.get() is None:
        return False
    return bool((_attr(agent, "always_on", None) or {}).get("enabled"))


def _build_messaging(agent: Any) -> str:
    return (_GUIDES / "messaging.md").read_text(encoding="utf-8").strip()


_CHANNEL_NAMES = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack", "wechat": "WeChat"}
_CHANNEL_SURFACE = (
    "This conversation is also delivered to {app} and read in that app. Write standard Markdown, "
    "which is converted for it; this replaces any formatting note above. Keep to one or two sentences "
    "unless asked for more. Never use tables or drawings: write each row as its own line. There are no "
    "buttons here: offer choices as a short numbered list and ask for the number. Do not send the user "
    "to a screen they cannot see from here; give a link, a file or the content itself."
)


def _build_chat_surface(agent: Any) -> str:
    """Comes after ``environment``, whose drawing guide it narrows."""
    delivery = _chat_delivery_var.get()
    if delivery:
        return _CHANNEL_SURFACE.format(app=_CHANNEL_NAMES.get(delivery, delivery.title()))
    if _channel_var.get() == "web" and delivery == "":
        return "Draw only when the user asks or a picture is the answer; this overrides the drawing advice above."
    return ""


def current_date_note(today: Any = None) -> str:
    """Today's date as the line a turn sends beside its message.

    Not part of the system prompt: the date changes at midnight with nothing
    else changing, and in the system prompt it would end every cached prefix
    (Codex's instructions come before tools and input). A turn's message
    carries the date it was sent on, stored on its node, so earlier turns
    render the same bytes after midnight; Codex sends the date the same way,
    in the environment context of the turn's input."""
    import datetime as _dt
    today = today or _dt.date.today()
    return today.strftime("Today is %A, %B %d, %Y.")


_PLATFORM_RULES: dict[str, str] = {
    "telegram": (
        "Telegram: use Markdown (bold/**/, italic/_/, code/`/, links). "
        "No tables. Messages over 4096 chars are split. "
        "Keep replies concise; use multiple messages for long output."
    ),
    "discord": (
        "Discord: use Markdown (bold/**/, italic/*/, code blocks/```/). "
        "Messages over 2000 chars are rejected — split long output. "
        "Use embeds sparingly. Mention users with <@id> format."
    ),
    "slack": (
        "Slack: use mrkdwn (*bold*, _italic_, `code`, ```code blocks```). "
        "NOT standard Markdown. No # headers. "
        "Messages over 40000 chars are rejected. "
        "Use Block Kit sections for structured output."
    ),
    "wechat": (
        "WeChat: plain text only, no Markdown rendering. "
        "Messages over 2048 chars may be truncated. "
        "No message editing after send. Keep replies short."
    ),
    "web": "",
}

# The OpenProgram web/desktop chat draws ```visual blocks (HTML/SVG in a
# sandboxed view, apps/web/lib/chat/markdown/visual.ts). Drawing is on by
# default, so the guide is part of the prompt rather than a file to read first.
def _web_rules() -> str:
    return (_GUIDES / "visuals.md").read_text(encoding="utf-8").strip()






# Session-dag.md §7 — blocks the dispatcher used to append by hand after
# calling the assembler. They live here now so the assembler is the single
# producer of the wire system prompt.

def _build_deferred_catalog(agent: Any) -> str:
    """Layer 6 catalog: the deferred tools' names, so the model can discover
    (and ``tool_search``) them even though their schemas aren't shipped.
    Left out of a conversation's own prompt (``catalog_beside``)."""
    tools = _tools_var.get()
    if not tools or _catalog_beside_var.get():
        return ""
    if not any(getattr(t, "name", None) == "tool_search" for t in tools):
        return ""
    from openprogram.runtime import program_host
    program = program_host.host()
    _, deferred = program.split_tools_for_dispatch(list(tools))
    if not deferred:
        return ""
    return program.deferred_catalog_text(deferred) or ""


#: What a tool adds to a prompt of its own when it is offered, by tool
#: name (``register_tool_section``): ``computer_use`` lists what it can
#: operate.
_TOOL_SECTIONS: dict[str, Callable[[], str]] = {}


def register_tool_section(tool_name: str, text: Callable[[], str]) -> None:
    """Add ``text()`` to the capabilities of a prompt whose tools offer
    ``tool_name``. The tool's Program registers it when it loads."""
    _TOOL_SECTIONS[tool_name] = text


def _build_tool_sections(agent: Any) -> str:
    """What the offered tools add, for a prompt of its own. A
    conversation's turns get it beside their message instead
    (``catalog_beside``), so connecting a device never changes their prompt."""
    tools = _tools_var.get()
    if not tools or _catalog_beside_var.get():
        return ""
    offered = {getattr(t, "name", None) for t in tools}
    parts = []
    for tool_name, text in list(_TOOL_SECTIONS.items()):
        if tool_name not in offered:
            continue
        try:
            parts.append(text())
        except Exception:
            _log.debug("prompt section of %s unavailable", tool_name, exc_info=True)
    return "\n\n".join(part for part in parts if part and part.strip())


_PLAN_MODE_TEXT = (
    "<plan-mode>\n"
    "This conversation is in plan mode: investigate and propose, "
    "but change nothing. Use read-only tools only. Do not edit "
    "files, run shell commands, change configuration or commit. "
    "These limits take priority over every other instruction "
    "until a plan is approved.\n\n"
    "Steps:\n"
    "1. Read the relevant code with read, glob and grep.\n"
    "2. Write a specific plan.\n"
    "3. Send it with `exit_plan_mode(plan=...)`. That call is the "
    "approval request, so do not also ask in your reply.\n\n"
    "After a rejection, change the plan to answer the reason given "
    "and send it again. You stay in plan mode until a plan is "
    "approved.\n"
    "</plan-mode>"
)


def _authoring_guide() -> str:
    """The Program authoring guide on this computer, as a file the agent can read."""
    from openprogram.runtime import interface_link

    try:
        return interface_link.document_file("capabilities/workflows/authoring")
    except Exception:
        return ""


def _build_programs(agent: Any) -> str:
    """How the chat agent writes a Program when the user asks for a workflow.

    Only with tools: writing a package needs the file and shell tools."""
    tools = _tools_var.get()
    if not tools:
        return ""
    names = {getattr(t, "name", "") for t in tools}
    from openprogram.state.program_directory import programs_root
    root = programs_root()
    guide = _authoring_guide()
    history = ("earlier conversations (`list_agents`, `read_conversation`), "
               if {"list_agents", "read_conversation"} <= names else "earlier conversations, ")
    lines = [
        "<programs>",
        f"The user's Programs are Python packages in {root}/<name>/, each its own Git repository. "
        "An entry becomes a tool once `openprogram programs check <directory>` passes.",
    ]
    if guide:
        lines.append(f"Read the authoring guide at {guide} before writing or changing a Program.")
    lines += [
        "When the user asks for a workflow or a Program, or sends a recorded demonstration to turn into one:",
        "1. Fill its contract (purpose, inputs, output, effects, failure behavior, tests) from this conversation.",
        f"2. Fill what is still missing from {history}memory, and any recorded demonstration.",
        "3. Ask the user, in one short message, only for what is still missing or ambiguous.",
        "4. Write the package with a test, run `openprogram programs check`, and summarize the steps, "
        "inputs and assumptions.",
        "</programs>",
    ]
    return "\n".join(lines)


def _build_plan_mode(agent: Any) -> str:
    return _PLAN_MODE_TEXT if _plan_mode_var.get() else ""


def _build_capabilities(agent: Any) -> str:
    """What the agent can reach beyond its loaded tools: skills, the
    Program recipe, and the deferred tools it can load by name."""
    parts = [part for part in (
        _build_skills(agent), _build_programs(agent), _build_deferred_catalog(agent), _build_tool_sections(agent),
    ) if part and part.strip()]
    return "\n\n".join(parts)


# The system prompt, top to bottom. Most stable first, so the cached prefix
# survives the longest:
#   L0 — the same in every session of this agent.
#   L1 — this session and project; plan mode at the very end.
register(ContextComponent("base", "L0", 10, _build_base))
# Output style shapes how replies are written; the agent's own instructions
# come after it and win on any conflict.
register(ContextComponent("output_style", "L0", 20, _build_output_style))
# Constant text, so ordinary turns of an always-on chat keep their cache.
register(ContextComponent("messaging", "L0", 25, _build_messaging, _always_on_chat_turn))
register(ContextComponent("inline_prompt", "L0", 30, _build_inline))
register(ContextComponent("environment", "L1", 10, _build_environment))
register(ContextComponent("chat_surface", "L1", 12, _build_chat_surface, _always_on_chat_turn))
register(ContextComponent("agents_md", "L1", 20, _build_agents_md))
register(ContextComponent("memory", "L1", 30, _build_memory))
# Order 35 in L1 is the conversation package's "open_work" section, which
# it registers on import (openprogram.runtime.conversation.layer_registrations.context_registration).
register(ContextComponent("capabilities", "L1", 40, _build_capabilities))
# No date here: it changes at midnight on its own, so it rides each turn's
# message instead (``current_date_note``, ``context.session_dag.render.turn_block``).
# Plan mode last: it overrides everything above it. Only a turn with a prompt
# of its own has it here; the conversation's own turn gets it beside its
# message (``conversation.session_settings.plan_mode.note_for``), so entering and leaving Plan
# never rebuilds the conversation's prompt.
register(ContextComponent("plan_mode", "L1", 90, _build_plan_mode))


# Prompt injection detection

import re as _re
import logging as _logging

_pi_log = _logging.getLogger(__name__)

_PI_PATTERNS: list[tuple[_re.Pattern[str], str]] = [
    (_re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", _re.I),
     "ignore previous instructions"),
    (_re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)", _re.I),
     "disregard previous"),
    (_re.compile(r"you\s+are\s+now\s+", _re.I), "role override (you are now)"),
    (_re.compile(r"new\s+instructions?\s*:", _re.I), "new instructions block"),
    (_re.compile(r"system\s+prompt\s*:", _re.I), "system prompt override"),
    (_re.compile(r"override\s+(your|all|the)\s+", _re.I), "override directive"),
    (_re.compile(r"\[INST\]", _re.I), "instruction tag [INST]"),
    (_re.compile(r"<<\s*SYS\s*>>", _re.I), "system tag <<SYS>>"),
    (_re.compile(r"</s>"), "end-of-sequence token </s>"),
    (_re.compile(r"<\|im_start\|>", _re.I), "ChatML tag <|im_start|>"),
    (_re.compile(r"forget\s+(everything|all|what)\s+", _re.I), "forget directive"),
]


def detect_injection_patterns(text: str) -> list[str]:
    """Scan *text* for common prompt-injection patterns. Returns a list of
    human-readable descriptions of matched patterns (empty = clean)."""
    return [desc for pat, desc in _PI_PATTERNS if pat.search(text)]




# L2 (task-level, this-call-only) has no registered components. The layer
# stays in the registry and in ``assemble``'s signature because the
# assembler is generic over layers, but ``build_system_prompt`` assembles
# L0+L1 only — a component registered here would never reach the wire.
# Anything task-scoped belongs in the turn's user message instead, the way
# the memory prefetch block does (dag/overview.md §7).


__all__ = [
    "ContextComponent",
    "register",
    "assemble",
    "build_system_prompt",
    "current_date_note",
    "detect_injection_patterns",
]
