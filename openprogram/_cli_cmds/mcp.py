"""``openprogram mcp`` CLI subcommands.

Thin HTTP client wrapping the ``/api/mcp/*`` endpoints in the worker.
All real logic — spawn / stop / config persistence — lives in
``openprogram.mcp`` and is shared with the webui and TUI. The CLI's
only job is to format requests, render responses for a terminal.

Worker must be running (``openprogram worker start``); we don't fall
back to writing the JSON file directly so all three frontends see
consistent runtime state.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


def _worker_base_url() -> Optional[str]:
    """Resolve the running worker's HTTP base URL via the port file
    ``<state>/worker.port``. Returns ``None`` if no worker is up.
    """
    from openprogram.worker.lifecycle import read_worker_port
    port = read_worker_port()
    if port is None:
        return None
    return f"http://127.0.0.1:{port}"


def _require_worker() -> str:
    base = _worker_base_url()
    if base is None:
        print("Error: openprogram worker is not running.",
              file=sys.stderr)
        print("Start it with: openprogram worker start", file=sys.stderr)
        sys.exit(1)
    return base


def _request(method: str, path: str,
              body: Optional[dict] = None) -> tuple[int, Any]:
    base = _require_worker()
    url = base + path
    data: Optional[bytes] = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method,
                                  headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8")
            payload = json.loads(text) if text else None
            return resp.status, payload
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"detail": text}
        return e.code, payload
    except urllib.error.URLError as e:
        print(f"Error contacting worker at {url}: {e}", file=sys.stderr)
        sys.exit(1)


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


def _render_detail(server: dict) -> str:
    lines = [
        f"name:           {server['name']}",
        f"state:          {_fmt_state(server['ready'], server['error'])}",
        f"type:           {server['type']}",
        f"command:        {' '.join(server.get('command', []))}",
        f"env:            {server.get('env') or '(empty)'}",
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


def _cmd_mcp_edit() -> int:
    """Open the mcp_servers.json file in $EDITOR. After save, the
    user has to restart affected servers manually (or restart worker).
    """
    import os
    import subprocess
    code, payload = _request("GET", "/api/mcp/config-path")
    if code != 200 or not isinstance(payload, dict):
        print(f"Error: could not resolve config path: {payload}",
              file=sys.stderr)
        return 1
    path = payload.get("path")
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, path])
    print("Done. Affected servers will pick up changes on next worker "
          "restart, or run `openprogram mcp restart <name>`.")
    return 0


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
