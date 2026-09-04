"""CLI `execution cancel` talks to the default worker, not local state."""

from __future__ import annotations

import io
import json
import multiprocessing
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from openprogram.agent import run_control
from openprogram.backend_endpoint import BackendEndpoint
from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


_FAKE_TOKEN = "A" * 43


@pytest.fixture
def store(tmp_path, monkeypatch) -> SessionStore:
    value = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: value)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", value)
    return value


@pytest.fixture(autouse=True)
def clean_runtime_control():
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
    run_control._owners.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()
    yield
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
    run_control._owners.clear()
    run_control._grace_threads.clear()
    run_control._finalizing.clear()


def _run_cli(monkeypatch, argv: list[str], origin: str) -> tuple[int, str]:
    from openprogram.cli import main
    from openprogram.cli.commands import execution as execution_cmd

    monkeypatch.setattr(
        execution_cmd,
        "_require_backend_endpoint",
        lambda: BackendEndpoint(origin=origin, token=_FAKE_TOKEN),
    )
    monkeypatch.setattr("sys.argv", ["openprogram", *argv])
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            main()
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, buf.getvalue()


def _serve_cancel(store: SessionStore) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            from openprogram.agent.run_control import (
                ExecutionNotCancellable,
                ExecutionNotFound,
                cancel_execution,
            )

            execution_id = (body.get("execution_id") or "").strip()
            try:
                record = cancel_execution(execution_id)
                status = 200
                payload = {"execution": record}
            except ExecutionNotFound:
                status = 404
                payload = {"error": "ExecutionNotFound"}
            except ExecutionNotCancellable as exc:
                status = 409
                payload = {
                    "error": "ExecutionNotCancellable",
                    "execution": exc.execution,
                }
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return server, origin


def test_cli_execution_cancel_is_idempotent(store, monkeypatch):
    store.create_session("cli-session", "main")
    SessionNodeWriter(store, "cli-session").append(Call(
        id="cli-exec",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        metadata={"status": "queued", "execution_kind": "agentic_function"},
    ))
    server, origin = _serve_cancel(store)
    try:
        code1, out1 = _run_cli(
            monkeypatch, ["execution", "cancel", "cli-exec", "--expected-version", "0", "--command-id", "cancel-cli-1"], origin,
        )
        code2, out2 = _run_cli(
            monkeypatch, ["execution", "cancel", "cli-exec", "--expected-version", "0", "--command-id", "cancel-cli-1"], origin,
        )
    finally:
        server.shutdown()

    assert code1 == 0
    assert code2 == 0
    assert "status=cancelled" in out1
    assert "reason_code=cancel.user" in out1
    assert "status=cancelled" in out2
    assert "reason_code=cancel.user" in out2
    assert "PID" not in out1 and "pid" not in out1
    assert "SIG" not in out1 and "signal" not in out1.lower()
    node = next(
        node for node in store.get_nodes("cli-session")
        if node.id == "cli-exec"
    )
    assert node.metadata["status"] == "cancelled"
    assert node.metadata["reason_code"] == "cancel.user"
    assert node.output == "partial output"


def _worker_main(ready, signalled, store_path, session_id, exec_id):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from openprogram.agent import run_control as rc
    from openprogram.context.nodes import Call as Node, ROLE_CODE as CODE
    from openprogram.store import SessionNodeWriter as Writer
    from openprogram.store.session import session_store as store_module
    from openprogram.store.session.session_store import SessionStore as Store

    store = Store(store_path)
    store_module._default_store = store
    store.create_session(session_id, "main")
    Writer(store, session_id).append(Node(
        id=exec_id,
        role=CODE,
        name="cancellation_probe",
        output="partial",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    live = True
    token = rc.CancellationToken(session_id, exec_id)
    rc.register_execution_owner(
        exec_id,
        session_id,
        token=token,
        is_alive=lambda: live,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            record = rc.cancel_execution(body["execution_id"])
            if token.is_cancelled():
                signalled.set()
            data = json.dumps({"execution": record}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    ready.put(server.server_address[1])
    server.handle_request()
    ready.put(_node_status(store, session_id, exec_id))
    ready.put(token.is_cancelled())


def _node_status(store, session_id, execution_id):
    return next(
        node for node in store.get_nodes(session_id)
        if node.id == execution_id
    ).metadata["status"]


def test_cli_cancel_signals_owner_in_worker_process(tmp_path, monkeypatch):
    store_path = tmp_path / "worker-sessions"
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    signalled = ctx.Event()
    proc = ctx.Process(
        target=_worker_main,
        args=(ready, signalled, str(store_path), "cli-remote", "remote-exec"),
    )
    proc.start()
    try:
        port = ready.get(timeout=20)
        origin = f"http://127.0.0.1:{port}"
        local_before = "remote-exec" in run_control._owners
        code, out = _run_cli(
            monkeypatch, ["execution", "cancel", "remote-exec", "--expected-version", "0"], origin,
        )
        persisted = ready.get(timeout=20)
        token_tripped = ready.get(timeout=5)
    finally:
        proc.join(5)
        if proc.is_alive():
            proc.terminate()
            proc.join(2)

    assert local_before is False
    assert "remote-exec" not in run_control._owners
    assert code == 0
    assert "status=" in out
    assert signalled.is_set()
    assert token_tripped is True
    assert persisted in {"cancelling", "cancelled"}
    assert "PID" not in out
