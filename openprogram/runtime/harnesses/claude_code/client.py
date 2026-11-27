"""The person's Claude Code, run in its stream-JSON mode: one process per conversation.

``claude -p --input-format stream-json --output-format stream-json`` reads
JSON lines on stdin (the person's message, and control requests such as
``initialize`` and ``interrupt``) and writes JSON lines on stdout: the
session's ``system`` / ``stream_event`` / ``assistant`` / ``user`` /
``result`` messages, and its own control requests (``can_use_tool`` when
``--permission-prompt-tool stdio`` sends permission prompts here). This is
the protocol Anthropic's Agent SDK speaks to the same program. A conversation's
turn runs in one process (``Pool``), closed when the turn ends so the Claude app
can go on with the session; model, effort and permission mode change in the
running process by control requests.

Which Claude Code: ``OPENPROGRAM_CLAUDE_BINARY``, else the newest one the
Claude desktop app installed for itself, else ``~/.claude/local/claude``,
``~/.local/bin/claude`` or ``claude`` on PATH. All of them share the one
sign-in ``claude auth login`` saves. When Claude Code has none, the Anthropic
API key set in OpenProgram's Model Services is handed to it
(``ANTHROPIC_API_KEY``), so its turns are billed to that API account.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

#: Environment a nested Claude Code must not inherit from one running OpenProgram.
_NESTED_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


class ClaudeError(Exception):
    def __init__(self, message: str, *, exited: bool = False) -> None:
        super().__init__(message)
        self.exited = exited


def _app_dir() -> Path:
    return Path(os.environ.get("CLAUDE_APP_DIR") or Path.home() / "Library" / "Application Support" / "Claude")


def _version_key(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:4])


def app_binaries() -> list[Path]:
    """The Claude Code builds the Claude app installed, newest first."""
    found = []
    for path in (_app_dir() / "claude-code").glob("*/*/claude.app/Contents/MacOS/claude"):
        if path.is_file():
            found.append(path)
    found.sort(key=lambda p: _version_key(p.parents[4].name), reverse=True)
    return found


def binary_path() -> Optional[str]:
    configured = os.environ.get("OPENPROGRAM_CLAUDE_BINARY")
    if configured and Path(configured).is_file():
        return configured
    bundled = app_binaries()
    if bundled:
        return str(bundled[0])
    for candidate in (Path.home() / ".claude" / "local" / "claude", Path.home() / ".local" / "bin" / "claude"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("claude")


def binary_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", out or "")
    return match.group(0) if match else ""


def settings_key() -> Optional[str]:
    """The Anthropic API key set in OpenProgram's Model Services, or None.

    Only an API key: Anthropic's sign-in tokens are never handed on."""
    try:
        from openprogram.models.auth.resolver import resolve_store_api_key_sync
        return resolve_store_api_key_sync("anthropic") or None
    except Exception as exc:  # no traceback: it could carry key material
        _log.debug("no Anthropic key for Claude Code (%s)", type(exc).__name__)
        return None


def own_sign_in(account: dict) -> bool:
    """Whether Claude Code reports a sign-in of its own (its ``initialize`` answer's
    ``account``): a saved login, an API key it was given, or a cloud provider."""
    return (str(account.get("tokenSource") or "none") != "none"
            or str(account.get("apiKeySource") or "none") != "none"
            or str(account.get("apiProvider") or "firstParty") != "firstParty")


def saved_login() -> bool:
    """Whether ``claude auth login`` has a login saved now: the keychain item's presence on
    macOS (its value is never read), else the credentials file's."""
    import hashlib
    import sys
    if sys.platform == "darwin":
        names = ["Claude Code-credentials"]
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        if config_dir:  # Claude Code names a separate config folder's item by its hash
            names.insert(0, "Claude Code-credentials-" + hashlib.sha256(config_dir.encode()).hexdigest()[:8])
        for name in names:
            try:
                if subprocess.run(["security", "find-generic-password", "-s", name],
                                  capture_output=True, timeout=5).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                return False
        return False
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return (home / ".credentials.json").is_file()


def sign_in_now(other_sign_in: bool) -> tuple[bool, Optional[str]]:
    """Whether Claude Code can sign in now, and the Model Services key to hand it, if any.

    ``other_sign_in``: it reported a sign-in that is no saved login (an API key or token
    in its environment or settings, a cloud provider), as last read. A saved login is
    checked now, so a ``claude auth login`` since is never overridden by the key."""
    if other_sign_in or saved_login():
        return True, None
    key = settings_key()
    return key is not None, key


def child_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _NESTED_ENV}
    env.update(extra or {})
    return env


