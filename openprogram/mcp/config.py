"""Config schema + loader for MCP servers.

Reads ``<state_dir>/mcp_servers.json`` (state dir resolves through
``openprogram.paths.get_state_dir``, so per-profile setups work too).
Returns a list of :class:`MCPServerConfig` for each ``enabled`` server.

File format (only ``local`` transport is implemented for v1; ``remote``
is reserved for future HTTP/SSE support):

.. code-block:: json

    {
      "servers": {
        "drawio": {
          "type": "local",
          "command": ["npx", "-y", "@drawio/mcp"],
          "env": {},
          "enabled": true,
          "timeout_seconds": 30
        }
      }
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Module reference (not ``from ... import get_state_dir``) — tests
# monkeypatch ``openprogram.paths.get_state_dir`` to redirect state
# I/O to a tmp dir. A direct import would freeze the original function
# at the moment config.py is loaded; if first import happens while a
# test's monkeypatch is active, the patched function leaks across
# tests. Going through the module ensures every call reads the
# attribute live.
from openprogram import paths as _paths


CONFIG_FILENAME = "mcp_servers.json"


@dataclass
class MCPServerConfig:
    """Resolved config for a single MCP server.

    Only ``type="local"`` is handled right now; transport selection
    happens in :mod:`.client`.
    """
    name: str
    type: str           # "local" (stdio) — "remote" reserved
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: float = 30.0

    def to_dict(self) -> dict:
        """Round-trip dict shape (matches the JSON file schema)."""
        return {
            "type": self.type,
            "command": list(self.command),
            "env": dict(self.env),
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
        }


def get_config_path() -> Path:
    return _paths.get_state_dir() / CONFIG_FILENAME


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
    """Validate + coerce one ``{type: ..., command: [...]}`` dict into
    a :class:`MCPServerConfig`. Returns ``None`` on bad input (logs a
    warning).
    """
    transport = entry.get("type", "local")
    if transport != "local":
        # Remote (HTTP/SSE) deliberately not implemented yet. The
        # current need (drawio-mcp) is a stdio server, and remote
        # transports also pull in OAuth / token handling.
        import sys
        print(f"[mcp] skipping server '{name}': unsupported type '{transport}'",
              file=sys.stderr)
        return None

    command = entry.get("command")
    if not isinstance(command, list) or not command:
        import sys
        print(f"[mcp] skipping server '{name}': missing/empty command list",
              file=sys.stderr)
        return None

    env_obj = entry.get("env", {})
    if not isinstance(env_obj, dict):
        env_obj = {}
    env = {str(k): str(v) for k, v in env_obj.items()}

    enabled = entry.get("enabled", True)
    timeout = float(entry.get("timeout_seconds", 30.0))

    return MCPServerConfig(
        name=name,
        type=transport,
        command=[str(x) for x in command],
        env=env,
        enabled=bool(enabled),
        timeout_seconds=timeout,
    )
