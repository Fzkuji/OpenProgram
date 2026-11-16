"""``openprogram mcp`` CLI subcommands.

Thin HTTP client wrapping the ``/api/mcp/*`` endpoints in the worker.
All real logic — spawn / stop / config persistence — lives in
``openprogram.extensions.mcp`` and is shared with the webui and TUI. The CLI's
only job is to format requests, render responses for a terminal.

Worker must be running (``openprogram worker start``); we don't fall
back to writing the JSON file directly so all three frontends see
consistent runtime state.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from openprogram.interfaces.cli import worker_client


_require_backend_endpoint = worker_client.require_backend_endpoint


def _request(method: str, path: str,
             body: Optional[dict] = None) -> tuple[int, Any]:
    return worker_client.request(_require_backend_endpoint(), method, path, body)


# ---------------------------------------------------------------------------
# Rendering helpers — plain-text aligned columns (no markdown tables)
# ---------------------------------------------------------------------------

def _fmt_state(ready: bool, error: Optional[str]) -> str:
    if ready:
        return "ready"
    if error == "disabled":
        return "disabled"
    if error:
        return "error"
    return "starting"


def _render_list(servers: list[dict]) -> str:
    if not servers:
        return "No MCP servers configured."
    name_w = max(4, max(len(s["name"]) for s in servers))
    state_w = max(7, max(len(_fmt_state(s["ready"], s["error"])) for s in servers))
    lines = [
        f"{'NAME':<{name_w}}  {'STATE':<{state_w}}  {'TOOLS':>5}  COMMAND",
    ]
    for s in servers:
        cmd = " ".join(s.get("command", [])) or "—"
        state = _fmt_state(s["ready"], s["error"])
        lines.append(
            f"{s['name']:<{name_w}}  {state:<{state_w}}  "
            f"{s['tool_count']:>5}  {cmd}"
        )
        if s["error"] and s["error"] != "disabled":
            lines.append(f"{'':<{name_w}}  └─ error: {s['error']}")
    return "\n".join(lines)


def _render_env(env: dict | None) -> str:
    """Render a server's env for the terminal without printing its values.

    An MCP server's env is where API keys and bot tokens live, so the
    values never reach stdout — only the key names, plus a masked hint so
    the reader can tell two configured credentials apart.

    The API already returns ``{"has_value", "masked"}`` per name rather
    than the value, so this renders what it was given. Older responses
    (a plain string) are masked here instead, which also covers anyone
    piping a hand-written config through this formatter.
    """
    if not env:
        return "(empty)"
    from openprogram.models.auth.summary import mask_secret as _mask

    def shown(value) -> str:
        if isinstance(value, dict):
            return value.get("masked", "") if value.get("has_value") else "(empty)"
        return _mask(str(value))

    return ", ".join(f"{key}={shown(env[key])}" for key in sorted(env))


def _render_detail(server: dict) -> str:
    lines = [
        f"name:           {server['name']}",
        f"state:          {_fmt_state(server['ready'], server['error'])}",
        f"type:           {server['type']}",
        f"command:        {' '.join(server.get('command', []))}",
        f"env:            {_render_env(server.get('env'))}",
        f"enabled:        {server.get('enabled')}",
        f"timeout_sec:    {server.get('timeout_seconds')}",
        f"tool_count:     {server['tool_count']}",
    ]
    if server.get("error"):
        lines.append(f"error:          {server['error']}")
    schemas = server.get("tool_schemas") or []
    if schemas:
        lines.append("")
        lines.append("Tools:")
        for t in schemas:
            lines.append(f"  • {t['name']}")
            desc = (t.get("description") or "").strip().split("\n")[0]
            if desc:
                lines.append(f"      {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_mcp_token_create() -> int:
    from openprogram.extensions.mcp.server.auth import MCPTokenError, create_token

    try:
        token = create_token()
    except MCPTokenError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(token)
    return 0


def _cmd_mcp_serve() -> int:
    from openprogram.extensions.mcp.server.server import serve

    return serve()


def _cmd_mcp_list() -> int:
    code, payload = _request("GET", "/api/mcp/servers")
    if code != 200 or not isinstance(payload, dict):
        print(f"Error: list failed (HTTP {code}): {payload}", file=sys.stderr)
        return 1
    print(_render_list(payload.get("servers", [])))
    return 0


def _cmd_mcp_show(name: str) -> int:
    code, payload = _request("GET", f"/api/mcp/servers/{urllib.parse.quote(name)}")
    if code == 404:
        print(f"Error: server '{name}' not loaded.", file=sys.stderr)
        return 1
    if code != 200:
        print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
        return 1
    print(_render_detail(payload))
    return 0


def _cmd_mcp_add(name: str, command: list[str],
                  env: Optional[list[str]] = None,
                  timeout: float = 30.0,
                  enabled: bool = True) -> int:
    env_dict: dict[str, str] = {}
    for kv in env or []:
        if "=" not in kv:
            print(f"Error: --env must be KEY=VALUE, got '{kv}'",
                  file=sys.stderr)
            return 1
        k, v = kv.split("=", 1)
        env_dict[k] = v
    body = {
        "name": name,
        "type": "local",
        "command": command,
        "env": env_dict,
        "enabled": enabled,
        "timeout_seconds": timeout,
    }
    code, payload = _request("POST", "/api/mcp/servers", body=body)
    if code in (200, 201):
        print(_render_detail(payload))
        return 0
    print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
    return 1


def _cmd_mcp_rm(name: str) -> int:
    code, payload = _request("DELETE",
                              f"/api/mcp/servers/{urllib.parse.quote(name)}")
    if code == 200:
        print(f"Removed '{name}'.")
        return 0
    if code == 404:
        print(f"Error: server '{name}' not found.", file=sys.stderr)
        return 1
    print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
    return 1


def _cmd_mcp_restart(name: str) -> int:
    code, payload = _request("POST",
                              f"/api/mcp/servers/{urllib.parse.quote(name)}/restart")
    if code == 200:
        print(_render_detail(payload))
        return 0
    print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
    return 1


def _cmd_mcp_enable(name: str) -> int:
    code, payload = _request("POST",
                              f"/api/mcp/servers/{urllib.parse.quote(name)}/enable")
    if code == 200:
        print(_render_detail(payload))
        return 0
    print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
    return 1


def _cmd_mcp_disable(name: str) -> int:
    code, payload = _request("POST",
                              f"/api/mcp/servers/{urllib.parse.quote(name)}/disable")
    if code == 200:
        print(_render_detail(payload))
        return 0
    print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
    return 1


def _cmd_mcp_edit_removed() -> int:
    """Report that raw editing of the secret config file is gone.

    Handing the file to ``$EDITOR`` put every stored env value, header,
    bearer token, and OAuth client secret on screen and in the editor's
    swap/undo files, and an unlocked external edit could silently lose a
    concurrent write. Editing now goes through the structured paths,
    which mask secrets and apply preserve/replace/delete per field.
    """
    print(
        "`openprogram mcp edit` has been removed: it opened stored secrets "
        "in a plain-text editor.\n"
        "Use the structured paths instead:\n"
        "  openprogram mcp add <name> ...   add or re-add a server\n"
        "  openprogram mcp rm <name>        remove one\n"
        "  the MCP settings page            edit fields with secrets masked",
        file=sys.stderr,
    )
    return 1


def _cmd_mcp_test(name: str, command: list[str],
                   env: Optional[list[str]] = None,
                   timeout: float = 30.0) -> int:
    env_dict: dict[str, str] = {}
    for kv in env or []:
        if "=" not in kv:
            print(f"Error: --env must be KEY=VALUE, got '{kv}'",
                  file=sys.stderr)
            return 1
        k, v = kv.split("=", 1)
        env_dict[k] = v
    body = {
        "name": name,
        "type": "local",
        "command": command,
        "env": env_dict,
        "enabled": True,
        "timeout_seconds": timeout,
    }
    code, payload = _request("POST", "/api/mcp/test", body=body)
    if code != 200:
        print(f"Error (HTTP {code}): {payload}", file=sys.stderr)
        return 1
    if payload.get("ok"):
        print(f"OK — {payload['tool_count']} tool(s): "
              f"{', '.join(payload['tools'])}")
        return 0
    print(f"FAILED — {payload.get('error') or 'unknown error'}",
          file=sys.stderr)
    return 1
