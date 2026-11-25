""""Also in the terminal": the gateway written into a harness's own config.

Codex (``$CODEX_HOME/config.toml``, default ``~/.codex``): ``model_provider =
"openprogram"`` and ``model = "<provider>/<model>"`` at the top level, and a
``[model_providers.openprogram]`` table with the gateway's ``base_url``,
``wire_api = "responses"`` and the gateway key as
``experimental_bearer_token``. Turning it off puts ``model_provider`` and
``model`` back; the table stays, because Codex cannot reopen a thread started
on a provider whose table is gone.

The Codex app reads the same ``config.toml``: ``model_catalog_json`` names
the gateway's models for its picker, and its app-server is restarted when the
route changes (``codex_app.py``).

Claude Code (``$CLAUDE_CONFIG_DIR/settings.json``, default ``~/.claude``):
``env.ANTHROPIC_BASE_URL``, ``env.ANTHROPIC_AUTH_TOKEN``, the tier variables
and the top-level ``model``; ``CLAUDE_CODE_USE_BEDROCK`` / ``_VERTEX`` /
``_FOUNDRY`` are removed while it is on. ``ANTHROPIC_MODEL`` is never set: it
would override the model picked with ``/model``.

Originals go to the stash (``stash.py``) before the first write and come
back on turning off. Nothing else is added to the harness's config: no MCP
servers, permissions or instructions.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import stash
from .config_files import (
    ConfigText,
    ConfigUnreadable,
    delete_path,
    get_path,
    json_dump_like,
    json_load,
    set_path,
    toml_edit,
    toml_load,
)

HARNESSES = ("codex", "claude_code")
PROVIDER_ID = "openprogram"
PROVIDER_TABLE = f"model_providers.{PROVIDER_ID}"
TIER_VARS = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
CLOUD_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class HarnessConfigError(Exception):
    """A harness config was left alone; the message says why."""


def config_path(harness: str) -> Path:
    if harness == "codex":
        root = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        return Path(root).expanduser() / "config.toml"
    if harness == "claude_code":
        root = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
        return Path(root).expanduser() / "settings.json"
    raise ValueError(f"unknown harness {harness!r}")


def strip_ref(model_ref: str) -> str:
    """``provider/model`` from a ref that may carry ``openprogram:``."""
    from .conversion import REF_PREFIX
    value = (model_ref or "").strip()
    return value[len(REF_PREFIX):] if value.startswith(REF_PREFIX) else value


def claude_code_env(model_ref: str, tiers: dict[str, str] | None, *, base_url: str, token: str) -> dict[str, str]:
    """The Claude Code environment that sends its model calls to the gateway."""
    picked = strip_ref(model_ref)
    env = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": token}
    for tier, name in TIER_VARS.items():
        env[name] = strip_ref((tiers or {}).get(tier) or "") or picked
    env["ANTHROPIC_SMALL_FAST_MODEL"] = env["ANTHROPIC_DEFAULT_HAIKU_MODEL"]
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = picked
    return env


def _desired(harness: str, model_ref: str, tiers: dict[str, str] | None) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """``(sets, deletes, table values)`` for turning the gateway on."""
    from .address import anthropic_base_url, openai_base_url
    from .keys import gateway_key
    if harness == "codex":
        return ({"model_provider": PROVIDER_ID, "model": strip_ref(model_ref)}, [],
                {"name": "OpenProgram", "base_url": openai_base_url(stable=True),
                 "wire_api": "responses", "experimental_bearer_token": gateway_key()})
    env = claude_code_env(model_ref, tiers, base_url=anthropic_base_url(stable=True), token=gateway_key())
    sets: dict[str, Any] = {f"env.{name}": value for name, value in env.items()}
    sets["model"] = strip_ref(model_ref)
    return sets, [f"env.{name}" for name in CLOUD_SWITCHES], {}


def _load(harness: str, text: ConfigText) -> dict[str, Any]:
    where = str(text.path)
    return toml_load(text.text, where) if harness == "codex" else json_load(text.text, where)


def _value(doc: dict[str, Any], key: str) -> tuple[bool, Any]:
    return get_path(doc, key)


def _write(harness: str, text: ConfigText, sets: dict[str, Any], deletes: list[str],
           table_values: dict[str, Any] | None = None) -> None:
    if harness == "codex":
        edited = toml_edit(text.text, sets=sets, deletes=deletes,
                           table=PROVIDER_TABLE if table_values else None,
                           table_values=table_values, where=str(text.path))
    else:
        data = json_load(text.text, str(text.path))
        for key, value in sets.items():
            set_path(data, key, value)
        for key in deletes:
            delete_path(data, key)
        if data.get("env") == {} and "env" in deletes:
            data.pop("env")
        edited = json_dump_like(text.text, data)
    if edited != text.text:
        text.write(edited)


def apply(harness: str, model_ref: str, tiers: dict[str, str] | None = None,
          *, codex_versions: list[str] | None = None) -> dict[str, Any]:
    """Point the harness's own config at the gateway with ``model_ref``.

    ``codex_versions``: the installed Codex's version, then the newest
    release, whose base instructions the Codex app's model catalog carries."""
    if not strip_ref(model_ref):
        raise HarnessConfigError("pick a model first")
    path = config_path(harness)
    try:
        text = ConfigText.read(path)
        doc = _load(harness, text)
        sets, deletes, table_values = _desired(harness, model_ref, tiers)
        was_applied = stash.entry(harness) is not None
        if harness == "codex":
            # The Codex app lists models from a catalog, not from config.toml (codex_app.py).
            from .codex_app import write_catalog
            catalog = write_catalog(path.parent, strip_ref(model_ref), codex_versions or [])
            if catalog is not None:
                sets["model_catalog_json"] = str(catalog)
        deletes = [key for key in deletes if _value(doc, key)[0]]
        originals = {key: _value(doc, key) for key in [*sets, *deletes]}
        if harness == "claude_code" and not _value(doc, "env")[0]:
            originals["env"] = (False, None)
        stash.remember(harness, str(path), originals)
        _write(harness, text, sets, deletes, table_values)
    except ConfigUnreadable as exc:
        raise HarnessConfigError(f"{exc}; it was left unchanged") from exc
    record = stash.entry(harness) or {}
    previous = record.get("wrote") or {}
    wrote = {key: {"set": value} for key, value in sets.items()}
    wrote.update({key: {"deleted": True} for key in deletes})
    # A switch removed in an earlier apply stays removed by us.
    wrote.update({key: value for key, value in previous.items() if value.get("deleted") and key not in wrote})
    stash.wrote(harness, wrote)
    if harness == "codex" and not was_applied:
        from .codex_app import restart_app
        restart_app(path.parent)
    return status(harness)


