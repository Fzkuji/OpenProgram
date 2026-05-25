"""Config schema + loader for MCP servers.

Reads ``<state_dir>/mcp_servers.json`` (state dir resolves through
``openprogram.paths.get_state_dir``, so per-profile setups work too).
Returns a list of :class:`MCPServerConfig` for each ``enabled`` server.

Transports:

  * ``local`` — stdio subprocess. Uses ``command`` + ``env``.
  * ``http`` — Streamable HTTP. Uses ``url`` + ``headers`` + ``auth``.
  * ``sse``  — legacy SSE transport. Uses ``url`` + ``headers`` + ``auth``.

File format::

    {
      "servers": {
        "drawio": {
          "type": "local",
          "command": ["npx", "-y", "@drawio/mcp"],
          "env": {},
          "enabled": true,
          "timeout_seconds": 30
        },
        "linear": {
          "type": "http",
          "url": "https://mcp.linear.app/mcp",
          "auth": {"kind": "oauth", "client_name": "OpenProgram"},
          "enabled": true
        },
        "internal": {
          "type": "http",
          "url": "https://mcp.example.com/mcp",
          "headers": {"X-Tenant": "acme"},
          "auth": {"kind": "bearer", "token": "abc..."},
          "enabled": true
        }
      }
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Module reference (not ``from ... import get_state_dir``) — tests
# monkeypatch ``openprogram.paths.get_state_dir`` to redirect state
# I/O to a tmp dir. A direct import would freeze the original function
# at the moment config.py is loaded; if first import happens while a
# test's monkeypatch is active, the patched function leaks across
# tests. Going through the module ensures every call reads the
# attribute live.
from openprogram import paths as _paths


CONFIG_FILENAME = "mcp_servers.json"

LOCAL = "local"
HTTP = "http"
SSE = "sse"
_KNOWN_TRANSPORTS = (LOCAL, HTTP, SSE)

AUTH_NONE = "none"
AUTH_BEARER = "bearer"
AUTH_OAUTH = "oauth"
_KNOWN_AUTH_KINDS = (AUTH_NONE, AUTH_BEARER, AUTH_OAUTH)


@dataclass
class OAuthSettings:
    """Optional knobs for the OAuth 2.1 PKCE flow.

    All fields are optional — defaults work for any MCP server that
    supports dynamic client registration (RFC 7591), which is the
    common case. ``client_id``/``client_secret`` only need to be set
    for servers that pre-register clients.
    """
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scope: Optional[str] = None
    client_name: str = "OpenProgram"
    # 0 = pick a free port at runtime. Pin to a specific port only if
    # the server's allowlist requires a fixed redirect_uri.
    redirect_port: int = 0

    def to_dict(self) -> dict:
        out: dict = {"client_name": self.client_name,
                     "redirect_port": int(self.redirect_port)}
        if self.client_id:
            out["client_id"] = self.client_id
        if self.client_secret:
            out["client_secret"] = self.client_secret
        if self.scope:
            out["scope"] = self.scope
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "OAuthSettings":
        return cls(
            client_id=_opt_str(raw.get("client_id")),
            client_secret=_opt_str(raw.get("client_secret")),
            scope=_opt_str(raw.get("scope")),
            client_name=str(raw.get("client_name") or "OpenProgram"),
            redirect_port=int(raw.get("redirect_port") or 0),
        )


@dataclass
class MCPServerConfig:
    """Resolved config for a single MCP server.

    Fields are union-typed: ``command``/``env`` only apply to
    ``type=local``; ``url``/``headers``/``auth_*`` only apply to
    ``type=http`` or ``type=sse``. :func:`parse_entry` enforces this.
    """
    name: str
    type: str = LOCAL
    # local-only ------------------------------------------------------
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # remote-only -----------------------------------------------------
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auth_kind: str = AUTH_NONE
    bearer_token: Optional[str] = None
    oauth: Optional[OAuthSettings] = None
    # shared ----------------------------------------------------------
    enabled: bool = True
    timeout_seconds: float = 30.0
    # When False (default), tools from this server are registered with
    # ``defer=True`` so their full JSON Schemas don't bloat every LLM
    # request — the model discovers them via the deferred-tool catalog
    # in the system prompt and uses ``tool_search`` to load on demand.
    # Flip to True for a server whose tools the model uses every turn
    # (e.g. a focused drawio server with a handful of tools); the full
    # schema then appears in the initial tools array from turn 1.
    #
    # Per-tool ``_meta['anthropic/alwaysLoad'] == true`` overrides
    # server policy for individual tools (matches claude-code semantics).
    always_load: bool = False

    @property
    def is_remote(self) -> bool:
        return self.type in (HTTP, SSE)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "type": self.type,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "always_load": self.always_load,
        }
        if self.type == LOCAL:
            out["command"] = list(self.command)
            out["env"] = dict(self.env)
        else:
            out["url"] = self.url
            out["headers"] = dict(self.headers)
            auth_obj: dict[str, Any] = {"kind": self.auth_kind}
            if self.auth_kind == AUTH_BEARER and self.bearer_token:
                auth_obj["token"] = self.bearer_token
            if self.auth_kind == AUTH_OAUTH and self.oauth is not None:
                auth_obj.update(self.oauth.to_dict())
            out["auth"] = auth_obj
        return out


def get_config_path() -> Path:
    return _paths.get_state_dir() / CONFIG_FILENAME


def get_tokens_dir() -> Path:
    """Directory holding OAuth token files for remote MCP servers."""
    p = _paths.get_state_dir() / "mcp_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_configs(*, include_disabled: bool = False) -> list[MCPServerConfig]:
    """Load server configs from disk.

    By default, returns only enabled servers (matching the original
    worker-startup semantics: disabled ones are skipped). The webui
    management endpoints pass ``include_disabled=True`` so disabled
    entries still appear in the management list.

    Missing file → empty list. Malformed file → empty list + log to
    stderr (don't crash worker startup over a typo).
    """
    path = get_config_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        import sys
        print(f"[mcp] failed to parse {path}: {e}", file=sys.stderr)
        return []

    servers_obj = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers_obj, dict):
        return []

    out: list[MCPServerConfig] = []
    for name, entry in servers_obj.items():
        if not isinstance(entry, dict):
            continue
        cfg = parse_entry(name, entry)
        if cfg is None:
            continue
        if cfg.enabled or include_disabled:
            out.append(cfg)
    return out


def save_configs(configs: Iterable[MCPServerConfig]) -> Path:
    """Persist a full set of server configs back to disk.

    The file is rewritten as a whole (read-modify-write style).
    Callers are expected to pass the *complete* desired set — adding
    or removing entries is the caller's job.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "servers": {cfg.name: cfg.to_dict() for cfg in configs},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def parse_entry(name: str, entry: dict) -> Optional[MCPServerConfig]:
    """Validate + coerce one dict into a :class:`MCPServerConfig`.

    Returns ``None`` on bad input (logs a warning to stderr).
    """
    import sys

    transport = str(entry.get("type", LOCAL))
    if transport not in _KNOWN_TRANSPORTS:
        print(f"[mcp] skipping server '{name}': unknown type "
              f"'{transport}' (expected one of {_KNOWN_TRANSPORTS})",
              file=sys.stderr)
        return None

    enabled = bool(entry.get("enabled", True))
    timeout = float(entry.get("timeout_seconds", 30.0))
    always_load = bool(entry.get("always_load", False))

    if transport == LOCAL:
        command = entry.get("command")
        if not isinstance(command, list) or not command:
            print(f"[mcp] skipping server '{name}': missing/empty "
                  f"command list", file=sys.stderr)
            return None
        env_obj = entry.get("env", {})
        if not isinstance(env_obj, dict):
            env_obj = {}
        return MCPServerConfig(
            name=name,
            type=transport,
            command=[str(x) for x in command],
            env={str(k): str(v) for k, v in env_obj.items()},
            enabled=enabled,
            timeout_seconds=timeout,
            always_load=always_load,
        )

    # remote (http / sse) --------------------------------------------
    url = entry.get("url")
    if not isinstance(url, str) or not url.strip():
        print(f"[mcp] skipping server '{name}': missing 'url' for "
              f"transport '{transport}'", file=sys.stderr)
        return None

    headers_obj = entry.get("headers", {})
    if not isinstance(headers_obj, dict):
        headers_obj = {}
    headers = {str(k): str(v) for k, v in headers_obj.items()}

    auth_raw = entry.get("auth") or {"kind": AUTH_NONE}
    if not isinstance(auth_raw, dict):
        print(f"[mcp] server '{name}': 'auth' must be an object, "
              f"defaulting to none", file=sys.stderr)
        auth_raw = {"kind": AUTH_NONE}
    auth_kind = str(auth_raw.get("kind", AUTH_NONE))
    if auth_kind not in _KNOWN_AUTH_KINDS:
        print(f"[mcp] server '{name}': unknown auth kind "
              f"'{auth_kind}', defaulting to none", file=sys.stderr)
        auth_kind = AUTH_NONE

    bearer_token: Optional[str] = None
    oauth: Optional[OAuthSettings] = None
    if auth_kind == AUTH_BEARER:
        bearer_token = _opt_str(auth_raw.get("token"))
        if not bearer_token:
            print(f"[mcp] server '{name}': bearer auth without "
                  f"'token' — set it via the management API",
                  file=sys.stderr)
    elif auth_kind == AUTH_OAUTH:
        oauth = OAuthSettings.from_dict(auth_raw)

    return MCPServerConfig(
        name=name,
        type=transport,
        url=url.strip(),
        headers=headers,
        auth_kind=auth_kind,
        bearer_token=bearer_token,
        oauth=oauth,
        enabled=enabled,
        timeout_seconds=timeout,
        always_load=always_load,
    )


def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