class ClaudeProcess:
    """One ``claude -p`` in stream-JSON mode."""

    def __init__(self, argv: list[str], *, cwd: Optional[str] = None, env: Optional[dict[str, str]] = None) -> None:
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.messages: "queue.Queue[dict]" = queue.Queue()
        self._process: Optional[subprocess.Popen] = None
        self._stderr: deque[str] = deque(maxlen=40)
        self._ids = itertools.count(1)
        self._answers: dict[str, "queue.Queue[dict]"] = {}
        self._write_lock = threading.Lock()
        self.exited = threading.Event()
        self.withdrawn: set[str] = set()  # requests Claude Code took back (``control_cancel_request``)

    def start(self) -> None:
        from openprogram.host._platform.process_ownership import process_tree_popen_kwargs
        self._process = subprocess.Popen(
            self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=child_env(self.env), text=True, encoding="utf-8", errors="replace", bufsize=1,
            **process_tree_popen_kwargs(),
        )
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="claude-stderr", daemon=True)
        self._stderr_reader.start()
        threading.Thread(target=self._read, name="claude-reader", daemon=True).start()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self.exited.is_set()

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def send(self, message: dict) -> None:
        assert self._process is not None and self._process.stdin is not None
        try:
            with self._write_lock:
                self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ClaudeError("Claude Code stopped", exited=True) from exc

    def control(self, request: dict, *, timeout: float = 60) -> dict:
        """Send a control request and wait for its answer."""
        request_id = f"op_{next(self._ids)}"
        answer: "queue.Queue[dict]" = queue.Queue()
        self._answers[request_id] = answer
        if self.exited.is_set():
            self._answers.pop(request_id, None)
            raise ClaudeError("Claude Code stopped", exited=True)
        try:
            self.send({"type": "control_request", "request_id": request_id, "request": request})
            try:
                response = answer.get(timeout=timeout)
            except queue.Empty:
                raise ClaudeError(f"Claude Code did not answer {request.get('subtype')}") from None
        finally:
            self._answers.pop(request_id, None)
        if response.get("subtype") == "error":
            raise ClaudeError(str(response.get("error") or "Claude Code refused the request"),
                              exited=bool(response.get("exited")))
        return response.get("response") or {}

    def control_async(self, request: dict) -> None:
        """Send a control request without waiting (an interrupt)."""
        self.send({"type": "control_request", "request_id": f"op_{next(self._ids)}", "request": request})

    def respond(self, request_id: str, response: dict) -> None:
        self.send({"type": "control_response",
                   "response": {"subtype": "success", "request_id": request_id, "response": response}})

    def end_input(self) -> None:
        try:
            if self._process is not None and self._process.stdin is not None:
                self._process.stdin.close()
        except OSError:
            pass

    def close(self, wait: float = 3.0) -> None:
        self.end_input()
        if self._process is None:
            return
        try:
            self._process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        from openprogram.host._platform.processes import kill_process_tree
        kill_process_tree(process.pid)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def drop_pending(self) -> None:
        """Forget messages no turn is waiting for (a turn starts on an empty queue)."""
        self.withdrawn.clear()  # requests withdrawn in an earlier turn are long settled
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                return
            if message.get("type") == "openprogram/exited":
                self.messages.put(message)
                return

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "control_response":
                    response = message.get("response") or {}
                    waiting = self._answers.get(str(response.get("request_id")))
                    if waiting is not None:
                        waiting.put(response)
                    continue
                if message.get("type") == "control_cancel_request" and message.get("request_id"):
                    self.withdrawn.add(str(message["request_id"]))
                self.messages.put(message)
        except (OSError, ValueError):
            _log.debug("Claude Code output not read", exc_info=True)
        finally:
            # Whoever waits on this process hears that it is gone, with all it said on stderr.
            reader = getattr(self, "_stderr_reader", None)
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=1)
            self.exited.set()
            for waiting in list(self._answers.values()):
                waiting.put({"subtype": "error", "error": "Claude Code stopped", "exited": True})
            self.messages.put({"type": "openprogram/exited"})

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for line in self._process.stderr:
            if line.strip():
                self._stderr.append(line.rstrip())


def stream_args(binary: str) -> list[str]:
    """Stream-JSON both ways, partial messages, permission prompts sent here, and summarised
    adaptive thinking (as T3 Code and Paseo start it through the Agent SDK)."""
    return [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--permission-prompt-tool", "stdio",
            "--thinking", "adaptive", "--thinking-display", "summarized"]


def read_listing(binary: str) -> dict:
    """What Claude Code offers, from its ``initialize`` answer; no turn runs."""
    import tempfile
    process = ClaudeProcess(stream_args(binary), cwd=tempfile.gettempdir())
    process.start()
    try:
        return process.control({"subtype": "initialize"}, timeout=60)
    finally:
        process.kill()


# -- one process per conversation -------------------------------------------

#: How long a conversation keeps its Claude Code process after a turn: none. A
#: running one holds the session in ``~/.claude/sessions``, and the Claude app and
#: ``claude --resume`` refuse a session another process holds.
IDLE_SECONDS = 0.0
#: How long a process closed at its turn's end may take to finish writing before it is ended.
TURN_END_GRACE_SECONDS = 15.0