def _ours(written: dict[str, Any] | None, present: bool, current: Any) -> bool:
    if not written:
        return False
    if written.get("deleted"):
        return not present
    return present and current == written.get("set")


def restore(harness: str) -> dict[str, Any]:
    """Put back the values OpenProgram replaced, except ones changed since."""
    record = stash.entry(harness)
    if record is None:
        return status(harness)
    path = Path(record.get("path") or config_path(harness))
    try:
        text = ConfigText.read(path)
        doc = _load(harness, text)
        sets: dict[str, Any] = {}
        deletes: list[str] = []
        for key, original in (record.get("originals") or {}).items():
            present, current = _value(doc, key)
            if not _ours((record.get("wrote") or {}).get(key), present, current):
                continue
            if original.get("had"):
                sets[key] = original.get("value")
            elif present:
                deletes.append(key)
        if harness == "claude_code":
            env_original = (record.get("originals") or {}).get("env")
            if env_original and not env_original.get("had"):
                remaining = {k: v for k, v in (doc.get("env") or {}).items() if f"env.{k}" not in deletes}
                if not remaining and isinstance(doc.get("env"), dict):
                    deletes.append("env")
        if sets or deletes:
            _write(harness, text, sets, deletes)
    except ConfigUnreadable as exc:
        raise HarnessConfigError(f"{exc}; the original settings are kept to restore later") from exc
    stash.forget(harness)
    if harness == "codex":
        from .codex_app import remove_catalog, restart_app
        remove_catalog(path.parent)
        restart_app(path.parent)
    return status(harness)


def restore_all() -> list[str]:
    """Restore every harness; the messages of those that were left alone."""
    errors = []
    for harness in HARNESSES:
        try:
            restore(harness)
        except HarnessConfigError as exc:
            errors.append(f"{harness}: {exc}")
    return errors


def status(harness: str) -> dict[str, Any]:
    """Whether the gateway is written into the harness's config, and drift."""
    record = stash.entry(harness)
    path = Path((record or {}).get("path") or config_path(harness))
    result: dict[str, Any] = {"path": str(path), "applied": record is not None, "drift": [], "error": None}
    if record is None:
        return result
    try:
        doc = _load(harness, ConfigText.read(path))
    except ConfigUnreadable as exc:
        result["error"] = str(exc)
        return result
    for key, written in (record.get("wrote") or {}).items():
        present, current = _value(doc, key)
        if not _ours(written, present, current):
            result["drift"].append(key)
    if harness == "codex":
        table, _ = _value(doc, PROVIDER_TABLE)
        if not table:
            result["drift"].append(PROVIDER_TABLE)
    return result
