"""One trusted application operation per process, with JSON messages on stdio."""
from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import os
import signal
import threading
from pathlib import Path
import sys
import uuid


def main():
    channel = sys.stdout
    request = json.loads(sys.stdin.readline())
    definition = request["definition"]
    # The supervisor is the only owner. A hard worker exit must not leave
    # an operation continuing to modify application data unnoticed.
    parent_pid = os.getppid()
    finished = threading.Event()
    def watch_parent():
        while not finished.wait(0.5):
            if os.getppid() != parent_pid:
                if os.name != "nt":
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                os._exit(1)
    watcher = threading.Thread(target=watch_parent, daemon=True)
    watcher.start()
    def send(event):
        channel.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        channel.flush()

    class Context:
        instance_id = request["instance"]["id"]
        project_path = request["instance"]["project_path"]
        run_id = request["run_id"]

        def progress(self, value):
            send({"type": "progress", "value": value})

        def load(self):
            self._require("storage.app")
            from .state import data
            return data(self.instance_id)

        def save(self, value, *, expected_version):
            self._require("storage.app")
            from .state import data
            return data(self.instance_id, value, expected_version=expected_version)

        def read_file(self, relative):
            self._require("files.project.read")
            if not self.project_path:
                raise ValueError("no project bound")
            from .catalog import contained
            return contained(Path(self.project_path), relative).read_text()

        def ask(self, question):
            request_id = uuid.uuid4().hex
            send({"type": "question", "request_id": request_id, "question": str(question)})
            answer = json.loads(sys.stdin.readline())
            if answer.get("request_id") != request_id:
                raise ValueError("question response mismatch")
            return answer.get("answer")

        def _require(self, capability):
            if capability not in definition["capabilities"]:
                raise PermissionError(f"application requires {capability}")

    runtime = None
    token = None
    writer_token = None
    session_store = None
    class LazyRuntime:
        def __getattr__(self, name):
            nonlocal runtime
            if runtime is None:
                from openprogram.providers.registry import create_runtime
                runtime = create_runtime(model=request.get("model") or None)
            return getattr(runtime, name)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            # Avoid writing bytecode into the immutable version directory.
            sys.dont_write_bytecode = True
            sys.path.insert(0, request["root"])
            if "model.invoke" in definition["capabilities"]:
                from openprogram.agentic_programming.function import _current_runtime
                from openprogram.store import SessionStore, SessionNodeWriter, _store
                from .catalog import home
                session_store = SessionStore(root_path=home() / "runs")
                writer_token = _store.set(SessionNodeWriter(session_store, request["run_id"]))
                token = _current_runtime.set(LazyRuntime())
            module, name = definition["backend"]["entry"].split(":")
            operations = getattr(importlib.import_module(module), name)
            function = operations[request["operation"]]
            result = function(request["input"], Context())
            if inspect.isawaitable(result):
                import asyncio
                result = asyncio.run(result)
            from jsonschema import validate
            validate(result, definition["operations"][request["operation"]].get("output", {}))
            send({"type": "result", "value": result})
        except Exception as exc:
            send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            finished.set()
            watcher.join(timeout=1)
            if writer_token is not None:
                _store.reset(writer_token)
            if session_store is not None:
                session_store.close()
            if token is not None:
                _current_runtime.reset(token)
            if runtime is not None:
                close = getattr(runtime, "close", None)
                if close:
                    close()


if __name__ == "__main__":
    main()
