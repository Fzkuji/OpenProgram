"""``codex app-server``: one JSON-RPC process per conversation.

Each conversation gets its own process (a crash ends one conversation's
turn, not every one's), started when its first turn needs it and released
after thirty idle minutes. Starts are serialised per ``CODEX_HOME`` and a
start that fails on Codex's SQLite state setup is tried again: two
processes opening the same state database at once can trip it.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

#: The name a session Codex records says started it.
CLIENT_NAME = "OpenProgram"
#: A conversation's process is released after this long without a turn.
IDLE_RELEASE_SECONDS = 30 * 60
#: How long one request may wait for its answer.
REQUEST_TIMEOUT_SECONDS = 60.0
#: Attempts at a start that failed on Codex's state database.
_START_ATTEMPTS = 3
_SQLITE_STARTUP = "sqlite state initialization"

Message = dict[str, Any]
Listener = Callable[[Message], bool]


class AppServerError(RuntimeError):
    """A request Codex answered with an error, or a process that went away."""

    def __init__(self, message: str, *, code: Optional[int] = None, data: Any = None,
                 exited: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.exited = exited


def _client_version() -> str:
    try:
        from importlib.metadata import version
        return version("openprogram")
    except Exception:  # noqa: BLE001 - a missing distribution still runs
        return "0"


def binary_path() -> Optional[str]:
    """The ``codex`` the user installed: the ``harnesses.codex.binary`` setting,
    else ``codex`` on PATH, else what the user's login shell finds (an app
    started from the Dock gets a short PATH). No install place is assumed."""
    configured = os.environ.get("OPENPROGRAM_CODEX_BINARY")
    if not configured:
        try:
            from openprogram.state.config import read_config_value
            harnesses = read_config_value("harnesses")
            if isinstance(harnesses, dict) and isinstance(harnesses.get("codex"), dict):
                configured = harnesses["codex"].get("binary")
        except Exception:  # noqa: BLE001 - an unreadable config means no setting
            configured = None
    if isinstance(configured, str) and configured.strip():
        path = Path(configured).expanduser()
        return str(path) if path.exists() else None
    return shutil.which("codex") or _login_shell_codex()


def _login_shell_codex() -> Optional[str]:
    """``command -v codex`` in the user's login shell (macOS and Linux)."""
    shell = os.environ.get("SHELL")
    if sys.platform == "win32" or not shell:
        return None
    try:
        out = subprocess.run([shell, "-lic", "command -v codex"], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed((out.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("/") and os.access(line, os.X_OK):
            return line
    return None


_versions: dict[tuple[str, float, int], str] = {}


def binary_version(binary: str) -> str:
    """``codex --version`` of ``binary`` (``0.171.0``), kept until the file changes."""
    try:
        stat = Path(binary).resolve().stat()
    except OSError:
        return ""
    key = (binary, stat.st_mtime, stat.st_size)
    if key in _versions:
        return _versions[key]
    version = ""
    try:
        from openprogram.host._platform.process_ownership import no_window_creation_flags
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10,
                             creationflags=no_window_creation_flags())
        lines = (out.stdout or out.stderr or "").strip().splitlines()
        version = lines[0].split()[-1] if lines else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    _versions[key] = version
    return version


def codex_home(env: Optional[dict[str, str]] = None) -> str:
    """The Codex state folder a process with ``env`` uses."""
    value = (env or {}).get("CODEX_HOME") or os.environ.get("CODEX_HOME")
    return str(Path(value).expanduser()) if value else str(Path.home() / ".codex")


class AppServer:
    """One running ``codex app-server`` and the JSON-RPC lines it reads and writes.

    Answers arrive on a reader thread. Notifications and Codex's own
    requests go to the listeners in order; a request no listener takes
    gets ``default_answer`` so Codex never waits on nobody.
    """

    def __init__(self, argv: list[str], *, env: Optional[dict[str, str]] = None) -> None:
        self.argv = list(argv)
        self.env = dict(env or {})
        self._process: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[Any, dict] = {}
        self._pending_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._listeners_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=40)
        self.exited = threading.Event()
        self.user_agent = ""

    #: The program's name in the errors the person sees.
    label = "Codex"

    # -- lifetime ---------------------------------------------------------

    def start(self) -> dict:
        """Start the process and complete the handshake; returns ``initialize``'s answer."""
        from openprogram.host._platform.process_ownership import process_tree_popen_kwargs

        env = {**os.environ, **self.env}
        self._process = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, **process_tree_popen_kwargs(),
        )
        threading.Thread(target=self._read, name="codex-app-server-reader", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True).start()
        answer = self.request("initialize", {
            "clientInfo": {"name": CLIENT_NAME, "title": CLIENT_NAME, "version": _client_version()},
        })
        self.notify("initialized")
        self.user_agent = str((answer or {}).get("userAgent") or "")
        return answer or {}

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self.exited.is_set()

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process is not None else None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def close(self) -> None:
        """Stop the process and every command it started."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                from openprogram.host._platform.processes import kill_process_tree
                kill_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        self._closed()

    def kill(self) -> None:
        """Stop the process tree at once (a Stop that Codex did not obey)."""
        process = self._process
        if process is not None and process.poll() is None:
            from openprogram.host._platform.processes import kill_process_tree
            kill_process_tree(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._closed()

    def _closed(self) -> None:
        self.exited.set()
        with self._pending_lock:
            waiting = list(self._pending.values())
            self._pending.clear()
        for slot in waiting:
            slot["error"] = AppServerError(f"{self.label} stopped", exited=True)
            slot["event"].set()

    # -- protocol ---------------------------------------------------------

    def _write(self, message: Message) -> None:
        process = self._process
        if process is None or process.stdin is None or not self.alive:
            raise AppServerError(f"{self.label} is not running", exited=True)
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                process.stdin.write(line.encode("utf-8"))
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise AppServerError(f"{self.label} is not running", exited=True) from exc

    def request(self, method: str, params: Optional[dict] = None, *,
                timeout: float = REQUEST_TIMEOUT_SECONDS) -> Any:
        request_id = next(self._ids)
        slot: dict = {"event": threading.Event()}
        with self._pending_lock:
            self._pending[request_id] = slot
        try:
            self._write({"id": request_id, "method": method, "params": params or {}})
            if not slot["event"].wait(timeout):
                raise AppServerError(f"{self.label} did not answer {method}")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in slot:
            error = slot["error"]
            if isinstance(error, AppServerError):
                raise error
            message = str(error.get("message") or error)
            data = error.get("data")
            detail = data if isinstance(data, str) else (data or {}).get("message") if isinstance(data, dict) else None
            if detail and str(detail) not in message:
                message = f"{message}: {detail}"  # ACP puts the reason in ``data``
            raise AppServerError(message, code=error.get("code"), data=data)
        return slot.get("result")

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        message: Message = {"method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def respond(self, request_id: Any, result: Any = None, *, error: Optional[dict] = None) -> None:
        message: Message = {"id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result if result is not None else {}
        try:
            self._write(message)
        except AppServerError:
            _log.debug("Codex left before an answer to request %s", request_id)

    def listen(self, listener: Listener) -> Callable[[], None]:
        """Add a listener; returns the function that removes it."""
        with self._listeners_lock:
            self._listeners.append(listener)

        def remove() -> None:
            with self._listeners_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return remove

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for raw in process.stdout:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" not in message and "id" in message:
                    with self._pending_lock:
                        slot = self._pending.get(message["id"])
                    if slot is not None:
                        if "error" in message:
                            slot["error"] = message["error"] or {}
                        else:
                            slot["result"] = message.get("result")
                        slot["event"].set()
                    continue
                self._dispatch(message)
        except Exception:  # noqa: BLE001 - the reader ends with the process
            _log.debug("Codex reader stopped", exc_info=True)
        finally:
            self._closed()
            with self._listeners_lock:
                listeners = list(self._listeners)
            for listener in listeners:
                try:
                    listener({"method": "openprogram/exited"})
                except Exception:  # noqa: BLE001
                    _log.debug("Codex exit listener failed", exc_info=True)

    def _dispatch(self, message: Message) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        taken = False
        for listener in listeners:
            try:
                taken = bool(listener(message)) or taken
            except Exception:  # noqa: BLE001 - one listener never stops the stream
                _log.warning("Codex listener failed", exc_info=True)
        if not taken and "id" in message:
            self.respond(message["id"], **self.default_answer(message))

    def default_answer(self, message: Message) -> dict:
        return default_answer(message)

    def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            for raw in process.stderr:
                self._stderr.append(raw.decode("utf-8", "replace").rstrip())
        except Exception:  # noqa: BLE001
            pass


def default_answer(message: Message) -> dict:
    """The answer to a request of Codex's nobody can take: refuse it, so the turn goes on."""
    method = message.get("method")
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        return {"result": {"decision": "decline"}}
    if method in ("applyPatchApproval", "execCommandApproval"):
        return {"result": {"decision": "denied"}}
    if method == "item/permissions/requestApproval":
        return {"result": {"permissions": {}, "scope": "turn"}}
    if method == "item/tool/requestUserInput":
        return {"result": {"answers": {}}}
    if method == "mcpServer/elicitation/request":
        return {"result": {"action": "decline"}}
    if method == "item/tool/call":
        return {"result": {"contentItems": [{"type": "inputText", "text": "This tool is not available."}],
                           "success": False}}
    return {"error": {"code": -32601, "message": f"OpenProgram does not handle {method}"}}


