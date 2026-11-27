"""Claude Desktop on the gateway: the model ids it keeps, and its requests.

Ported from yetone/magpie ``internal/gateway/desktop.go``,
``desktop_list.go`` and ``desktop_subagent.go`` (MIT License, Copyright (c)
yetone). Claude Desktop in its third-party ("3p") gateway mode
(``agents/claude_desktop.py``) lists the gateway's ``/v1/models`` and keeps a
model only when its id reads as Anthropic's (:func:`accepts`), so every
model is shown to it by an id it keeps (:func:`claude_looking`), read back
when it asks for one (:func:`catalog_id`). Its requests are then put on the
model meant (:func:`move`): Claude Code's own ids of a tier on the model
picked for that tier, Desktop's connection check and its title calls on
the chat's model, a subagent turn on the model picked for subagents.

The ids keep magpie's forms with OpenProgram's name in them:
``anthropic/openprogram-<n>`` for a model whose id names no Claude model,
``mythos-openprogram-<n>`` for one with reasoning levels (Desktop's effort
picker), ``openprogram-<n>.anthropic.claude-<tier>-<v>`` for a Claude model
with reasoning levels; ``<n>`` is a hash of the model's id. Nothing here
reads or forwards Desktop's own sign-in: the gateway answers with
OpenProgram's provider keys only.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

AGENT = "claude_desktop"

# Desktop's check of a gateway model id (qo in its app.asar, 2.7032).
_DENIED = re.compile(
    r"ark-code|astron|command-r|deepseek|doubao|gemini|gemma|glm|gpt|grok|hermes|hy3|kimi|lfm|\bling\b|llama|"
    r"longcat|mimo|minimax|mistral|mixtral|moonshot|nemotron|openai|phi-|qianfan|qwen|tc-code|\bunic\b|yi-|"
    r"stepfun|step-3|seed-|bytedance|hunyuan|granite|amazon\.nova|nova-|devstral|ministral|ernie|codex|arcee|"
    r"trinity|abab|phi\d|\bk2\.|\bm2\.|jamba|arctic|solar|mercury|zamba|kat-coder|\bds-|dpsk")
_TIER = re.compile(r"^(sonnet|opus|haiku|fable|mythos)(-[\d.]+)?$")
_WORDS = ("claude", "sonnet", "opus", "haiku", "fable", "mythos", "anthropic")
_UA_VERSION = re.compile(r" Claude/(\d+)")

#: The prefix of a model listed by an alias because its own id names no Claude model.
ALIAS = "anthropic/openprogram-"
#: The prefix of a model with reasoning levels, which Desktop's effort picker matches.
EFFORT_ALIAS = "mythos-openprogram-"
_OWN = "openprogram-"
CLAUDE_INFIX = ".anthropic."
_CLAUDE = re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-\d+(?:-\d+)?$")
_DATED = re.compile(r"-\d{8}$")
_FAMILY = re.compile(r"^claude-(?:[0-9][0-9.-]*-)?(?:opus|sonnet|haiku|fable)(?:[-.@:]|$)")
_DOTTED = re.compile(r"(?i)claude-(?:opus|sonnet|haiku|fable|mythos)-\d+(?:\.\d+)+")
_DASHEDISH = re.compile(r"(?i)claude-(?:opus|sonnet|haiku|fable|mythos)-\d+-\d")

TIER_NAMES = ("opus", "sonnet", "haiku", "fable")
#: The newest Claude model of each tier, the behavesAs of a pick that is none.
BEHAVES_AS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5",
              "fable": "claude-fable-5-2"}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
ONE_M = 1_000_000
TWO_HUNDRED_K = 200_000
ONE_M_SAID = "1M context window"
PROBE_TOKENS = 16
TITLE_PROMPT = "You write short session titles."
_SURFACES = ("code", "cowork", "chat")


# Ids

def accepts(model_id: str) -> bool:
    """Claude Desktop's check of a gateway model id (magpie ``desktopAccepts``)."""
    lowered = model_id.lower()
    if _TIER.match(lowered):
        return True
    if _DENIED.search(lowered):
        return False
    return any(word in lowered for word in _WORDS)


