"""Gemini CLI (``~/.gemini/settings.json`` and ``~/.gemini/.env``).

After yetone/magpie ``internal/agent/gemini.go`` (MIT License, Copyright (c)
yetone). Gemini CLI only speaks Google's own API, so it is put on the gateway
the way magpie does it: the gateway serves the Gemini API, so
``GOOGLE_GEMINI_BASE_URL`` in ``~/.gemini/.env`` (which the CLI loads) points
at it, the gateway key is the ``GEMINI_API_KEY`` beside it, and
``settings.json`` signs in with that key (``security.auth.selectedType =
"gemini-api-key"``) and names the OpenProgram model in ``model.name``.
Gemini CLI is given a key of its own (``named_key``), never the gateway's.
Its ``/model`` and ``/auth`` rewrite ``model.name`` and the sign-in choice
themselves, so both are loose: no drift, and while ``.env`` names the
gateway disconnecting puts back the user's own base URL, key, sign-in choice
and model as a pair, whatever Gemini CLI wrote there since (magpie
``unroute``), and takes out a ``"model": {}`` it would leave. A reconnect on
the same model (a new port) leaves a model picked in Gemini CLI. Gemini CLI's own
Google sign-in is never read or passed on.

``$GEMINI_CLI_HOME`` moves the folder, as it does for the CLI and for
OpenProgram's Gemini CLI chats (``runtime/harnesses/gemini_cli``). Those
chats run on Gemini CLI's own models and sign-in even while its terminal
config is on the gateway: the chat's process is given back the user's own
setup (``gemini_cli.client.launch_env``).
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import Agent, Field, Wiring, Write, taken

AUTH_KEY = "security.auth.selectedType"
API_KEY_AUTH = "gemini-api-key"
BASE_ENV = "GOOGLE_GEMINI_BASE_URL"
KEY_ENV = "GEMINI_API_KEY"


def home() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()).expanduser() / ".gemini"


def config_path() -> Path:
    return home() / "settings.json"


def env_path() -> Path:
    return home() / ".env"


def _model_now() -> str:
    from ..edit import ConfigText, ConfigUnreadable, get_path, load
    try:
        text = ConfigText.read(config_path())
        if not text.exists:
            return ""
        present, value = get_path(load("jsonc", text.text, str(config_path())), "model.name")
    except (ConfigUnreadable, OSError):
        return ""
    return value if present and isinstance(value, str) else ""


def _wire(at: Wiring) -> list[Write]:
    model = at.model
    if (at.previous.get("values") or {}).get("model") == model:
        model = _model_now() or model  # a pick made in Gemini CLI since stays
    return [
        Write(env_path(), "dotenv", sets={BASE_ENV: at.base("gemini"), KEY_ENV: at.key}),
        Write(config_path(), "jsonc", sets={AUTH_KEY: API_KEY_AUTH, "model.name": model},
              loose=(AUTH_KEY, "model.name")),
    ]


def _detect() -> bool:
    # Gemini CLI is its binary. What it leaves in ~/.gemini stays when it is
    # uninstalled, and Antigravity keeps its folders there too (antigravity,
    # antigravity-cli, config) and reads GEMINI.md, so nothing there says
    # Gemini CLI is here: a row for it would set up a CLI that can't run.
    from .base import find_tool
    return not taken(home()) and bool(find_tool("gemini"))


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)gemini( |$)"):
        return "Gemini CLI reads its settings at start-up — restart open gemini sessions to see this."
    return ""


def agent() -> Agent:
    return Agent(
        id="gemini", name="Gemini CLI", icon="geminicli", aliases=("gemini-cli",), kind="cli",
        protocol="gemini", bin="gemini", dir=home, path=config_path, fields=[Field("model", "Model")],
        ua=("geminicli", "gemini-cli"), wire=_wire, notice=_notice, detect=_detect, detect_only=True,
        named_key=True,
    )
