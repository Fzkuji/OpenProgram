"""DS Bot (github.com/Fei2411/DS-Bot), a DeepSeek Harness desktop app with a dsh of its own.

After yetone/magpie ``internal/agent/dsbot.go`` (MIT License, Copyright (c)
yetone). The DS Bot plugin loaded into DSH Desktop or ``dsh web`` runs on
``~/.dsh`` and is the DeepSeek Harness row (``dsh.py``). The desktop app
ships its own dsh and runs it with ``DSH_HOME`` at a data folder of its own,
booting the profile ``ds-bot``. Its models are that dsh's llm-pi-ai routes
and its new Bots start on that dsh's ``agent-default-model``, so that home is
wired as ``~/.dsh`` is: OpenProgram's routes in each profile's patch list,
the key in ``.env`` and dsh's key store, and the start. Its requests can't
be told from dsh's, so it has no User-Agent of its own.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .base import Agent, App, Field, app_bundles, user_config_dir
from .dsh import Home, effort_field, notice_for
from .model_rows import env_dir

PROFILE = "ds-bot"
APP = App("io.github.fei2411.dsbot", ("DS Bot.app", "DeepSeek Bot.app"))


def data_dir() -> Optional[Path]:
    """The app's data folder, the DSH_HOME its dsh runs on; ``None`` when it
    can't be told (magpie ``dsbotHome``). ``DS_BOT_HOME``, else
    ``locations.json``'s ``dataDir`` in the app's userData (``DS_BOT_SHELL_DATA``,
    else ``<appData>/DS Bot``, or ``DeepSeek Bot``, its name before, while that
    one is there and DS Bot's has no locations.json), else ``~/.ds-bot``, the
    folder its first run saves when the default is kept. A locations.json
    that can't be read or names no folder is unknown: never taken for the
    default, where a folder the app doesn't run on would be wired."""
    named = env_dir("DS_BOT_HOME")
    if named:
        return Path(named)
    user_data = env_dir("DS_BOT_SHELL_DATA")
    if user_data:
        folder = Path(user_data)
    else:
        folder = user_config_dir() / "DS Bot"
        legacy = user_config_dir() / "DeepSeek Bot"
        if not (folder / "locations.json").is_file() and legacy.is_dir():
            folder = legacy
    try:
        raw = (folder / "locations.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return Path.home() / ".ds-bot"
    except OSError:
        return None
    try:
        place = str((json.loads(raw) or {}).get("dataDir") or "").strip()
    except (ValueError, AttributeError):
        return None
    return Path(place) if place else None


def profile_file(dir_: Path) -> Path:
    """The app's profile's patch list. Its initProfile writes each of the
    profile's files only where missing, so one made before the app first
    opened is kept."""
    return dir_ / "profiles" / PROFILE / "cordis.patch.yml"


def _app() -> bool:
    return bool(app_bundles(APP))


def _notice() -> str:
    note = notice_for(
        data_dir, "DS Bot hasn't been opened yet: it starts on this the first time it opens.",
        _app, profile_file, (), "", "")
    return note or ("New Bots in DS Bot start on this, unless a default model for Bots is set in DS Bot. "
                    "A Bot made before keeps its own model until you pick another for it.")


def agent() -> Agent:
    folder = data_dir()
    # its own dsh's version isn't read: Gemini's route stays off (magpie dshSpeaksGemini)
    wiring = Home("ds_bot", lambda: folder, profile_file)
    # with its data folder unknown there is nothing to read or write; the
    # installed app still shows it
    path = wiring.path if folder is not None else (lambda: user_config_dir() / "DS Bot" / "locations.json")
    return Agent(
        id="ds_bot", name="DS Bot", icon="deepseek", aliases=("ds-bot", "dsbot", "deepseek-bot"), kind="app",
        protocol="openai-chat", dir=(lambda: folder) if folder is not None else None, path=path,
        # no compaction model: its requests can't be told from dsh's
        fields=[Field("model", "Model"), effort_field("ds_bot", wiring.efforts)],
        # no bin: its command (dsbot, on Linux) is the app itself
        wire=wiring.wire, unwire=wiring.unwire, on_route=wiring.on_route, notice=_notice,
        # the app installed and never opened has no data folder yet; while its
        # data folder can't be told, only the app tells it is here
        sign=_app, detect=_app if folder is None else None, detect_only=folder is None,
    )