def is_claude_desktop(user_agent: str) -> bool:
    """A request from Desktop's own window (magpie ``isClaudeDesktop``)."""
    return user_agent.startswith("Mozilla/") and " Claude/" in user_agent


def knows_mythos(user_agent: str) -> bool:
    """A Desktop build below 2 drops the mythos word: it is shown plain aliases."""
    found = _UA_VERSION.search(user_agent or "")
    return found is None or int(found.group(1)) >= 2


def alias_number(text: str) -> str:
    """FNV-1a 64 of ``text``, its last ten digits (magpie ``aliasNumber``)."""
    h = 0xcbf29ce484222325
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f"{h % 10_000_000_000:010d}"


def claude_dashed(model_id: str) -> str:
    if "." not in model_id:
        return model_id
    return _DOTTED.sub(lambda m: m.group(0).replace(".", "-"), model_id)


def _split_1m(ref: str) -> tuple[str, str]:
    return (ref[:-4], "[1m]") if ref.endswith("[1m]") else (ref, "")


def _undash_in(served: list[str], model_id: str) -> str:
    found = ""
    for other in served:
        if other == model_id:
            return ""
        if other == found or claude_dashed(other) != model_id:
            continue
        if found:
            return ""
        found = other
    return found


def claude_spelled(ref: str, served: list[str]) -> str:
    """A dotted Claude version dashed, as Claude Code reads it, when unambiguous."""
    model_id, rest = _split_1m(ref)
    dashed = claude_dashed(model_id)
    if dashed != model_id and _undash_in(served, dashed) == model_id:
        return dashed + rest
    return ref


def claude_undashed(ref: str, served: list[str]) -> str:
    model_id, rest = _split_1m(ref)
    if not _DASHEDISH.search(model_id):
        return ""
    real = _undash_in(served, model_id)
    return real + rest if real else ""


def _model_part(row: dict[str, Any]) -> str:
    return row["id"].split("/", 1)[1] if "/" in row["id"] else row["id"]


def _levels(row: dict[str, Any]) -> list[str]:
    return [str(level) for level in row.get("thinking_levels") or [] if level and level != "off"]


def claude_model(row: dict[str, Any]) -> str:
    """The Claude model a row is, or ``""`` (magpie ``claudeModel``)."""
    model = _model_part(row).lower().rsplit("/", 1)[-1]
    model = _DATED.sub("", claude_dashed(model))
    return model if _CLAUDE.match(model) else ""


def claude_looking(row: dict[str, Any], served: list[str]) -> str:
    """The id Desktop is shown a row by (magpie ``claudeLooking``)."""
    number = alias_number(row["id"])
    if _levels(row):
        model = claude_model(row)
        if model:
            return f"{_OWN}{number}{CLAUDE_INFIX}{model}"
        return EFFORT_ALIAS + number
    if accepts(row["id"]) and not row["id"].startswith(ALIAS):
        return claude_spelled(row["id"], served)
    return ALIAS + number


def desktop_number(row: dict[str, Any], served: list[str]) -> str:
    """The number Desktop's menu says beside a Claude model it names after its catalog."""
    looking = claude_looking(row, served)
    if not looking.startswith(_OWN) or CLAUDE_INFIX not in looking:
        return ""
    return looking[len(_OWN):].split(CLAUDE_INFIX, 1)[0]


def _alias_digits(model_id: str) -> str:
    model_id = _split_1m(model_id)[0]
    for prefix in (ALIAS, EFFORT_ALIAS):
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    if model_id.startswith(_OWN):
        rest = model_id[len(_OWN):]
        at = rest.find(CLAUDE_INFIX)
        if at > 0:
            return rest[:at]
    return ""


def _alias_asked(model_id: str) -> bool:
    digits = _alias_digits(model_id)
    return len(digits) == 10 and digits.isdigit()


def _tier_key(model_id: str, tier: str) -> str:
    return model_id + "\x00" + tier