# -- starting ---------------------------------------------------------------

_home_locks: dict[str, threading.Lock] = {}
_home_locks_lock = threading.Lock()


def _home_lock(home: str) -> threading.Lock:
    with _home_locks_lock:
        return _home_locks.setdefault(home, threading.Lock())


def start_app_server(extra_args: Optional[list[str]] = None,
                     env: Optional[dict[str, str]] = None) -> AppServer:
    """Start ``codex app-server`` and finish its handshake.

    Starts that share a ``CODEX_HOME`` run one at a time, and one that fails
    while Codex sets up its state database is tried again.
    """
    binary = binary_path()
    if binary is None:
        raise AppServerError("Codex is not installed on this computer.")
    argv = [binary, "app-server", *(extra_args or [])]
    last: Optional[Exception] = None
    with _home_lock(codex_home(env)):
        for attempt in range(_START_ATTEMPTS):
            server = AppServer(argv, env=env)
            try:
                server.start()
                return server
            except (AppServerError, OSError) as exc:
                tail = server.stderr_tail()
                server.kill()
                last = exc
                if _SQLITE_STARTUP not in f"{exc} {tail}".lower():
                    break
                time.sleep(0.3 * (attempt + 1))
    message = str(last) if last else "Codex did not start"
    raise AppServerError(f"Codex did not start: {message}")


