"""The outside agents OpenProgram's gateway can be written into: one registry.

After yetone/magpie ``internal/agent`` (MIT License, Copyright (c) yetone),
whose agents OpenProgram integrates the way magpie does. Each agent is one
module here returning an :class:`Agent` (``base.py``): what it is, where its
config lives, what connecting writes. The engine (``engine.py``) connects,
syncs, reports drift and disconnects every agent alike; ``lifecycle.py``
keeps connected agents in step with the providers and the Worker's port;
``running.py`` finds an app running on old settings and restarts it.

  base.py         Agent, Field, Write, Wiring, App, Live; detection helpers
  engine.py       connect / sync / disconnect / status / drift / import links
  lifecycle.py    catalog sync, reseat off removed models, rewire on a new port
  running.py      running processes, an app behind its config, its restart
  local_api.py    a running app's own HTTP API, on loopback or a Unix socket
  appdb.py        another app's SQLite database, read without touching it
  applink.py      an app's import link, handed over when something opens it
  keychain.py     a key in the OS credential store (Zed)
  userenv.py      variables in the user's environment for apps opened later
  shellfn.py      a function in the user's shell profiles that starts an agent
  reach.py        an agent's address that reaches the gateway through a proxy
  codex.py        Codex CLI and app
  claude_code.py  Claude Code, and a row per other config folder of it
  claude_desktop.py, claude_science.py, t3code.py, openmausbot.py,
  kiro_crew.py, paseo.py, newmax.py, minimax.py, zcode.py
                  the apps and CLIs ported in magpie's batches 2 and 3
  opencode_format.py  OpenCode's config shape, shared by the agents that keep it
  opencode.py, openchamber.py, mimocode.py, deveco.py, air.py, air_ide.py,
  openbot.py      OpenCode and the agents that run on it (magpie batch 6)
  pi_like.py      Pi's settings.json + models.json, shared by Pi and its forks
  pi.py, omo.py, prime_agent.py, mu.py, stepcode.py, aside.py, pencil.py,
  craft_agents.py, liveagent.py, mothx.py, omp.py (+ its profiles)
                  the agents in Pi's format (magpie batch 5)
  gemini.py, agy.py, cursor.py, devin.py
                  Gemini CLI, Antigravity CLI, and Cursor CLI and Devin
                  with their own models only (magpie batches 4 and 12)
  shellfn.py      a function in the user's shell profiles (agy's, Dim's dima)
  fasttools/      the fast-tools plugin given to Antigravity
  vscode.py, vscode_insiders.py, vscodium.py, zoo_code.py, zed.py,
  cursor_local.py, antigravity.py
                  editors and IDEs (magpie batch 9)
  dim.py, kun.py, nowledge_mem.py, hanako.py, alma.py, shelley.py, miniq.py,
  avibe.py        apps changed through their own local API (magpie batch 10)
  cindy.py, aionui.py, cherry_studio.py, lody.py
                  apps that take the gateway only through their own import
                  link (magpie batch 11)
  crush.py, goose.py, kimi.py, jcode.py, vibe.py, atomcode.py, grok.py,
  kigi.py, grok_app.py, reasonix.py, morph.py, muse.py, penguin.py,
  hermes.py, raycast.py, dsh.py, ds_bot.py
                  TOML/YAML provider tables (magpie batch 8)
  model_rows.py   the enabled models as the provider-table agents list them
  telemetry.py    an agent's own telemetry switch, a kept choice

To add an agent, write a module with ``agent() -> Agent`` and list it in
:data:`MODULES`; nothing else names agents. A module may also have
``extra_agents() -> list[Agent]``: rows of their own found on this computer
(Claude Code's other config folders, Qoder CN, WorkBuddy AI, QwenPaw's other agents, omp's
profiles).
"""
from __future__ import annotations

import importlib
from typing import Optional

from .base import ROOT, Agent, App, AppGone, Field, Live, Members, Wiring, Write

#: The agent modules, in display order.
MODULES = (
    "codex",
    "claude_code",
    # magpie batches 2 and 3: the Anthropic-env family, Kiro Crew
    "claude_desktop",
    "claude_science",
    "t3code",
    "openmausbot",
    "kiro_crew",
    "paseo",
    "newmax",
    "minimax",
    "zcode",
    # Chat Completions provider entries in JSON (magpie batch 7)
    "qwen",
    "copilot",
    "commandcode",
    "fx",
    "empryo",
    "nanobot",
    "paimon",
    "ante",
    "cline",
    "qoder",
    "ocr",
    "snow",
    "tuanjie_cowork",
    "openpencil",
    "workbuddy",
    "codebuddy",
    "copilot_jetbrains",
    "droid",
    "obsidian",
    "astrbot",
    "openclaw",
    "qwenpaw",
    # OpenCode format (opencode_format.py, magpie batch 6)
    "opencode",
    "openchamber",
    "mimocode",
    "deveco",
    "air",
    "air_ide",
    "openbot",
    # Pi's format (pi_like.py, magpie batch 5)
    "pi",
    "omo",
    "prime_agent",
    "mu",
    "stepcode",
    "aside",
    "pencil",
    "craft_agents",
    "liveagent",
    "mothx",
    "omp",
    # Gemini's API, and own models only (magpie batches 4 and 12)
    "gemini",
    "agy",
    "cursor",
    "devin",
    # TOML/YAML provider tables (magpie Batch 8)
    "crush",
    "goose",
    "kimi",
    "jcode",
    "vibe",
    "atomcode",
    "grok",
    "kigi",
    "grok_app",
    "reasonix",
    "morph",
    "muse",
    "penguin",
    "hermes",
    "raycast",
    "dsh",
    "ds_bot",
    # editors and IDEs; apps changed through their own local API; apps
    # that take the gateway only through their own import link
    "vscode", "vscode_insiders", "vscodium", "zoo_code", "zed", "cursor_local", "antigravity",
    "dim", "kun", "nowledge_mem", "hanako", "alma", "shelley", "miniq", "avibe",
    "cindy", "aionui", "cherry_studio", "lody",
)