def aliased(model_id: str, rows: list[dict[str, Any]], picks: Mapping[str, str]) -> str:
    """The catalog id an alias stands for, ``""`` for none (magpie ``aliased``)."""
    number = _alias_digits(model_id)
    if len(number) != 10:
        return ""
    ids = [row["id"] for row in rows] + [picks.get(t) or "" for t in TIER_NAMES]
    for candidate in ids:
        if candidate and alias_number(candidate) == number:
            return candidate
    for candidate in ids:
        for tier in TIER_NAMES:
            if candidate and alias_number(_tier_key(candidate, tier)) == number:
                return candidate
    return ""


def catalog_id(model_id: str, rows: Optional[list[dict[str, Any]]] = None,
               picks: Optional[Mapping[str, str]] = None) -> str:
    """The catalog id of a model as Desktop names it (magpie ``unprefixed``), ``[1m]`` kept."""
    rows = _rows() if rows is None else rows
    picks = _picks() if picks is None else picks
    _, mark = _split_1m(model_id)
    real = aliased(model_id, rows, picks)
    if real:
        return real + mark
    served = [row["id"] for row in rows]
    undashed = claude_undashed(model_id, served)
    if undashed:
        return undashed
    if model_id.startswith("anthropic/") and len(model_id) > len("anthropic/"):
        bare = _split_1m(model_id)[0]
        if bare in served or _provider_served(bare, rows):
            return model_id
        return model_id[len("anthropic/"):]
    return model_id


def claude_tier(model_id: str) -> str:
    """The tier a model id is a Claude model of, ``""`` for none."""
    model = _split_1m(model_id)[0].lower().rsplit("/", 1)[-1]
    if not _FAMILY.match(model):
        return ""
    return next((tier for tier in TIER_NAMES if tier in model), "")


# What OpenProgram serves and what is picked

def _picks() -> dict[str, str]:
    """The agent's picks (its model, tiers, subagent model), while it is connected."""
    from . import stash
    try:
        record = stash.entry(AGENT) or {}
    except stash.StashUnreadable:
        return {}
    return dict(record.get("values") or {})


def ordered(rows: list[dict[str, Any]], picks: Mapping[str, str]) -> list[dict[str, Any]]:
    """The models as Desktop is shown them: the picked model first, which
    Desktop starts a chat on (magpie lists the Agents page's order)."""
    model = picks.get("model") or ""
    return sorted(rows, key=lambda row: row["id"] != model)


def _rows() -> list[dict[str, Any]]:
    from .conversion import enabled_model_refs
    return ordered(enabled_model_refs(), _picks())


def _provider_served(model_id: str, rows: list[dict[str, Any]]) -> bool:
    provider = model_id.split("/", 1)[0] if "/" in model_id else ""
    return bool(provider) and any(row.get("provider") == provider for row in rows)


def unserved(asked: str, rows: list[dict[str, Any]]) -> bool:
    """Not a model OpenProgram serves (magpie ``unserved``)."""
    if not asked:
        return False
    model_id = _split_1m(asked)[0]
    if any(model_id in (row["id"], _model_part(row)) for row in rows):
        return False
    return not _provider_served(model_id, rows)


def _serves(model_id: str, rows: list[dict[str, Any]]) -> bool:
    return bool(model_id) and not unserved(model_id, rows)


# The model list

def names(rows: list[dict[str, Any]]) -> list[str]:
    """Each row's name, those that read alike told apart (magpie ``desktopNames``)."""
    out = [str(row.get("name") or row["id"]) for row in rows]

    def count(items: list[str]) -> dict[str, int]:
        same: dict[str, int] = {}
        for item in items:
            same[item.lower()] = same.get(item.lower(), 0) + 1
        return same

    same = count(out)
    short = list(out)
    for i, row in enumerate(rows):
        if same[out[i].lower()] > 1:
            short[i] = f"{out[i]} ({_model_part(row)})"
    still = count(short)
    for i, row in enumerate(rows):
        if short[i] != out[i] and still[short[i].lower()] > 1:
            short[i] = f"{out[i]} ({row['id']})"
    return short


