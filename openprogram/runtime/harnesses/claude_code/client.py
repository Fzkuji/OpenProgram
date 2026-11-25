"""The person's Claude Code, run in its stream-JSON mode: one process per conversation.

``claude -p --input-format stream-json --output-format stream-json`` reads
JSON lines on stdin (the person's message, and control requests such as
``initialize`` and ``interrupt``) and writes JSON lines on stdout: the
session's ``system`` / ``stream_event`` / ``assistant`` / ``user`` /
``result`` messages, and its own control requests (``can_use_tool`` when
``--permission-prompt-tool stdio`` sends permission prompts here). This is
the protocol Anthropic's Agent SDK speaks to the same program. A conversation
keeps one process (``Pool``) and sends each turn into it; model, effort and
permission mode change in the running process by control requests.

Which Claude Code: ``OPENPROGRAM_CLAUDE_BINARY``, else the newest one the
Claude desktop app installed for itself, else ``~/.claude/local/claude``,
``~/.local/bin/claude`` or ``claude`` on PATH.
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
from typing import Any, Optional

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

#: How long an idle conversation keeps its Claude Code process.
IDLE_SECONDS = 30 * 60


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


class Pool:
    """Each conversation's Claude Code process, closed when idle.

    A process is known by the session it holds, its folder and its agent;
    the model, effort and permission mode change in the running process."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self._entries: dict[str, Live] = {}
        self._loose: set[Live] = set()  # started for one turn while another held the branch
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    def get(self, key: str, identity: tuple) -> Optional[Live]:
        """The conversation's idle process when it holds ``identity``; an idle one holding
        another is stopped. A process running a turn is never shared or stopped."""
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
            if self._reaper is None or not self._reaper.is_alive():
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
            if kept and (broken or not live.process.alive):
                self._entries.pop(key, None)
                kept = False
        if broken:
            live.process.kill()
        elif not kept:
            live.process.close()

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
                stale = [self._entries.pop(key) for key in idle]
            for live in stale:
                live.process.close()


_pool: Optional[Pool] = None


def pool() -> Pool:
    global _pool
    if _pool is None:
        _pool = Pool()
        import atexit
        atexit.register(_pool.close_all)
    return _pool