__all__ = ["Agent", "App", "AppGone", "Field", "Live", "Members", "ROOT", "Wiring", "Write", "MODULES", "all_agents", "find", "detected", "agent_of_ua"]


def all_agents() -> list[Agent]:
    """Every agent OpenProgram knows, in display order (magpie ``All``)."""
    _install_marks()
    found = []
    for name in MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        found.append(module.agent())
        # rows of their own beside the main one (magpie's nested rows:
        # Claude Code's other config folders, claude_dirs.go)
        extra = getattr(module, "extra_agents", None)
        if extra is not None:
            found.extend(extra())
    return found


def find(query: str, agents: Optional[list[Agent]] = None) -> Optional[Agent]:
    """An agent by id, alias, or unique prefix of its id or name (magpie ``Find``)."""
    query = (query or "").strip().lower()
    if not query:
        return None
    agents = agents if agents is not None else all_agents()
    prefix = []
    for agent in agents:
        if agent.id.lower() == query or query in agent.aliases:
            return agent
        if agent.id.startswith(query) or agent.name.lower().startswith(query):
            prefix.append(agent)
    return prefix[0] if len(prefix) == 1 else None


def detected(agents: Optional[list[Agent]] = None) -> list[Agent]:
    """The agents that seem to be on this computer (magpie ``Detected``)."""
    return [agent for agent in (agents if agents is not None else all_agents()) if agent.installed()]


def agent_of_ua(user_agent: str, agents: Optional[list[Agent]] = None) -> Optional[str]:
    """The agent a User-Agent names: its product name an id or alias first,
    then a known prefix (magpie ``usage.AgentOf``)."""
    text = (user_agent or "").strip()
    name = text.split("/", 1)[0].split(" ", 1)[0].lower()
    if not name:
        return None
    agents = agents if agents is not None else all_agents()
    for agent in agents:
        if name == agent.id or name in agent.aliases:
            return agent.id
    for agent in agents:
        if any(name.startswith(prefix) for prefix in agent.ua):
            return agent.id
    if name in LIBRARIES:
        return _agent_after(text, agents)
    return None


#: HTTP libraries, runtimes and the vendors' SDKs, which an app calls a model
#: with (magpie ``usage.libraries``): such a User-Agent may name its app after it.
LIBRARIES = frozenset({
    "curl", "wget", "httpie", "postmanruntime", "insomnia",
    "bun", "node", "deno", "node-fetch", "undici", "axios", "got", "ky",
    "python", "python-httpx", "httpx", "python-requests", "requests", "python-urllib", "urllib3", "aiohttp",
    "go-http-client", "go-resty", "resty", "okhttp", "java", "java-http-client", "apache-httpclient",
    "reactor-netty", "ruby", "faraday", "rest-client", "guzzlehttp", "dart", "reqwest", "libcurl",
    "openai", "asyncopenai", "anthropic", "asyncanthropic", "ai-sdk", "google-genai-sdk",
})


def _agent_after(user_agent: str, agents: list[Agent]) -> Optional[str]:
    """The agent a library's User-Agent names after its first product, among
    the products (not the comments) it goes on to list: DeepSeek Harness's
    Gemini route goes out as ``google-genai-sdk/2.32.0 gl-node/v25.8.2,
    deepseek-harness/0.2.2-rc.5 (+https://…)`` (magpie ``agentAfter``)."""
    plain, depth = [], 0
    for char in user_agent:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif not depth:
            plain.append(char)
    words = [w for w in "".join(plain).replace(",", " ").split() if w]
    for word in words[1:]:
        name = word.split("/", 1)[0].lower()
        if not name or name in LIBRARIES:
            continue
        for agent in agents:
            if name == agent.id or name in agent.aliases or any(name.startswith(p) for p in agent.ua):
                return agent.id
    return None


def _install_marks() -> None:
    from ..edit import mark
    if mark.note is None:
        from .lifecycle import line_note
        mark.note = line_note