def _mid_window(row: dict[str, Any]) -> bool:
    """Past Desktop's 200K and under 1M, and not a Claude model: listed by its ``[1m]`` id."""
    window = int(row.get("context_window") or 0)
    return TWO_HUNDRED_K < window < ONE_M and not claude_model(row)


def _window_said(row: dict[str, Any]) -> str:
    window = int(row.get("context_window") or 0)
    return ONE_M_SAID if window >= ONE_M else f"{(window + 500) // 1000}K context window"


def tier_of(picks: Mapping[str, str]) -> dict[str, str]:
    """The tier each picked model is tagged with: a model picked for two, the first."""
    out: dict[str, str] = {}
    for tier in TIER_NAMES:
        model = picks.get(tier) or ""
        if model and model not in out:
            out[model] = tier
    return out


def shared_tiers(picks: Mapping[str, str]) -> dict[str, list[str]]:
    """The tiers a model is picked for after the first."""
    first = tier_of(picks)
    more: dict[str, list[str]] = {}
    for tier in TIER_NAMES:
        model = picks.get(tier) or ""
        if model and first.get(model) != tier:
            more.setdefault(model, []).append(tier)
    return more


def models(rows: list[dict[str, Any]], picks: Mapping[str, str], mythos: bool = True) -> list[dict[str, Any]]:
    """``/v1/models`` as Desktop is shown it (magpie ``desktopModels``)."""
    from .models_info import model_object
    served = [row["id"] for row in rows]
    shown = names(rows)
    tiers = tier_of(picks)
    data = []
    for i, row in enumerate(rows):
        entry = model_object(row)
        entry["display_name"] = shown[i]
        model_id = claude_looking(row, served)
        if not mythos and model_id.startswith(EFFORT_ALIAS):
            model_id = ALIAS + alias_number(row["id"])
        said = []
        if _mid_window(row):
            model_id += "[1m]"
            said.append(_window_said(row))
        if desktop_number(row, served) and row.get("provider"):
            # Desktop names it after its own catalog ("Opus 5.5"): the
            # provider goes under the name (magpie desktopSaidBy)
            said.append(str(row["provider"]))
        entry["id"] = model_id
        if said:
            entry["description"] = " · ".join(said)
        tier = tiers.get(row["id"])
        if tier:
            entry["anthropic_family_tier"], entry["is_family_default"] = tier, True
        data.append(entry)
    return data


def tier_alias(row: dict[str, Any], tier: str, served: list[str]) -> str:
    """The id Desktop is shown a model by for a later tier it is picked for."""
    number = alias_number(_tier_key(row["id"], tier))
    looking = claude_looking(row, served)
    if looking.startswith(_OWN):
        rest = looking[len(_OWN):]
        at = rest.find(CLAUDE_INFIX)
        if at > 0:
            return _OWN + number + rest[at:]
    if looking.startswith(EFFORT_ALIAS):
        return EFFORT_ALIAS + number
    return ALIAS + number


def tier_rows(rows: list[dict[str, Any]], data: list[dict[str, Any]], picks: Mapping[str, str]) -> list[dict[str, Any]]:
    """The extra rows that give Desktop a model picked for two tiers for the later ones."""
    more = shared_tiers(picks)
    served = [row["id"] for row in rows]
    out = []
    for row, entry in zip(rows, data):
        for tier in more.get(row["id"], []):
            extra = dict(entry)
            extra["id"] = tier_alias(row, tier, served)
            extra["display_name"] = f"{entry['display_name']} · {tier}"
            extra["anthropic_family_tier"], extra["is_family_default"] = tier, True
            if str(entry["id"]).endswith("[1m]"):
                extra["supports_1m"] = True
            out.append(extra)
    return out


def listing(user_agent: str = "") -> list[dict[str, Any]]:
    """Every row Desktop's ``GET /v1/models`` answers."""
    rows, picks = _rows(), _picks()
    data = models(rows, picks, knows_mythos(user_agent))
    return data + tier_rows(rows, data, picks)


def _runs_as(row: dict[str, Any], model_id: str, tier: str) -> str:
    model = claude_model(row)
    if not model:
        return BEHAVES_AS[tier]
    if model_id.startswith((EFFORT_ALIAS, ALIAS)):
        return model
    return ""


