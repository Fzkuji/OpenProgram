"""DeepSeek Harness's ACP server (``dsh --profile acp``): one process per conversation.

DeepSeek Harness ships an Agent Client Protocol server as a ``dsh``
profile: newline-delimited JSON-RPC 2.0 on stdio, the same line protocol
``codex app-server`` speaks, so the Codex client's reader, requests and
per-conversation process pool are reused. Its sessions are saved under
``~/.dsh/sessions`` with the DeepSeek Harness app's own, and its
credentials are the app's.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from openprogram.runtime.harnesses.codex.client import AppServer, AppServerError, ProcessPool

#: The ACP protocol version spoken.
PROTOCOL_VERSION = 1
#: Where the macOS app keeps its command-line launcher.
_APP_LAUNCHER = Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")


def binary_path() -> Optional[str]:
    """The ``dsh`` launcher: ``DSH_BINARY``, ``dsh`` on PATH, or the app's own."""
    configured = os.environ.get("DSH_BINARY")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("dsh")
    if found:
        return found
    return str(_APP_LAUNCHER) if _APP_LAUNCHER.is_file() else None


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")


def signed_in() -> bool:
    """Whether DeepSeek Harness has credentials saved (their values are never read)."""
    return (dsh_home() / ".credentials.yaml").is_file()


def app_version() -> str:
    try:
        import json
        runtime = _APP_LAUNCHER.parents[3] / "runtime" / "primary-runtime" / "runtime.json"
        return str(json.loads(runtime.read_text(encoding="utf-8")).get("desktopVersion") or "")
    except (OSError, ValueError, IndexError):
        return ""


#: Whether the installed DeepSeek Harness reads images in a prompt (its ``initialize`` says).
_takes_images = False


class AcpServer(AppServer):
    """``dsh --profile acp``: JSON-RPC 2.0 lines and ACP's handshake."""

    label = "DeepSeek Harness"

    def default_answer(self, message: dict) -> dict:
        if message.get("method") == "session/request_permission":
            return {"result": {"outcome": {"outcome": "cancelled"}}}  # ACP's answer when nobody decides
        return super().default_answer(message)

    def start(self) -> dict:
        from openprogram.host._platform.process_ownership import process_tree_popen_kwargs
        import subprocess
        import threading

        env = {**os.environ, **self.env}
        self._process = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, **process_tree_popen_kwargs(),
        )
        threading.Thread(target=self._read, name="dsh-acp-reader", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="dsh-acp-stderr", daemon=True).start()
        answer = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "OpenProgram", "version": "1"},
        }, timeout=120)
        global _takes_images
        capabilities = ((answer or {}).get("agentCapabilities") or {}).get("promptCapabilities") or {}
        self.takes_images = _takes_images = bool(capabilities.get("image"))
        return answer or {}

    def _write(self, message: dict) -> None:
        super()._write({"jsonrpc": "2.0", **message})


def start_acp_server(extra_args: Optional[list[str]] = None, env: Optional[dict[str, str]] = None) -> AcpServer:
    binary = binary_path()
    if binary is None:
        raise AppServerError("DeepSeek Harness is not installed on this computer.")
    server = AcpServer([binary, "--profile", "acp", *(extra_args or [])], env=env)
    try:
        server.start()
    except (AppServerError, OSError) as exc:
        tail = server.stderr_tail().splitlines()[-1:] if server.stderr_tail() else []
        server.kill()
        raise AppServerError(f"DeepSeek Harness did not start: {tail[0] if tail else exc}") from exc
    return server


_pool: Optional[ProcessPool] = None


def pool() -> ProcessPool:
    global _pool
    if _pool is None:
        _pool = ProcessPool(starter=start_acp_server)
        import atexit
        atexit.register(_pool.close_all)
    return _pool


# -- models -----------------------------------------------------------------

def model_id(value: str) -> str:
    """``["deepseek-official","deepseek-v4-flash"]`` (ACP's option value) → ``deepseek-official:deepseek-v4-flash``.

    A colon, not a slash: OpenProgram reads a model reference's provider up
    to its first slash (``deepseek-harness/deepseek-official:deepseek-v4-flash``)."""
    import json
    try:
        parts = json.loads(value)
    except (TypeError, ValueError):
        return str(value)
    return ":".join(str(p) for p in parts) if isinstance(parts, list) else str(value)


def option_value(model: str) -> str:
    """The ACP option value of ``provider:model``."""
    import json
    provider, _, name = model.partition(":")
    return json.dumps([provider, name], separators=(",", ":")) if name else model


def read_options(config_options: list) -> tuple[list[dict], list[str], Optional[str]]:
    """Models, efforts and the default effort a session's ``configOptions`` offer."""
    models: list[dict] = []
    efforts: list[str] = []
    default_effort: Optional[str] = None
    for option in config_options or []:
        if not isinstance(option, dict):
            continue
        if option.get("id") == "model":
            current = option.get("currentValue")
            for group in option.get("options") or []:
                for row in (group.get("options") if isinstance(group, dict) and "options" in group else [group]):
                    if isinstance(row, dict) and row.get("value"):
                        models.append({"id": model_id(row["value"]), "label": str(row.get("name") or row["value"]),
                                       "default": row["value"] == current})
        elif option.get("id") == "reasoning_effort":
            efforts = [str(o.get("value")) for o in option.get("options") or [] if isinstance(o, dict) and o.get("value")]
            default_effort = option.get("currentValue")
    for model in models:
        model.update(efforts=efforts, default_effort=default_effort, images=_takes_images, personality=False)
    return models, efforts, default_effort
