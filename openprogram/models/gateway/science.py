"""Claude Science at the gateway: how it is known, listed models and served.

Ported from yetone/magpie ``internal/gateway/science.go`` (MIT License,
Copyright (c) yetone), with ``aliasNumber`` and ``desktopNames`` from
``desktop.go``.

Claude Science (Anthropic's research app, "operon" inside) is a daemon that
serves its UI on localhost and asks Anthropic's Messages API at the base URL
its environment's ``ANTHROPIC_BASE_URL`` names (https anywhere, http only on
a loopback address). OpenProgram starts the daemon with that variable set to
the gateway (``agents/claude_science.py``). It still needs its own Claude
sign-in: with none it asks nothing, and with one it sends ``Authorization:
Bearer sk-ant-oat…``, which the gateway neither checks nor passes on — the
daemon's address carries the agent's own key (``Agent.path_keyed``), and
each turn goes upstream with OpenProgram's own provider key only — beside its
client's default headers ``X-Anthropic-Surface: operon`` and ``anthropic-beta:
oauth-2025-04-20``. That header is how its requests are known: its User-Agent
is the Anthropic SDK's, which says nothing of the app.

Its model picker lists from ``GET /v1/models`` only an id that starts with
``claude-`` whose display_name isn't a lower-case slug, so every model is
listed to it by an alias, ``claude-openprogram-<number>``, named as the
agents' lists name it. And it asks for Claude models by their own ids where
no pick of the user's is in play — its default model before one is picked,
``claude-haiku-…`` for its small tasks — so a model the gateway doesn't serve
goes to the one its last turn was on (:func:`turn`).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Optional

#: Claude Science's agent id.
SCIENCE_ID = "claude-science"
#: The X-Anthropic-Surface Claude Science's client sends on every request.
SURFACE = "operon"
#: The prefix of the id a model is listed to Claude Science by.
ALIAS = "claude-openprogram-"

#: A name Claude Science takes for an internal model's id and leaves out of its picker.
_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")

_lock = threading.Lock()
_picked: dict[str, str] = {}   # "model", "from": the model its latest turn was on, and its file


def is_science(headers: Any) -> bool:
    """A request from Claude Science's own client."""
    return str(headers.get("x-anthropic-surface") or "").strip().lower() == SURFACE


def alias_number(model_id: str) -> str:
    """Ten digits of an FNV-1a hash of ``model_id`` (magpie ``aliasNumber``)."""
    value = 0xcbf29ce484222325
    for byte in model_id.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f"{value % 10_000_000_000:010d}"


def science_name(name: str) -> str:
    """A model's name as Claude Science is shown it: a slug's first letter
    in upper case, which its picker keeps."""
    if not _SLUG.match(name):
        return name
    return name[:1].upper() + name[1:]


def names(rows: list[dict[str, Any]]) -> list[str]:
    """The names the models are shown by (magpie ``desktopNames``): each
    model's own, and where two are alike its id after it, short as the name
    already says the provider, or the whole id where that is still not enough."""
    out = [str(row.get("name") or row["id"]) for row in rows]

    def count(values: list[str]) -> dict[str, int]:
        same: dict[str, int] = {}
        for value in values:
            same[value.lower()] = same.get(value.lower(), 0) + 1
        return same

    same = count(out)
    short = list(out)
    for i, row in enumerate(rows):
        if same[out[i].lower()] > 1:
            model = row["id"].split("/", 1)[1] if "/" in row["id"] else row["id"]
            short[i] = f"{out[i]} ({model})"
    still = count(short)
    for i, row in enumerate(rows):
        if short[i] != out[i] and still[short[i].lower()] > 1:
            short[i] = f"{out[i]} ({row['id']})"
    return short


def science_object(row: dict[str, Any], name: str) -> dict[str, Any]:
    """``row`` as Claude Science is shown it: by its alias and a name its
    picker keeps, with the reply limit as ``max_tokens``, which it reads one by."""
    from .models_info import model_object
    entry = model_object(row)
    entry["id"], entry["display_name"] = ALIAS + alias_number(row["id"]), science_name(name)
    output = int(row.get("max_tokens") or 0)
    if output > 0:
        entry["max_tokens"] = output
    return entry


def models_list() -> dict[str, Any]:
    """``/v1/models`` as Claude Science is shown it: every enabled model by its alias."""
    from .conversion import enabled_model_refs
    rows = enabled_model_refs()
    data = [science_object(row, name) for row, name in zip(rows, names(rows))]
    out: dict[str, Any] = {"data": data, "has_more": False}
    if data:
        out["first_id"], out["last_id"] = data[0]["id"], data[-1]["id"]
    return out


def find_model(model_id: str) -> Optional[dict[str, Any]]:
    """One model as Claude Science is shown it, by its alias or its own id."""
    from .conversion import enabled_model_refs
    rows = enabled_model_refs()
    real = aliased(model_id, rows) or model_id
    for row, name in zip(rows, names(rows)):
        if row["id"] == real:
            return science_object(row, name)
    return None


def aliased(model_id: str, rows: Optional[list[dict[str, Any]]] = None) -> str:
    """The enabled model a ``claude-openprogram-<number>`` alias stands for, ``""`` for none."""
    if not isinstance(model_id, str) or not model_id.startswith(ALIAS):
        return ""
    number = model_id[len(ALIAS):].removesuffix("[1m]")
    if len(number) != 10:
        return ""
    if rows is None:
        from .conversion import enabled_model_refs
        rows = enabled_model_refs()
    for row in rows:
        if alias_number(row["id"]) == number:
            return row["id"]
    return ""


def _picked_path() -> Path:
    from .keys import gateway_dir
    return gateway_dir() / "claude-science.model"


def _has_tools(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("tools"), list) and bool(body["tools"])


def _connected_pick() -> str:
    """The model Claude Science was connected with in OpenProgram, ``""`` for none."""
    from . import stash
    try:
        record = stash.entry(SCIENCE_ID) or {}
    except stash.StashUnreadable:
        return ""
    return str((record.get("values") or {}).get("model") or "")


def turn(asked: Any, body: Any) -> Any:
    """The model a Claude Science request for ``asked`` is served by (magpie
    ``scienceTurn``): ``asked`` (an alias read as its model) when the gateway
    serves it, a turn's (it carries tools) noted as the one picked; else the
    model picked last, else the one it was connected with, else the first
    one listed to it."""
    from .conversion import enabled_model_refs
    if not isinstance(asked, str):
        return asked
    rows = enabled_model_refs()
    served = {row["id"] for row in rows}
    real = aliased(asked, rows) or asked
    with _lock:
        path = str(_picked_path())
        if _picked.get("from") != path:
            try:
                _picked["model"] = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                _picked["model"] = ""
            _picked["from"] = path
        picked = _picked.get("model", "")
        if real not in served:
            for candidate in (picked, _connected_pick()):
                if candidate in served:
                    return candidate
            return rows[0]["id"] if rows else asked
        if real != picked and _has_tools(body):
            _picked["model"] = real
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(real + "\n", encoding="utf-8")
            except OSError:
                pass
        return real


def request_body(body: Any) -> Any:
    """A Claude Science Messages request with its model read as the one served."""
    if isinstance(body, dict) and "model" in body:
        body = dict(body)
        body["model"] = turn(body.get("model"), body)
    return body


__all__ = ["SCIENCE_ID", "SURFACE", "ALIAS", "is_science", "alias_number", "science_name", "names",
           "models_list", "find_model", "aliased", "turn", "request_body"]