def catalog_named(model_id: str) -> str:
    """The Claude model of Desktop's catalog it names a row of ``model_id`` by."""
    model = _split_1m(model_id)[0].lower()
    if CLAUDE_INFIX in model:
        model = model.split(CLAUDE_INFIX, 1)[1]
    model = _DATED.sub("", model)
    return model if _CLAUDE.match(model) else ""


def _names_clash(data: list[dict[str, Any]]) -> bool:
    seen = set()
    for entry in data:
        named = catalog_named(str(entry["id"]))
        if not named:
            continue
        if named in seen:
            return True
        seen.add(named)
    return False


def inference_models(rows: list[dict[str, Any]], picks: Mapping[str, str]) -> Optional[list[dict[str, Any]]]:
    """The ``inferenceModels`` list for Desktop's profile (magpie
    ``DesktopInferenceModels``): ``None`` while no listed model is picked for
    a tier and no two rows read alike, when Desktop reads ``/v1/models``."""
    tiers = tier_of(picks)
    data = models(rows, picks, True)
    tagged = False
    out = []
    for row, entry in zip(rows, data):
        name, long = _split_1m(str(entry["id"]))
        item: dict[str, Any] = {"name": name, "labelOverride": entry["display_name"]}
        if long:
            item["supports1m"], item["prefer1m"] = True, True
        elif int(row.get("context_window") or 0) >= ONE_M:
            item["supports1m"] = True
        tier = tiers.get(row["id"])
        if tier:
            tagged = True
            item["anthropicFamilyTier"], item["isFamilyDefault"] = tier, True
            behaves = _runs_as(row, name, tier)
            if behaves:
                item["behavesAs"] = behaves
        out.append(item)
    if not tagged and not _names_clash(data):
        return None
    return out


def discovers(picks: Mapping[str, str]) -> bool:
    """Desktop reads ``/v1/models`` beside ``inferenceModels`` while a model is picked for two tiers."""
    return bool(shared_tiers(picks))


# Requests

def has_tools(body: dict[str, Any]) -> bool:
    return isinstance(body.get("tools"), list) and len(body["tools"]) > 0


def small(body: dict[str, Any]) -> bool:
    tokens = body.get("max_tokens")
    return isinstance(tokens, int) and 0 < tokens <= 4096


def probe(body: dict[str, Any]) -> bool:
    """Desktop's check that the gateway answers: one user message ".", one token."""
    messages = body.get("messages")
    if body.get("max_tokens") != 1 or body.get("system") or body.get("tools"):
        return False
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        return False
    return messages[0].get("role") == "user" and messages[0].get("content") == "."


def title_call(body: dict[str, Any]) -> bool:
    """Claude Code's session-title call."""
    system = body.get("system")
    if isinstance(system, str):
        return system.startswith(TITLE_PROMPT)
    if isinstance(system, list):
        return any(isinstance(b, dict) and str(b.get("text") or "").startswith(TITLE_PROMPT) for b in system)
    return False


def search_call(body: dict[str, Any]) -> bool:
    """Claude Code's WebSearch helper call: one web_search tool, chosen."""
    tools, choice = body.get("tools"), body.get("tool_choice")
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict) or not isinstance(choice, dict):
        return False
    tool = tools[0]
    if not str(tool.get("type") or "").startswith("web_search_") or tool.get("name") != "web_search":
        return False
    if choice.get("type") in ("auto", "any"):
        return True
    return choice.get("type") == "tool" and choice.get("name") == "web_search"


def subagent_of(headers: Mapping[str, str]) -> str:
    return (headers.get("x-claude-code-agent-id") or "").strip()[:128]


def claude_code_asks(headers: Mapping[str, str]) -> bool:
    return (headers.get("user-agent") or "").startswith("claude-cli/")


_picked_lock = threading.Lock()
_picked: dict[str, Any] = {"model": "", "from": "", "at": 0.0}


def _picked_path() -> Path:
    from .keys import gateway_dir
    return gateway_dir() / "claude-desktop.model"


