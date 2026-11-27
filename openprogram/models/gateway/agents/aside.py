"""Aside (the ``aside`` command and app): one folder per account,
``~/.aside/u/<id>``, each with Pi's ``settings.json`` and ``models.json``.

After yetone/magpie ``internal/agent/aside.go``, ``aside_account.go``,
``aside_stage.go`` and ``aside_native.go`` (MIT License, Copyright (c)
yetone). The account written is the one picked here (the ``account``
choice, kept in ``aside-account.json`` beside the stash, :func:`pick`;
offered while Aside has more than one), else the one Aside
has active, ``~/.aside/accounts.json``'s ``currentAccountId``, else ``u0``:
a missing or unreadable accounts.json says nothing about which is active,
and an account whose folder isn't there is passed over, so none is made up.
Each account's files are its own, and what OpenProgram wrote to one stays
recorded with it.

The model is ``defaultModel`` in settings.json, an object
``{"provider", "modelId", "thinkingLevel"}``; Aside's roles (fast, standard,
deep, visual) are ``modelCategories.<role>`` in the same shape, each
written only when picked, and following the main model while unset. A
selection keeps its other fields, its thinking level too (``medium`` where
it had none); the level picked here is the main model's. OpenProgram's models go in models.json as the provider
``openprogram``, Pi's block with a key that names Aside.

The files are written first, which Aside reads at its next start (magpie's
offline ``stage``); after each change the running Aside is given the same
settings live through ``aside repl --account uN --host local``
(``aside.settings.set``, magpie ``aside_native.go``), and only when that
can't be done is a restart asked for.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Agent, Field, Pick, Wiring, Write
from .engine import AgentConfigError
from .pi_like import PI_LEVELS, PROVIDER_ID, block_kept, level_options, provider_block, read_doc

ROLES = ("fast", "standard", "deep", "visual")


def root() -> Path:
    return Path.home() / ".aside"


def _account_id(raw: Any) -> int | None:
    """An account id as Aside writes it (1), or as typed ("u1", "1") (magpie ``asideID``)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        text = raw.strip().removeprefix("u")
        return int(text) if text.isdigit() else None
    return None


def pick_path() -> Path:
    """Where the account picked here is kept (magpie ``asidePickPath``)."""
    from ..keys import gateway_dir
    return gateway_dir() / "aside-account.json"


