"""Persistent isolated Python transport, without host or GUI capabilities."""
from __future__ import annotations

from dataclasses import dataclass
import codecs
import json
import math
import os
from pathlib import Path
import selectors
import signal
import struct
import threading
import time

from .gui import spawn_gui_python

_MAX_TEXT = 256 * 1024
_MAX_FRAME = 2 * 1024 * 1024
_MAX_WIRE = 4 * 1024 * 1024


class GuiRunnerError(RuntimeError):
    """The runner cannot safely continue; no implicit restart or replay."""


@dataclass(frozen=True)
class GuiPythonResult:
    stdout: str
    stderr: str
    error: str | None


class GuiPythonRunner:
    """Own one interpreter for sequential, bounded calls inside a context.

    Results are untrusted script output, never evidence of a GUI side effect.
    All protocol faults, deadlines and cancellation destroy this interpreter.
    """

    def __init__(self):
        self._context = None
        self._process = None
        self._sequence = 0
        self._lock = threading.Lock()
        self._closed = False

    def __enter__(self):
        if self._closed or self._context is not None:
            raise GuiRunnerError("runner already entered or closed")
        bootstrap = Path(__file__).with_name("_gui_child.py").read_text(encoding="utf-8")
        context = spawn_gui_python(bootstrap)
        process = context.__enter__()
        self._context, self._process = context, process
        try:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            self._stop(time.monotonic() + 1)
        except BaseException:
            self.close()
            raise
        return self

    def _stop(self, deadline):
        """Prevent generated background threads from running between calls."""
        os.kill(self._process.pid, signal.SIGSTOP)
        while time.monotonic() < deadline:
            pid, status = os.waitpid(self._process.pid, os.WUNTRACED | os.WNOHANG)
            if pid:
                if os.WIFSTOPPED(status):
                    return
                self._process.returncode = os.waitstatus_to_exitcode(status)
                raise GuiRunnerError("child exited before idle suspension")
            time.sleep(0.001)
        raise GuiRunnerError("execution deadline exceeded during idle suspension")

    def close(self):
        self._closed = True
        if self._context is not None:
            context, self._context = self._context, None
            context.__exit__(None, None, None)

    def __exit__(self, *args):
        self.close()

    def execute(self, code: str, *, timeout: float = 30,
                cancel: threading.Event | None = None) -> GuiPythonResult:
        if not isinstance(code, str) or len(code.encode("utf-8")) > _MAX_TEXT:
            raise ValueError("code exceeds input limit or is not text")
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout must be finite and in (0, 30]")
        if not self._lock.acquire(blocking=False):
            raise GuiRunnerError("concurrent execution is not allowed")
        try:
            if self._closed or self._context is None:
                raise GuiRunnerError("runner is closed or not entered")
            self._sequence += 1
            return self._exchange(code, time.monotonic() + timeout, cancel)
        except BaseException:
            self.close()
            raise
        finally:
            self._lock.release()

    def _exchange(self, code, deadline, cancel):
        process = self._process
        os.kill(process.pid, signal.SIGCONT)
        payload = json.dumps({"id": self._sequence, "code": code}, ensure_ascii=True).encode()
        pending = memoryview(struct.pack("!I", len(payload)) + payload)
        incoming = bytearray()
        output = {"stdout": [], "stderr": []}
        text_size = 0
        wire_size = 0
        stderr_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def append(stream, text):
            nonlocal text_size
            text_size += len(text.encode("utf-8"))
            if text_size > _MAX_TEXT:
                raise GuiRunnerError("output limit exceeded")
            output[stream].append(text)

        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while True:
                if cancel is not None and cancel.is_set():
                    raise GuiRunnerError("execution cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GuiRunnerError("execution deadline exceeded")
                for key, _ in selector.select(min(remaining, 0.05)):
                    try:
                        if key.data == "input":
                            pending = pending[os.write(key.fd, pending):]
                            if not pending:
                                selector.unregister(key.fileobj)
                            continue
                        data = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError as exc:
                        raise GuiRunnerError("child pipe failed") from exc
                    wire_size += len(data)
                    if wire_size > _MAX_WIRE:
                        raise GuiRunnerError("output transport limit exceeded")
                    if not data:
                        raise GuiRunnerError("child exited or closed protocol pipe")
                    if key.data == "stderr":
                        append("stderr", stderr_decoder.decode(data))
                        continue
                    incoming.extend(data)
                    while len(incoming) >= 4:
                        length, = struct.unpack("!I", incoming[:4])
                        if not 0 < length <= _MAX_FRAME:
                            raise GuiRunnerError("invalid frame size")
                        if len(incoming) < length + 4:
                            break
                        raw = bytes(incoming[4:4 + length])
                        del incoming[:4 + length]
                        try:
                            message = json.loads(raw)
                        except (ValueError, RecursionError) as exc:
                            raise GuiRunnerError("invalid protocol JSON") from exc
                        if (not isinstance(message, dict) or type(message.get("id")) is not int
                                or message["id"] != self._sequence):
                            raise GuiRunnerError("invalid protocol identity")
                        if message.get("type") == "output":
                            if (not isinstance(message.get("stream"), str) or message["stream"] not in output
                                    or not isinstance(message.get("text"), str)):
                                raise GuiRunnerError("invalid protocol output")
                            append(message["stream"], message["text"])
                        elif message.get("type") == "done":
                            error = message.get("error")
                            if error is not None and not isinstance(error, str):
                                raise GuiRunnerError("invalid protocol error")
                            if error is not None:
                                text_size += len(error.encode("utf-8"))
                                if text_size > _MAX_TEXT:
                                    raise GuiRunnerError("output limit exceeded")
                            if incoming or pending:
                                raise GuiRunnerError("unexpected protocol data at completion")
                            self._stop(deadline)
                            # All writes preceding done are now observable. Drain raw
                            # stderr before returning; stdout must have no extra frame.
                            for stream, name in ((process.stderr, "stderr"), (process.stdout, "stdout")):
                                while True:
                                    try:
                                        tail = os.read(stream.fileno(), 65536)
                                    except BlockingIOError:
                                        break
                                    if not tail:
                                        raise GuiRunnerError("child exited at completion")
                                    if name == "stdout":
                                        raise GuiRunnerError("unexpected protocol data at completion")
                                    append("stderr", stderr_decoder.decode(tail))
                            append("stderr", stderr_decoder.decode(b"", final=True))
                            return GuiPythonResult("".join(output["stdout"]), "".join(output["stderr"]), error)
                        else:
                            raise GuiRunnerError("unknown protocol message")
