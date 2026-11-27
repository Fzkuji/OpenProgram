"""WorkBuddy and WorkBuddy AI (Tencent's CodeBuddy desktop app; ``~/.workbuddy/models.json``).

After yetone/magpie ``internal/agent/workbuddy.go`` (MIT License, Copyright
(c) yetone). WorkBuddy takes models of one's own from
``~/.workbuddy/models.json`` (``$WORKBUDDY_CONFIG_DIR`` if set): a list of
OpenAI chat-completions models, or ``{"models": […], "availableModels": […]}``
as CodeBuddy Code's CLI writes it. Its own "local models" are entries of the
same list (``local: true``)::

    [{"id": "deepseek/deepseek-chat", "name": …, "vendor": "openprogram",
      "apiKey": "<key naming WorkBuddy>", "url": "http://127.0.0.1:<port>/…/v1/chat/completions",
      "maxInputTokens": …, "maxOutputTokens": …,
      "contextWindow": {"supportedLengths": [200000, …], "defaultLength": …},
      "supportsToolCall": true, "supportsImages": …, "supportsReasoning": true,
      "reasoning": {"supportedEfforts": […], "canDisableThinking": …, "defaultEffort": …}}]

WorkBuddy shows them in its picker as ``custom-local:<id>`` and asks ``url``
for model ``<id>``, with ``reasoning_effort``, under ``Bearer apiKey``. 5.6's
app.asar watches the file only when it was there at start, and the watch is
on the file itself, so a rewrite can go unseen until a restart; the row
offers one (``App``). The model is picked per task in WorkBuddy's window, not
in a file, so what connecting sets is whether OpenProgram's models are in
that picker. Its requests say ``CLI/<ver> WorkBuddy/<ver>``, so they are
told apart by their key.

OpenProgram's entries are those of vendor ``openprogram``; the user's own
stay as WorkBuddy wrote them. A model turned off in WorkBuddy stays off, and
the default effort set on one in WorkBuddy's model settings stays while the
model still offers it. A new file is a bare list for WorkBuddy, as it writes
one, and ``{"models": […]}`` for CodeBuddy Code (``codebuddy.py``), as its
docs give it; a list of the models to show (``availableModels``) keeps
showing OpenProgram's.

WorkBuddy AI, the international build (bundle ``com.workbuddy.workbuddy-ai``),
is the same app with its data in ``~/.workbuddy-ai`` (its
``cli/product.json`` ``dataFolderName``); it is an agent of its own, its
models under its own key, so each build's picker is written in its own folder.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ROOT, Agent, App, Members, Wiring, Write, agent_dir, app_bundles, taken

VENDOR = "openprogram"
#: The smaller context WorkBuddy offers beside a model's whole window, the
#: size its own models offer.
SMALL_WINDOW = 200000
#: A model whose window isn't known is given this one.
DEFAULT_WINDOW = 200000
#: Some vendors report their window as the output limit (grok's 500000);
#: WorkBuddy would offer that much (magpie ``zcodeMaxOutput``).
MAX_OUTPUT = 128000

APP = App("com.tencent.workbuddy.mac", ("WorkBuddy.app",))
AI_APP = App("com.workbuddy.workbuddy-ai", ("WorkBuddy AI.app",))


def default_level(levels: list[str]) -> str:
    """medium where a model takes it, else the middle of its levels (magpie ``zcodeDefaultLevel``)."""
    if "medium" in levels:
        return "medium"
    on = [level for level in levels if level != "disabled"]
    return on[len(on) // 2] if on else levels[0]


def buddy_model(model: dict[str, Any], key: str, v1: str) -> dict[str, Any]:
    """One model as a models.json entry (magpie ``buddyModel``), keys sorted as Go writes them."""
    context = int(model.get("context_window") or 0) or DEFAULT_WINDOW
    entry: dict[str, Any] = {
        "id": model["id"], "name": model.get("name") or model["id"], "vendor": VENDOR,
        "apiKey": key, "url": v1 + "/chat/completions", "maxInputTokens": context,
        "supportsToolCall": True, "supportsImages": bool(model.get("vision")), "supportsReasoning": False,
    }
    # WorkBuddy offers a choice of context only for a model whose
    # contextWindow lists two sizes or more (its own Opus has 200K and 1M);
    # the session's budget is defaultLength, so it is the whole window
    if context > SMALL_WINDOW:
        entry["contextWindow"] = {"supportedLengths": [SMALL_WINDOW, context], "defaultLength": context}
    # without it WorkBuddy caps every reply at its own default
    output = int(model.get("max_tokens") or 0)
    if output > 0:
        entry["maxOutputTokens"] = min(output, context, MAX_OUTPUT)
    efforts = list(model.get("thinking_levels") or [])
    levels: list[str] = []
    for level in efforts:
        if level != "none" and level not in levels:
            levels.append(level)
    if levels:
        entry["supportsReasoning"] = True
        entry["reasoning"] = {"supportedEfforts": levels, "canDisableThinking": "none" in efforts,
                              "defaultEffort": default_level(levels)}
    return dict(sorted(entry.items()))


def read_doc(path: Path) -> Any:
    """models.json as read: a list, an object, or None when empty or absent."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None  # the engine refuses the file with its reason