# -- one process per conversation -------------------------------------------

class ProcessPool:
    """The app-server of each conversation, released when idle."""

    def __init__(self, *, idle_seconds: float = IDLE_RELEASE_SECONDS,
                 starter: Optional[Callable[[list[str], dict[str, str]], "AppServer"]] = None) -> None:
        self.idle_seconds = idle_seconds
        # How a process is started: ``codex app-server`` unless another
        # harness (DeepSeek Harness over ACP) pools its own processes.
        self.starter = starter
        self._entries: dict[str, dict] = {}
        self._loose: set = set()  # processes started for one turn while another held the key
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    def acquire(self, key: str, *, extra_args: list[str], env: dict[str, str]) -> tuple[AppServer, bool]:
        """The conversation's process, started (or restarted) as needed.

        The second value is True when the process is new, so a thread it
        knew must be resumed. A process whose launch arguments changed is
        replaced.
        """
        launch = (tuple(extra_args), tuple(sorted(env.items())))
        stale = None
        loose = False
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.get("reload") and not entry["busy"]:
                # A turn ran in a process of its own meanwhile: this one's thread is behind.
                self._entries.pop(key, None)
                stale, entry = entry["server"], None
            if entry is not None and entry["busy"] and entry["server"].alive:
                # Another turn is running in it: this one gets a process of its own
                # rather than a second turn in the same thread, or a restart under it.
                loose = True
            elif entry is not None and (entry["launch"] != launch or not entry["server"].alive):
                self._entries.pop(key, None)
                stale = entry["server"]
            elif entry is not None:
                entry["busy"] += 1
                entry["used"] = time.monotonic()
                return entry["server"], False
        if stale is not None:
            stale.close()
        server = (self.starter or start_app_server)(extra_args, env)
        if loose:
            with self._lock:
                self._loose.add(server)
            return server, True
        with self._lock:
            current = self._entries.get(key)
            if current is not None and current["server"].alive:
                # Another turn started one first: this one serves this turn alone.
                self._loose.add(server)
                return server, True
            self._entries[key] = {"server": server, "launch": launch, "busy": 1, "used": time.monotonic()}
            self._ensure_reaper()
        return server, True

    def release(self, key: str, server: AppServer) -> None:
        with self._lock:
            loose = server in self._loose
            self._loose.discard(server)
        if loose:
            self._behind(key)
            server.close()
            return
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry["server"] is server:
                entry["busy"] = max(0, entry["busy"] - 1)
                entry["used"] = time.monotonic()

    def discard(self, key: str, server: AppServer) -> None:
        """Forget and stop a process that failed; the next turn starts another."""
        with self._lock:
            loose = server in self._loose
            self._loose.discard(server)
            entry = self._entries.get(key)
            if entry is not None and entry["server"] is server:
                self._entries.pop(key, None)
        if loose:
            self._behind(key)
        server.kill()

    def _behind(self, key: str) -> None:
        """A turn ran in a process of its own: the kept one reloads the thread before its next."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry["reload"] = True

    def close_all(self) -> None:
        with self._lock:
            servers = [entry["server"] for entry in self._entries.values()] + list(self._loose)
            self._entries.clear()
            self._loose.clear()
        for server in servers:
            server.close()

    def reap(self) -> None:
        now = time.monotonic()
        with self._lock:
            idle = [key for key, entry in self._entries.items()
                    if entry["busy"] == 0 and now - entry["used"] >= self.idle_seconds]
            servers = [self._entries.pop(key)["server"] for key in idle]
        for server in servers:
            server.close()

    def _ensure_reaper(self) -> None:
        # Called under the lock; a reaper leaving clears itself under it too.
        if self._reaper is not None:
            return

        def run() -> None:
            try:
                while True:
                    time.sleep(min(60.0, max(1.0, self.idle_seconds / 4)))
                    self.reap()
                    with self._lock:
                        if not self._entries:
                            self._reaper = None
                            return
            finally:
                # Ended by an error too: the next process starts another reaper.
                with self._lock:
                    if self._reaper is threading.current_thread():
                        self._reaper = None

        self._reaper = threading.Thread(target=run, name="codex-app-server-reaper", daemon=True)
        self._reaper.start()


_pool: Optional[ProcessPool] = None
_pool_lock = threading.Lock()


def pool() -> ProcessPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPool()
            import atexit
            atexit.register(_pool.close_all)
        return _pool
