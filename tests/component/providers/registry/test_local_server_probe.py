"""A local server counts as running when it answers its model list on this machine."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from openprogram.models.listing import detected


def test_a_local_server_counts_when_it_answers():
    class Models(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Models)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert detected.local_server_running("ollama", f"http://127.0.0.1:{port}/v1") is True
    finally:
        server.shutdown()
        server.server_close()
    assert detected.local_server_running("ollama", f"http://127.0.0.1:{port}/v1") is False
