"""JSON-RPC-over-stdio client for language servers.

Synchronous by design. A language server call is request/response with
one outstanding request at a time from a single tool invocation, so the
reader runs on a background thread that parses ``Content-Length``-framed
messages and hands responses to whoever is waiting, while notifications
(diagnostics, progress) are stored by URI.

Server processes are wrapped by ``backend.local._invocation`` like every
other child this repo starts, so the configured sandbox applies. Under
``sandbox.mode=workspace-write`` that is exactly right: a language
server reads the workspace and writes nothing outside it.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

log = logging.getLogger(__name__)

# Enough for pyright to walk a large repo's imports on first open;
# every request also returns early the moment the response arrives.
REQUEST_TIMEOUT = 30.0
# Diagnostics arrive as an unsolicited notification some time after the
# file is opened. Poll for this long before reporting what we have.
DIAGNOSTICS_TIMEOUT = 20.0


class ServerUnavailable(RuntimeError):
    """The language server for this file cannot run here."""


@dataclass(frozen=True)
class ServerSpec:
    """How to start one language's server, and what to say when it is missing."""
    language_id: str
    binary: str
    arguments: tuple[str, ...]
    install_hint: str
    extensions: tuple[str, ...]


SERVERS: tuple[ServerSpec, ...] = (
    ServerSpec(
        language_id="python",
        binary="pyright-langserver",
        arguments=("--stdio",),
        install_hint="npm install -g pyright",
        extensions=(".py", ".pyi"),
    ),
    ServerSpec(
        language_id="typescript",
        binary="typescript-language-server",
        arguments=("--stdio",),
        install_hint="npm install -g typescript-language-server typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"),
    ),
)


def spec_for_file(file_path: str) -> ServerSpec | None:
    """Return the server spec covering ``file_path``'s extension."""
    extension = os.path.splitext(file_path)[1].lower()
    for spec in SERVERS:
        if extension in spec.extensions:
            return spec
    return None


def path_to_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    if not uri.startswith("file:"):
        return uri
    parsed = urlparse(uri)
    encoded_path = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    return url2pathname(encoded_path)


class LanguageServer:
    """One running language server, spoken to over stdio."""

    def __init__(self, spec: ServerSpec, workspace: str,
                 process: subprocess.Popen) -> None:
        self.spec = spec
        self.workspace = workspace
        self.process = process
        self._lock = threading.Lock()
        self._next_id = 0
        self._responses: dict[str, dict] = {}
        self._response_arrived = threading.Condition()
        self._diagnostics: dict[str, list[dict]] = {}
        self._opened: set[str] = set()
        self._versions: dict[str, int] = {}
        self._closed = False
        self._reader = threading.Thread(
            target=self._read_forever, name=f"lsp-{spec.language_id}",
            daemon=True,
        )
        self._reader.start()

    # -- wire format ----------------------------------------------------

    def _send(self, message: dict) -> None:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        stdin = self.process.stdin
        if stdin is None or self.process.poll() is not None:
            raise ServerUnavailable(
                f"{self.spec.binary} exited before the request was sent")
        with self._lock:
            stdin.write(frame)
            stdin.flush()

    def _read_forever(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            return
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = stdout.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    name, _, value = line.partition(b":")
                    headers[name.strip().lower().decode("ascii")] = (
                        value.strip().decode("ascii")
                    )
                length = int(headers.get("content-length", 0))
                if length <= 0:
                    continue
                body = stdout.read(length)
                if not body:
                    return
                try:
                    message = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._dispatch(message)
        except Exception as exc:  # reader death must not kill the process
            log.debug("lsp reader for %s stopped: %s", self.spec.binary, exc)
        finally:
            with self._response_arrived:
                self._closed = True
                self._response_arrived.notify_all()

    def _dispatch(self, message: dict) -> None:
        if "id" in message and ("result" in message or "error" in message):
            with self._response_arrived:
                self._responses[message["id"]] = message
                self._response_arrived.notify_all()
            return
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri", "")
            with self._response_arrived:
                self._diagnostics[uri] = params.get("diagnostics") or []
                self._response_arrived.notify_all()
            return
        # Server-to-client requests we do not implement still need an
        # answer, or servers such as pyright stall waiting for one.
        if "id" in message and "method" in message:
            method = message["method"]
            if method == "workspace/configuration":
                items = (message.get("params") or {}).get("items") or [{}]
                result: Any = [{} for _ in items]
            else:
                result = None
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def request(self, method: str, params: dict,
                timeout: float = REQUEST_TIMEOUT) -> Any:
        # String ids, so ours can never collide with the integer ids the
        # server picks for its own requests. Pyright numbers its
        # ``workspace/configuration`` requests from 0 and exits when a
        # client request reuses one of those ids.
        with self._response_arrived:
            self._next_id += 1
            request_id = f"op-{self._next_id}"
        self._send({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._response_arrived:
            while request_id not in self._responses:
                if self._closed:
                    raise ServerUnavailable(
                        f"{self.spec.binary} exited during {method}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{method} timed out after {timeout:g}s")
                self._response_arrived.wait(remaining)
            message = self._responses.pop(request_id)
        if "error" in message:
            error = message["error"] or {}
            raise RuntimeError(error.get("message") or str(error))
        return message.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- protocol -------------------------------------------------------

    def initialize(self) -> None:
        self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": path_to_uri(self.workspace),
            "workspaceFolders": [{
                "uri": path_to_uri(self.workspace),
                "name": os.path.basename(self.workspace) or self.workspace,
            }],
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": False},
                    "definition": {"linkSupport": True},
                    "references": {},
                },
                "workspace": {"workspaceFolders": True, "configuration": True},
            },
        })
        self.notify("initialized", {})

    def open_file(self, file_path: str) -> str:
        """Send the file's current on-disk text, so answers reflect the
        working tree rather than whatever the server last indexed."""
        uri = path_to_uri(file_path)
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        with self._response_arrived:
            version = self._versions.get(uri, 0) + 1
            method = ("textDocument/didChange" if uri in self._opened
                      else "textDocument/didOpen")
            if method == "textDocument/didOpen":
                params = {"textDocument": {
                    "uri": uri, "languageId": self.spec.language_id,
                    "version": version, "text": text,
                }}
            else:
                params = {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                }
            self._diagnostics.pop(uri, None)
            self.notify(method, params)
            self._opened.add(uri)
            self._versions[uri] = version
        return uri

    def wait_for_diagnostics(self, uri: str,
                             timeout: float = DIAGNOSTICS_TIMEOUT) -> list[dict]:
        deadline = time.monotonic() + timeout
        with self._response_arrived:
            while uri not in self._diagnostics:
                remaining = deadline - time.monotonic()
                if self._closed:
                    raise ServerUnavailable(
                        f"{self.spec.binary} exited while waiting for diagnostics")
                if remaining <= 0:
                    raise TimeoutError(
                        f"diagnostics timed out after {timeout:g}s")
                self._response_arrived.wait(remaining)
            return list(self._diagnostics[uri])

    def stop(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5.0)
            self.notify("exit", {})
        except Exception:
            log.debug("LSP shutdown request failed", exc_info=True)
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                log.debug("LSP process kill failed", exc_info=True)