def _load_picked() -> None:
    path = str(_picked_path())
    if _picked["from"] == path:
        return
    try:
        model = Path(path).read_text(encoding="utf-8").strip()
        at = Path(path).stat().st_mtime
    except OSError:
        model, at = "", 0.0
    _picked.update(model=model, at=at)
    _picked["from"] = path


def _remember_picked(model: str) -> None:
    _picked.update(model=model, at=time.time())
    try:
        path = _picked_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model + "\n", encoding="utf-8")
    except OSError:
        pass


def selector_roots() -> list[Path]:
    """Desktop's 3p folders, where its model menus keep their picks."""
    from .agents.claude_desktop import desktop_dirs, isolated_dir
    roots = [desktop_dirs()[2]]
    copy = isolated_dir()
    if copy is not None and copy.is_dir():
        roots.append(copy)  # the copy in a folder of its own
    return roots


def chosen(asked: str, rows: list[dict[str, Any]], picks: Mapping[str, str]) -> str:
    """The model picked in Desktop's menus (``cowork_account_settings.json``)."""
    newest, path = 0.0, None
    for root in selector_roots():
        for found in root.glob("local-agent-mode-sessions/*/*/cowork_account_settings.json"):
            try:
                mtime = found.stat().st_mtime
            except OSError:
                continue
            if found.is_file() and (path is None or mtime > newest):
                path, newest = found, mtime
    if path is None:
        return ""
    try:
        state = json.loads(path.read_text(encoding="utf-8")).get("__model_selector_state") or {}
    except (OSError, ValueError, AttributeError):
        return ""
    picked_in: list[str] = []
    for surface in _SURFACES:
        model = catalog_id(str((state.get(surface) or {}).get("model") or "").strip(), rows, picks)
        if model and _serves(model, rows) and model not in picked_in:
            picked_in.append(model)
    if not picked_in:
        return ""
    if _split_1m(catalog_id(asked, rows, picks))[0] == _split_1m(picked_in[0])[0]:
        return asked
    with _picked_lock:
        _load_picked()
        last, at = _picked["model"], _picked["at"]
    if last and at > newest:
        model = catalog_id(last, rows, picks)
        if _serves(model, rows):
            return model
    return picked_in[0]


def default(asked: str, rows: list[dict[str, Any]]) -> str:
    """The first model Desktop is shown, when it isn't the one asked."""
    if not rows or rows[0]["id"] == asked:
        return ""
    return rows[0]["id"]


def claude_own_id(asked: str, rows: list[dict[str, Any]]) -> bool:
    """Claude Code's own id of a Claude model, not one OpenProgram lists."""
    bare = _split_1m(asked)[0]
    if "/" in bare or not claude_tier(bare):
        return False
    return all(row["id"] != bare for row in rows)


def served_beside(asked: str, picked: str, rows: list[dict[str, Any]], by_claude_code: bool) -> str:
    """Claude Code's own id of a Claude model, on the chat's provider (magpie ``servedBeside``)."""
    bare, mark = _split_1m(asked)
    tier = claude_tier(bare)
    if not picked or "/" in bare or not tier:
        return ""
    picked = _split_1m(picked)[0]
    provider, folder, chat_claude = "", "", False
    for row in rows:
        if picked in (row["id"], _model_part(row)):
            provider, chat_claude = row.get("provider") or "", bool(claude_model(row))
            model = _model_part(row)
            folder = model[:model.rfind("/") + 1] if "/" in model else ""
            break
    if not provider or by_claude_code and not chat_claude:
        return ""
    found, tier_id, tier_model, tier_beside = "", "", "", False
    for row in rows:
        if row.get("provider") != provider:
            continue
        model = _model_part(row)
        if model == bare or model.endswith("/" + bare):
            if model == bare or folder and model == folder + bare:
                return row["id"] + mark
            found = found or row["id"] + mark
            continue
        own = claude_model(row)
        if not own or claude_tier(own) != tier:
            continue
        beside = bool(folder) and model.startswith(folder)
        if not tier_id or beside and not tier_beside or beside == tier_beside and own > tier_model:
            tier_id, tier_model, tier_beside = row["id"], own, beside
    if found:
        return found
    return tier_id + mark if tier_id else ""


