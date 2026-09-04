from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


HARNESS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "openprogram/programs/applications/gui_harness"
)


@pytest.fixture
def harness_on_path():
    if not (HARNESS_ROOT / "gui_harness" / "main.py").is_file():
        pytest.skip("gui_harness checkout is not present")
    root = str(HARNESS_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


@pytest.mark.parametrize("http_status,returncode", [(200, 0), (200, 1), (500, 0)])
def test_osworld_http_protocol_supports_screenshot_and_execute(
    harness_on_path, tmp_path, http_status, returncode,
):
    from gui_harness.action.input import VMTarget
    from gui_harness.adapters.vm_adapter import _vm_screenshot

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path, None))
            if self.path != "/screenshot":
                self.send_error(404)
                return
            body = b"\x89PNG\r\n\x1a\nprotocol-test"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            requests.append(("POST", self.path, payload))
            body = json.dumps({"output": "ok", "returncode": returncode}).encode()
            self.send_response(http_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        screenshot_path = _vm_screenshot(
            endpoint, str(tmp_path / "osworld-screen.png"),
        )
        if http_status != 200 or returncode:
            with pytest.raises(RuntimeError):
                VMTarget(endpoint)._exec("echo ok")
        else:
            execute_result = VMTarget(endpoint)._exec("echo ok")
            assert execute_result == {"output": "ok", "returncode": 0}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert Path(screenshot_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert requests[0] == ("GET", "/screenshot", None)
    assert requests[1][0:2] == ("POST", "/execute")
    assert requests[1][2]["command"] == "echo ok"
    assert requests[1][2]["shell"] is True
    assert requests[1][2]["timeout"] == 30