def picked() -> int | None:
    """The account picked here; ``None`` when none was (magpie ``asidePicked``)."""
    try:
        data = json.loads(pick_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _account_id(data.get("account")) if isinstance(data, dict) else None


def pick(value: str) -> None:
    """Keep ``value`` (``u1``) as the account written; ``""`` follows Aside's
    own again (magpie ``pickAccount``). Nothing in Aside changes."""
    value = value.strip()
    if not value:
        pick_path().unlink(missing_ok=True)
        return
    chosen = _account_id(value)
    if chosen is None or not (root() / "u" / str(chosen)).is_dir():
        raise ValueError(f"Aside has no account {value}")
    pick_path().parent.mkdir(parents=True, exist_ok=True)
    pick_path().write_text(json.dumps({"account": f"u{chosen}"}) + "\n", encoding="utf-8")


def account() -> int:
    """The account written: the one picked here, else the one Aside has
    active, else 0 (magpie ``asideChoice``)."""
    chosen = picked()
    if chosen is not None and (root() / "u" / str(chosen)).is_dir():
        return chosen
    try:
        data = json.loads((root() / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    current = _account_id(data.get("currentAccountId")) if isinstance(data, dict) else None
    if current is not None and (root() / "u" / str(current)).is_dir():
        return current
    return 0


def folder() -> Path:
    return root() / "u" / str(account())


def config_path() -> Path:
    return folder() / "settings.json"


def _selection(before: Any, model: str, *, thinking: bool = True) -> dict[str, Any]:
    out = dict(before) if isinstance(before, dict) else {}
    if thinking:
        out.setdefault("thinkingLevel", "medium")
    out["provider"], out["modelId"] = PROVIDER_ID, model
    return out


def accounts() -> tuple[int | None, list[tuple[int, str]]]:
    """The account Aside has active and its accounts with their emails, from
    ``accounts.json`` (magpie ``asideAccounts``)."""
    try:
        data = json.loads((root() / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    if not isinstance(data, dict):
        return None, []
    found = {}
    for entry in data.get("accounts") or []:
        account_id = _account_id(entry.get("id")) if isinstance(entry, dict) else None
        if account_id is not None:
            email = entry.get("email")
            found[account_id] = email if isinstance(email, str) and email else "Local Account"
    return _account_id(data.get("currentAccountId")), sorted(found.items())


def account_options() -> list[tuple[str, str]]:
    """Aside's accounts to pick from, none while it has one (magpie
    ``asideAccountOptions``); unpicked, the one Aside has active is written."""
    active, listed = accounts()
    if len(listed) < 2:
        return []
    return [(f"u{i}", f"u{i} · {who}" + (" · active in Aside" if i == active else "")) for i, who in listed]


def _wire(at: Wiring) -> list[Write]:
    try:
        pick(at.values.get("account", ""))
    except ValueError as exc:
        raise AgentConfigError(str(exc)) from exc
    models = folder() / "models.json"
    settings = read_doc(config_path())
    block = block_kept(read_doc(models), provider_block(at))
    selection = _selection(settings.get("defaultModel"), at.model)
    if at.values.get("effort") in PI_LEVELS:
        selection["thinkingLevel"] = at.values["effort"]
    sets: dict[str, Any] = {"defaultModel": selection}
    categories = settings.get("modelCategories") if isinstance(settings.get("modelCategories"), dict) else {}
    for role in ROLES:
        if at.values.get(role):
            sets[f"modelCategories.{role}"] = _selection(categories.get(role), at.values[role])
    # Aside's own picker rewrites each selection: one still on OpenProgram
    # goes back on disconnect, one the user moved off it stays (magpie's
    # plan restores by the provider)
    picks = tuple(Pick((key,), f"{key}.provider", PROVIDER_ID) for key in sets)
    key = f"providers.{PROVIDER_ID}"
    return [Write(models, "json", sets={key: block}, owns=(key,), listing=True),
            Write(config_path(), "json", sets=sets, picks=picks)]


# The running Aside (aside_native.go)

OK = "OPENPROGRAM_ASIDE_OK"
TIMEOUT = 10
#: per account, whether the running Aside took the last change live
_live: dict[int, bool] = {}


def repl(account_id: int, expr: str) -> bool:
    """Run ``expr`` in the account's running Aside; whether it ran (magpie ``asideSet``)."""
    import subprocess
    from .base import find_tool
    command = find_tool("aside")
    if not command:
        return False
    try:
        out = subprocess.run([command, "repl", "--account", f"u{account_id}", "--host", "local",
                              f"{expr}console.log({json.dumps(OK)})"],
                             capture_output=True, text=True, timeout=TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return OK in (out or "")


def live_expr(settings: dict[str, Any], roles: list[str]) -> str:
    """The settings OpenProgram writes as ``aside.settings.set`` calls:
    ``defaultModel``, and each role in ``roles`` set in or taken out of
    ``modelCategories`` (magpie ``setSetting``)."""
    expr = f"aside.settings.set('defaultModel',{json.dumps(settings.get('defaultModel'))});"
    if roles:
        categories = settings.get("modelCategories") if isinstance(settings.get("modelCategories"), dict) else {}
        expr += "const c=aside.settings.get('modelCategories')||{};"
        for role in roles:
            if role in categories:
                expr += f"c[{json.dumps(role)}]={json.dumps(categories[role])};"
            else:
                expr += f"delete c[{json.dumps(role)}];"
        expr += "aside.settings.set('modelCategories',c);"
    return expr


def _after(connected: bool, record: dict[str, Any]) -> None:
    """Give the running Aside of each account written what its settings.json
    now holds; where that can't be done its next start reads the file."""
    for path, kept in (record.get("files") or {}).items():
        settings_path = Path(path)
        if settings_path.name != "settings.json" or settings_path.parent.parent != root() / "u":
            continue
        account_id = _account_id(settings_path.parent.name)
        if account_id is None:
            continue
        roles = [key.split(".", 1)[1] for key in (kept.get("wrote") or {}) if key.startswith("modelCategories.")]
        try:
            settings = read_doc(settings_path)
        except Exception:  # noqa: BLE001 - a file that can't be read is left to Aside
            continue
        _live[account_id] = repl(account_id, live_expr(settings, roles))


def _notice() -> str:
    from .running import any_running
    if _live.get(account()) is not True and any_running(r"(^|/)aside( |$)", r"Aside\.app/"):
        return "Aside reads these settings at its next start — restart Aside to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="aside", name="Aside", icon="aside", kind="cli", protocol="openai-chat", bin="aside",
        dir=root, path=config_path, named_key=True, before_copy=True, wire=_wire, notice=_notice, after=_after,
        fields=[Field("model", "Model"), Field("effort", "Thinking", quiet=True, options=level_options),
                *(Field(role, role.capitalize(), quiet=True, follows="model") for role in ROLES),
                # which of Aside's accounts is written; unpicked, the one it has active
                Field("account", "Account", quiet=True, options=account_options)],
    )
