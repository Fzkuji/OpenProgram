"""Single source of truth for user-editable settings.

Every surface that views or edits settings — the ``setup`` CLI sections,
the ``openprogram ports`` command, the TUI settings screen, the web pages
— renders from the one ``SETTINGS`` list here and writes through
``set_setting`` instead of poking the config dict directly. Adding a
setting = one ``SettingSpec``, and it shows up everywhere.

Modelled on openclaw's ``parseConfigPath`` / ``setConfigValueAtPath``
(dot-path access with a prototype-pollution blocklist) and opencode's
typed config service. Per-setting ``apply`` says whether a change takes
effect immediately (``"live"``) or only on the next worker/web start
(``"next_start"``), so the editor can tell the user the truth instead of
implying everything is instant.

See ``docs/design/cli/cli-redesign.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from openprogram import setup as _setup

# openclaw's isBlockedObjectKey — never let a dot-path write reach these.
_BLOCKED_KEYS = frozenset({"__proto__", "constructor", "prototype"})

APPLY_LIVE = "live"
APPLY_NEXT_START = "next_start"


@dataclass(frozen=True)
class SettingSpec:
    key: str                              # stable id, e.g. "ui.port"
    path: tuple[str, ...]                 # dot-path into config.json
    group: str                            # "Ports" | "Memory" | ...
    label: str
    widget: str                           # "number" | "toggle" | "enum"
    apply: str                            # APPLY_LIVE | APPLY_NEXT_START
    default: Any = None
    choices: Optional[Callable[[], list[str]]] = None   # enum options, lazy
    validate: Optional[Callable[[Any], Optional[str]]] = None  # -> error|None
    help: str = ""
    secret: bool = False


# ── validators / choice providers ─────────────────────────────────────────────


def _validate_port(v: Any) -> Optional[str]:
    try:
        p = int(v)
    except (TypeError, ValueError):
        return "must be a whole number"
    if not 1 <= p <= 65535:
        return "must be in 1–65535"
    return None


def _search_choices() -> list[str]:
    """``auto`` + every registered web_search provider. Best-effort: an
    import failure degrades to just ``auto`` rather than breaking the
    whole settings read."""
    try:
        from openprogram.functions.tools.web_search.registry import registry as _wsr
        import openprogram.functions.tools.web_search.providers  # noqa: F401
        names = [getattr(p, "name", "") for p in _wsr.all()]
        return ["auto"] + [n for n in names if n]
    except Exception:
        return ["auto"]


def _claude_account_choices() -> list[str]:
    """``''`` (follow terminal login) + each saved Claude account, so the
    TUI/settings constrain the active account to real ones (same as the web
    panel) instead of accepting a free-text name that may not exist."""
    try:
        from openprogram.providers.anthropic._meridian_cli import (
            _parse_accounts, _proxy_bin,
        )
        if not _proxy_bin():
            return [""]
        return [""] + [a["name"] for a in _parse_accounts()]
    except Exception:
        return [""]


def _coerce(widget: str, value: Any) -> Any:
    if widget == "number":
        return int(value)
    if widget == "toggle":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    return str(value)


# ── the registry ──────────────────────────────────────────────────────────────

SETTINGS: list[SettingSpec] = [
    SettingSpec(
        key="ui.port", path=("ui", "port"), group="Ports",
        label="Backend port", widget="number",
        apply=APPLY_NEXT_START, default=_setup.DEFAULT_BACKEND_PORT,
        validate=_validate_port,
        help="The port the API + WebSocket backend (FastAPI worker) listens "
             "on. The web UI and TUI both talk to it here. Rebinds on the "
             "next start.",
    ),
    SettingSpec(
        key="ui.web_port", path=("ui", "web_port"), group="Ports",
        label="Frontend port", widget="number",
        apply=APPLY_NEXT_START, default=_setup.DEFAULT_WEB_PORT,
        validate=_validate_port,
        help="The port the web UI itself is served on — the address you open "
             "in the browser. Must differ from the backend port.",
    ),
    SettingSpec(
        key="ui.open_browser", path=("ui", "open_browser"), group="Ports",
        label="Auto-open browser", widget="toggle",
        apply=APPLY_NEXT_START, default=True,
        help="When you run `openprogram web`, also pop open a browser window "
             "pointed at the UI. Turn off to start the server only and open "
             "the address yourself (e.g. on a headless server).",
    ),
    SettingSpec(
        key="search.default_provider", path=("search", "default_provider"),
        group="Search", label="Default web-search provider", widget="enum",
        apply=APPLY_LIVE, default="auto", choices=_search_choices,
        help="`auto` picks the highest-priority configured provider.",
    ),
    SettingSpec(
        key="memory.backend", path=("memory", "backend"), group="Memory",
        label="Memory backend", widget="enum", apply=APPLY_NEXT_START,
        default="local", choices=lambda: ["local", "none"],
        help="`local` = on-disk memory tool; `none` = disabled.",
    ),
    SettingSpec(
        key="claude_code.account",
        path=("providers", "claude-code", "meridian_profile"),
        group="Claude Code", label="Active Claude account",
        widget="enum", apply=APPLY_LIVE, default="",
        choices=_claude_account_choices,
        help="Which saved Claude account claude-code runs on, independent of "
             "the terminal `claude auth login`. Empty = follow the terminal "
             "login. Add accounts with `openprogram providers claude-code "
             "accounts add`.",
    ),
]

_BY_KEY = {s.key: s for s in SETTINGS}


# ── dot-path access (openclaw-style, blocklist-guarded) ───────────────────────


def _get_at(cfg: dict, path: tuple[str, ...], default: Any) -> Any:
    node: Any = cfg
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node if node is not None else default


def _set_at(cfg: dict, path: tuple[str, ...], value: Any) -> None:
    # Reject a blocked key ANYWHERE in the path, not only the segment being
    # traversed — a non-terminal __proto__/constructor/prototype must never
    # slip through if path construction is ever relaxed.
    for k in path:
        if k in _BLOCKED_KEYS:
            raise ValueError(f"blocked config key: {k}")
    node = cfg
    for k in path[:-1]:
        nxt = node.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            node[k] = nxt
        node = nxt
    node[path[-1]] = value


# ── public API ────────────────────────────────────────────────────────────────


def get_settings() -> list[dict]:
    """Resolved current settings for every spec, ready to render.

    Reads the config once; each row carries its value, group, label,
    widget, apply mode, resolved choices (for enums), and help. Secret
    values are returned as a bool ``set`` flag, never the value.
    """
    cfg = _setup._read_config()
    rows: list[dict] = []
    for s in SETTINGS:
        raw = _get_at(cfg, s.path, s.default)
        row: dict = {
            "key": s.key,
            "group": s.group,
            "label": s.label,
            "widget": s.widget,
            "apply": s.apply,
            "help": s.help,
        }
        if s.secret:
            row["set"] = bool(raw)
        else:
            row["value"] = raw
        if s.choices is not None:
            try:
                row["choices"] = list(s.choices())
            except Exception:
                row["choices"] = []
        rows.append(row)

    # Providers are read-only status rows (✓ configured / ✗ not) with an
    # action — selecting one runs ``/login``. check_providers() is cheap
    # (~1ms; env + which checks). The web already has a full Providers tab;
    # this is the at-a-glance status for the TUI/CLI.
    try:
        from openprogram.providers.registry import check_providers
        for name, st in check_providers().items():
            ok = bool(st.get("available"))
            rows.append({
                "key": f"providers.{name}",
                "group": "Providers",
                "label": name,
                "widget": "status",
                "apply": APPLY_LIVE,
                "help": f"{st.get('method', '')} · {'configured' if ok else 'not configured'}",
                "value": ok,
                "action": "/login",
            })
    except Exception:
        pass

    # Tools are dynamic — one live toggle per registered tool, ``on`` when
    # the user hasn't disabled it. Keyed ``tools.disabled.<name>`` so
    # set_setting can flip membership of ``tools.disabled``.
    try:
        from openprogram.functions import list_registered_agent_tools
        disabled = set((cfg.get("tools", {}) or {}).get("disabled", []) or [])
        for name in sorted(list_registered_agent_tools()):
            rows.append({
                "key": f"tools.disabled.{name}",
                "group": "Tools",
                "label": name,
                "widget": "toggle",
                "apply": APPLY_LIVE,
                "help": "",
                "value": name not in disabled,
            })
    except Exception:
        pass
    return rows


def set_setting(key: str, value: Any) -> dict:
    """Validate + persist one setting. Returns ``{applied, value[, note]}``
    on success or ``{error}`` on failure. ``applied`` is ``"live"`` or
    ``"next_start"``. Routes through the existing typed writer when one
    exists (``ui.*`` → ``set_ui_ports``), else a guarded dot-path write.
    """
    # Provider status rows are read-only (configure via /login or the web).
    if key.startswith("providers."):
        return {"error": "provider status is read-only — use /login or the Providers page"}

    # Dynamic per-tool toggles: ``on`` = enabled = not in tools.disabled.
    if key.startswith("tools.disabled."):
        name = key[len("tools.disabled."):]
        if not name:
            return {"error": "invalid tool key"}
        enable = _coerce("toggle", value)

        def _toggle_tool(cfg: dict) -> None:
            tools = cfg.setdefault("tools", {})
            disabled = set(tools.get("disabled", []) or [])
            disabled.discard(name) if enable else disabled.add(name)
            tools["disabled"] = sorted(disabled)

        _setup.update_config(_toggle_tool)
        return {"applied": APPLY_LIVE, "value": enable}

    spec = _BY_KEY.get(key)
    if spec is None:
        return {"error": f"unknown setting: {key}"}

    # coerce to the widget's type before validation
    try:
        coerced = _coerce(spec.widget, value)
    except (TypeError, ValueError):
        return {"error": f"invalid value for {spec.label!r}: {value!r}"}

    if spec.validate is not None:
        err = spec.validate(coerced)
        if err:
            return {"error": f"{spec.label}: {err}"}

    if spec.widget == "enum" and spec.choices is not None:
        opts = list(spec.choices())
        if coerced not in opts:
            return {"error": f"{spec.label}: must be one of {', '.join(opts)}"}

    # route to the typed writer that already owns this key, else dot-path
    if spec.key == "ui.port":
        _setup.set_ui_ports(backend_port=coerced)
    elif spec.key == "ui.web_port":
        _setup.set_ui_ports(web_port=coerced)
    elif spec.key == "ui.open_browser":
        _setup.set_ui_ports(open_browser=coerced)
    elif spec.key == "search.default_provider":
        _setup.write_search_default_provider(None if coerced == "auto" else coerced)
    else:
        _setup.update_config(lambda cfg: _set_at(cfg, spec.path, coerced))

    result: dict = {"applied": spec.apply, "value": coerced}

    # surface a port conflict the way `openprogram ports` does, so the
    # editor can warn "that port is taken by <who>" right after saving.
    if spec.key in ("ui.port", "ui.web_port"):
        try:
            from openprogram._ports import describe_port_owner
            owner = describe_port_owner(coerced)
            if owner is not None and not owner.is_ours:
                result["note"] = f"port {coerced} is currently held by {owner.detail}"
        except Exception:
            pass

    return result
