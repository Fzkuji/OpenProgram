"""CodeBuddy Code and the CodeBuddy IDE (``$CODEBUDDY_CONFIG_DIR/models.json``, default ``~/.codebuddy``).

After yetone/magpie ``internal/agent/codebuddy.go`` (MIT License, Copyright
(c) yetone). CodeBuddy Code (Tencent's CLI, npm ``@tencent-ai/codebuddy-code``:
``codebuddy``, ``cbc``) is one package for the international edition
(codebuddy.ai) and the China one (codebuddy.cn). Both take models of one's
own from ``models.json``, in WorkBuddy's format (``workbuddy.py``)::

    {"models": [{"id": "deepseek/deepseek-chat", "name": …, "vendor": "openprogram",
      "apiKey": "<key naming CodeBuddy>", "url": "…/v1/chat/completions", …}],
     "availableModels": […]}

and pick up a change to it without a restart. Such a model needs no
CodeBuddy sign-in: the CLI asks ``url`` for it under ``Bearer apiKey``. The
model a session starts on is ``settings.json``'s ``model`` (an id from that
list, or one of CodeBuddy's own), read when a session starts; what the user
had there is stashed and put back on disconnect. Its requests say
``CLI/<ver> CodeBuddy/<ver>``, so they are told apart by their key.

The CodeBuddy IDE (CodeBuddy.app, CodeBuddy CN.app) reads the same file: its
genie extension loads ``models.json``'s ``models`` and ``availableModels``,
watches the file and lists each in its own model picker. It needs no CLI, so
one row, CodeBuddy, stands for both. The IDE reads ``<home>/.codebuddy``
whatever ``CODEBUDDY_CONFIG_DIR`` says, and not ``settings.json``'s
``model``: its model is picked in the IDE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .base import Agent, App, Wiring, Write, app_bundles
from .workbuddy import buddy_write

#: The IDE's two builds (magpie ``desktopApps["codebuddy"]``).
IDE_APPS = (App("com.tencent.codebuddy", ("CodeBuddy.app",)), App("com.tencent.codebuddycn", ("CodeBuddy CN.app",)))


def home() -> Path:
    return Path(os.environ.get("CODEBUDDY_CONFIG_DIR") or str(Path.home() / ".codebuddy")).expanduser()


def config_path() -> Path:
    return home() / "models.json"


def _wire(at: Wiring) -> list[Write]:
    return [buddy_write(config_path(), at, bare_new=False),
            Write(home() / "settings.json", "json", sets={"model": at.model})]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)(codebuddy|cbc|codebuddy-code)( |$)"):
        return "CodeBuddy Code reads its model when a session starts — restart open codebuddy sessions, or run /clear in them, to use this."
    return ""


def ide() -> bool:
    """The IDE alone is CodeBuddy here, its CLI not installed."""
    return any(app_bundles(app) for app in IDE_APPS)


def agent() -> Agent:
    return Agent(
        id="codebuddy", name="CodeBuddy", icon="codebuddy-color", aliases=("codebuddy-code", "cbc"), kind="cli",
        protocol="openai-chat", bin="codebuddy", dir=home, path=config_path, ua=("codebuddy",),
        named_key=True, wire=_wire, notice=_notice,
        # the IDE alone is CodeBuddy here, its CLI not installed (magpie sign)
        sign=ide,
    )