def buddy_write(path: Path, at: Wiring, *, bare_new: bool) -> Write:
    """OpenProgram's models in a models.json, the user's entries left as they
    are (magpie ``buddyWriteAt``); ``bare_new``: a new file is a bare list."""
    doc = read_doc(path)
    listed = doc if isinstance(doc, list) else (doc.get("models") if isinstance(doc, dict) else None)
    mine = [e for e in (listed or []) if isinstance(e, dict) and e.get("vendor") == VENDOR]
    off = {e.get("id") for e in mine if e.get("disabled") is True}
    efforts = {e.get("id"): (e.get("reasoning") or {}).get("defaultEffort") or (e.get("reasoning") or {}).get("effort") or ""
               for e in mine if isinstance(e.get("reasoning"), dict)}
    v1 = at.base("openai-chat")
    items = []
    for model in at.models:
        entry = buddy_model(model, at.key, v1)
        if entry["id"] in off:
            entry["disabled"] = True
            entry = dict(sorted(entry.items()))
        reasoning = entry.get("reasoning")
        # the default effort set in WorkBuddy's model settings (or by hand)
        # stays while the model still has it, Auto ("") included
        if reasoning and entry["id"] in efforts:
            was = efforts[entry["id"]]
            if not was:
                reasoning.pop("defaultEffort", None)
            elif was in reasoning["supportedEfforts"]:
                reasoning["defaultEffort"] = was
        items.append(entry)
    # where OpenProgram's entries were, which they keep, else at the end (magpie buddyWriteAt)
    # an effort or an on/off set in WorkBuddy's model settings is no drift
    members = Members({"vendor": VENDOR}, items, keep_place=True, loose=("reasoning", "disabled"))
    if isinstance(doc, list) or (doc is None and bare_new):
        return Write(path, "json", listing=True, members={ROOT: members})
    sets: dict[str, Any] = {}
    available = doc.get("availableModels") if isinstance(doc, dict) else None
    if isinstance(available, list) and available:
        # a list of the models to show keeps showing OpenProgram's
        ours = {e.get("id") for e in mine}
        sets["availableModels"] = [m for m in available if m not in ours] + [e["id"] for e in items]
    return Write(path, "json", listing=True, sets=sets, members={"models": members})


def _detect(home, app: App):
    def detect() -> bool:
        """On a Mac its app bundle: dragging WorkBuddy.app to the Trash leaves
        its folder behind. Elsewhere, its models.json or its folder."""
        folder = home()
        if taken(folder):
            return False
        if sys.platform == "darwin":
            return bool(app_bundles(app))
        return (folder / "models.json").is_file() or agent_dir(folder)
    return detect


def _build(id: str, name: str, home, aliases: tuple[str, ...], app: App) -> Agent:
    def path() -> Path:
        return home() / "models.json"
    return Agent(
        id=id, name=name, icon="workbuddy-color", aliases=aliases, kind="app", protocol="openai-chat",
        dir=home, path=path, ua=(id,), named_key=True, app=app, detect=_detect(home, app), detect_only=True,
        wire=lambda at: [buddy_write(path(), at, bare_new=True)],
    )


def workbuddy_home() -> Path:
    return Path(os.environ.get("WORKBUDDY_CONFIG_DIR") or str(Path.home() / ".workbuddy")).expanduser()


def workbuddy_ai_home() -> Path:
    # WORKBUDDY_CONFIG_DIR is WorkBuddy's: both builds would read a folder
    # it names, which that agent writes already
    return Path.home() / ".workbuddy-ai"


def agent() -> Agent:
    return _build("workbuddy", "WorkBuddy", workbuddy_home, ("work-buddy",), APP)


def extra_agents() -> list[Agent]:
    """WorkBuddy AI, its international build, a row of its own."""
    return [_build("workbuddy-ai", "WorkBuddy AI", workbuddy_ai_home, ("workbuddy-intl", "workbuddyai"), AI_APP)]