# ---------------------------------------------------------------------------
# One server per (language, workspace), started on first use
# ---------------------------------------------------------------------------

_servers: dict[tuple[str, str], LanguageServer] = {}
_servers_lock = threading.Lock()
_atexit_registered = False


def find_workspace(file_path: str) -> str:
    """Nearest ancestor holding a project marker, else the file's directory."""
    markers = ("pyproject.toml", "setup.py", "package.json", "tsconfig.json",
               ".git")
    directory = os.path.dirname(os.path.abspath(file_path))
    current = directory
    while True:
        if any(os.path.exists(os.path.join(current, m)) for m in markers):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return directory
        current = parent


def get_server(file_path: str) -> LanguageServer:
    """Return the running server for ``file_path``, starting it if needed.

    Raises ``ServerUnavailable`` when the language is unsupported or the
    server binary is not installed.
    """
    spec = spec_for_file(file_path)
    if spec is None:
        raise ServerUnavailable(
            f"no language server configured for {os.path.basename(file_path)} "
            "— supported: Python (.py), TypeScript/JavaScript (.ts/.tsx/.js/.jsx)"
        )
    if shutil.which(spec.binary) is None:
        raise ServerUnavailable(
            f"unavailable: install {spec.binary} — `{spec.install_hint}`")

    workspace = find_workspace(file_path)
    key = (spec.language_id, workspace)
    with _servers_lock:
        server = _servers.get(key)
        if server is not None and server.process.poll() is None:
            return server
        server = _start(spec, workspace)
        _servers[key] = server
        global _atexit_registered
        if not _atexit_registered:
            atexit.register(shutdown_all)
            _atexit_registered = True
        return server


def _start(spec: ServerSpec, workspace: str) -> LanguageServer:
    # ``_invocation`` is the same sandbox-wrapping entry the MCP stdio
    # client uses for its child. ``LocalBackend.spawn`` is not usable
    # here: it merges stderr into stdout, and a server's log lines in
    # the middle of a Content-Length frame desynchronize the protocol.
    from openprogram.backend.local import _invocation
    from openprogram._compat import no_window_creation_flags

    command = " ".join([spec.binary, *spec.arguments])
    try:
        args, use_shell, env, _sandboxed = _invocation(command, cwd=workspace)
        process = subprocess.Popen(
            args,
            shell=use_shell,
            cwd=workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=no_window_creation_flags(),
        )
    except Exception as exc:
        raise ServerUnavailable(f"could not start {spec.binary}: {exc}") from exc
    server = LanguageServer(spec, workspace, process)
    try:
        server.initialize()
    except Exception as exc:
        server.stop()
        raise ServerUnavailable(
            f"{spec.binary} failed to initialize: {exc}") from exc
    return server


def shutdown_all() -> None:
    """Stop every cached server. Registered at exit; also callable directly."""
    with _servers_lock:
        servers = list(_servers.values())
        _servers.clear()
    for server in servers:
        server.stop()