class Live:
    """A conversation's running Claude Code and what it was last told."""

    # ``busy`` is 1 while a turn runs on it; the pool's lock guards it.

    def __init__(self, process: ClaudeProcess, identity: tuple) -> None:
        self.process = process
        self.identity = identity
        self.busy = 0
        self.used = time.monotonic()
        self.loaded_at = time.time()
        self.model: Optional[str] = None
        self.effort: Optional[str] = None
        self.mode: Optional[str] = None
        # A turn ran in a process of its own meanwhile: this one's session is behind.
        self.behind = False
        # The environment it was started with beyond OpenProgram's (the Model Services key).
        self.env: Optional[dict[str, str]] = None
        # On OpenProgram's model: the user's settings.json model before it started.
        self.user_model: Optional[tuple] = None


class Pool:
    """Each conversation's Claude Code process, closed when idle (``idle_seconds``; 0
    closes it as soon as its turn ends).

    A process is known by the session it holds, its folder and its agent;
    the model, effort and permission mode change in the running process."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self._entries: dict[str, Live] = {}
        self._loose: set[Live] = set()  # started for one turn while another held the branch
        # A conversation's process closing after its turn. The next start waits for it: a
        # session another process still holds is resumed as a copy under a new id.
        self._closing: dict[str, set[threading.Thread]] = {}
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    def _close_later(self, key: str, close: Callable[[], None]) -> None:
        """Run ``close`` off the turn's path, recorded under ``key`` until it is done so the
        conversation's next start waits for it. Called under the lock."""
        def run() -> None:
            try:
                close()
            finally:
                with self._lock:
                    closers = self._closing.get(key)
                    if closers is not None:
                        closers.discard(thread)
                        if not closers:
                            self._closing.pop(key, None)

        thread = threading.Thread(target=run, name="claude-process-close", daemon=True)
        self._closing.setdefault(key, set()).add(thread)
        thread.start()

    def wait_closed(self, key: str, cancel: Optional[threading.Event] = None) -> None:
        """Wait for the conversation's previous processes to finish closing: a session
        another process still holds cannot be taken over. Ends early on ``cancel``."""
        deadline = time.monotonic() + TURN_END_GRACE_SECONDS + 10
        while True:
            with self._lock:
                closers = list(self._closing.get(key, ()))
            if not closers or time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                return
            closers[0].join(0.25)

    def get(self, key: str, identity: tuple) -> Optional[Live]:
        """The conversation's idle process when it holds ``identity``; an idle one holding
        another is stopped. A process running a turn is never shared or stopped. Waits
        first for the conversation's last processes to finish closing."""
        self.wait_closed(key)
        with self._lock:
            live = self._entries.get(key)
            if live is None or live.busy:
                return None
            if live.identity == identity and live.process.alive and not live.behind:
                live.busy = 1
                live.used = time.monotonic()
                return live
            self._entries.pop(key, None)
        live.process.close()
        return None

    def put(self, key: str, live: Live) -> None:
        """Keep ``live`` as the conversation's process, unless another is running a turn:
        then ``live`` serves this turn alone and is closed when it is released."""
        live.busy = 1
        with self._lock:
            current = self._entries.get(key)
            if current is not None and current.busy:
                self._loose.add(live)
                return
            self._entries[key] = live
            if self.idle_seconds > 0 and (self._reaper is None or not self._reaper.is_alive()):
                self._reaper = threading.Thread(target=self._reap_loop, name="claude-pool-reaper", daemon=True)
                self._reaper.start()
        if current is not None:
            current.process.close()

    def release(self, key: str, live: Live, *, broken: bool = False) -> None:
        live.busy = 0
        live.used = time.monotonic()
        with self._lock:
            if live in self._loose:
                current = self._entries.get(key)
                if current is not None:
                    current.behind = True
            self._loose.discard(live)
            kept = self._entries.get(key) is live
            if kept and (broken or not live.process.alive or self.idle_seconds <= 0):
                self._entries.pop(key, None)
                kept = False
            if not kept and not broken:
                # Off the turn's path, with time to finish writing the session's transcript.
                self._close_later(key, lambda: live.process.close(wait=TURN_END_GRACE_SECONDS))
        if broken:
            live.process.kill()

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values()) + list(self._loose)
            self._entries.clear()
            self._loose.clear()
        for live in entries:
            live.process.close()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(30)
            now = time.monotonic()
            with self._lock:
                idle = [key for key, live in self._entries.items()
                        if live.busy == 0 and now - live.used >= self.idle_seconds]
                for key in idle:
                    self._close_later(key, self._entries.pop(key).process.close)


_pool: Optional[Pool] = None


def pool() -> Pool:
    global _pool
    if _pool is None:
        _pool = Pool()
        import atexit
        atexit.register(_pool.close_all)
    return _pool