def _search_tier(rows: list[dict[str, Any]], picks: Mapping[str, str]) -> str:
    model = picks.get("haiku") or ""
    return model if model and not unserved(model, rows) else ""


def _tier_turn(asked: str, rows: list[dict[str, Any]], picks: Mapping[str, str]) -> str:
    if not unserved(asked, rows) or "/" in asked:
        return ""
    tier = claude_tier(asked)
    if not tier and _alias_asked(asked) and asked.startswith(_OWN):
        tier = claude_tier(_split_1m(asked)[0].split(CLAUDE_INFIX, 1)[-1])
    return (picks.get(tier) or "") if tier else ""


def _tier_asked(asked: str, picks: Mapping[str, str]) -> bool:
    model = _split_1m(asked)[0]
    return any(picks.get(tier) == model for tier in TIER_NAMES if picks.get(tier))


def move(asked: str, body: dict[str, Any], *, by_claude_code: bool, by_subagent: bool,
         rows: Optional[list[dict[str, Any]]] = None, picks: Optional[Mapping[str, str]] = None) -> str:
    """The model a Desktop request runs on (magpie ``desktopMoveTo``)."""
    rows = _rows() if rows is None else rows
    picks = _picks() if picks is None else picks
    if not asked:
        return asked
    if by_claude_code and search_call(body):
        haiku = _search_tier(rows, picks)
        if haiku:
            return haiku
    to = _tier_turn(asked, rows, picks)
    if to:
        return to
    if probe(body):
        to = chosen(asked, rows, picks)
        if to:
            return to
        to = default(asked, rows)
        return to if to and _serves(to, rows) else asked
    tools = has_tools(body)
    with _picked_lock:
        _load_picked()
        picked = _picked["model"]
        if asked == picked:
            if tools and not by_subagent:
                _picked["at"] = time.time()
            return asked
        aside = title_call(body) if by_claude_code else small(body)
        title = not tools and aside and accepts(asked) and not _tier_asked(asked, picks)
        gone = unserved(asked, rows)
        if picked and (gone or title or ((not tools or by_subagent) and claude_own_id(asked, rows))) \
                and not _serves(picked, rows):
            picked = ""
        elsewhere = not gone and not title and bool(picked) and (not tools or by_subagent) and claude_own_id(asked, rows)
        if elsewhere and picks.get(claude_tier(asked)):
            return picks[claude_tier(asked)]
        if (gone or elsewhere) and not title:
            to = served_beside(asked, picked, rows, by_claude_code)
            if to:
                return to
        if elsewhere:
            return picked
        if gone or title:
            return picked or default(asked, rows) or asked
        if tools and not by_subagent:
            _remember_picked(asked)
        return asked


def shape(body: Any, headers: Mapping[str, str]) -> Any:
    """A Desktop Messages request put on the model meant, before it is read."""
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        return body
    rows, picks = _rows(), _picks()
    asked = catalog_id(body["model"], rows, picks)
    sub = subagent_of(headers)
    is_probe = probe(body)
    to = move(asked, body, by_claude_code=claude_code_asks(headers), by_subagent=bool(sub), rows=rows, picks=picks)
    body = dict(body)
    if is_probe:
        # its check, given room every model answers in
        body["max_tokens"] = max(int(body.get("max_tokens") or 0), PROBE_TOKENS)
    helper = search_call(body) and bool(_search_tier(rows, picks))
    if sub and has_tools(body) and not helper:
        # every subagent turn on the model picked for subagents (magpie desktopSubagentPick)
        pick = picks.get("subagent_model") or ""
        if pick and _serves(pick, rows):
            to = pick
        effort = picks.get("subagent_effort") or ""
        if effort in EFFORTS:
            config = dict(body.get("output_config") or {}) if isinstance(body.get("output_config"), dict) else {}
            config["effort"] = effort
            body["output_config"] = config
    body["model"] = _split_1m(to)[0]
    return body
