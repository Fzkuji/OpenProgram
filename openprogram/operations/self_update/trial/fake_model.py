"""The trial's one model: an OpenAI-compatible server on loopback.

A trial holds no credentials, so nothing it does can reach a real provider or
spend model budget. A turn whose latest user message carries ``TURN_MARKER``
gets exactly one tool call and then, once the tool's result comes back, a
final answer; every other request, such as a conversation title, gets a short
text. That is enough to prove the candidate's agent loop, tool dispatch and
persistence.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any
import uuid

TURN_MARKER = "OPENPROGRAM-TRIAL-TURN"
FINAL_ANSWER = "OPENPROGRAM-TRIAL-FINAL"
MODEL_ID = "trial-model"
PROVIDER_ID = "openprogram-trial"
_MAX_BODY = 64 * 1024 * 1024
# Read-only tools, most preferred first, and arguments each accepts.
_READ_TOOLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("list", {"path": "."}),
    ("glob", {"pattern": "*"}),
    ("read", {"file_path": "/etc/hosts"}),
)


def _choose_tool(offered: list[str]) -> tuple[str, dict[str, Any]] | None:
    for name, arguments in _READ_TOOLS:
        if name in offered:
            return name, arguments
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


class FakeModel:
    """A running server. ``requests`` holds what every request asked for."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_arguments: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - the http.server hook
                body = json.dumps({"object": "list", "data": [
                    {"id": MODEL_ID, "object": "model", "owned_by": PROVIDER_ID},
                ]}).encode()
                self._send(200, "application/json", body)

            def do_POST(self) -> None:  # noqa: N802 - the http.server hook
                try:
                    size = int(self.headers.get("content-length", "0"))
                    if not 0 <= size <= _MAX_BODY:
                        raise ValueError
                    request = json.loads(self.rfile.read(size))
                    if not isinstance(request, dict):
                        raise ValueError
                except ValueError:
                    self._send(400, "application/json", b'{"error":{"message":"invalid request"}}')
                    return
                message = owner._reply(request)
                if request.get("stream"):
                    self._stream(request, message)
                else:
                    body = json.dumps({
                        "id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
                        "created": int(time.time()), "model": MODEL_ID,
                        "choices": [{"index": 0, "message": message,
                                     "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }).encode()
                    self._send(200, "application/json", body)

            def _send(self, status: int, kind: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _stream(self, request: dict[str, Any], message: dict[str, Any]) -> None:
                identifier = f"chatcmpl-{uuid.uuid4().hex}"

                def chunk(delta: dict[str, Any], finish: str | None = None, usage: bool = False) -> bytes:
                    payload: dict[str, Any] = {
                        "id": identifier, "object": "chat.completion.chunk", "created": int(time.time()),
                        "model": MODEL_ID, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    if usage:
                        payload["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
                    return f"data: {json.dumps(payload)}\n\n".encode()

                frames = [chunk({"role": "assistant", "content": ""})]
                if message.get("tool_calls"):
                    call = message["tool_calls"][0]
                    frames.append(chunk({"tool_calls": [{
                        "index": 0, "id": call["id"], "type": "function",
                        "function": {"name": call["function"]["name"], "arguments": call["function"]["arguments"]},
                    }]}))
                    frames.append(chunk({}, "tool_calls", True))
                else:
                    frames.append(chunk({"content": message.get("content") or ""}))
                    frames.append(chunk({}, "stop", True))
                frames.append(b"data: [DONE]\n\n")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(sum(len(frame) for frame in frames)))
                self.end_headers()
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="trial-fake-model", daemon=True)

    def _reply(self, request: dict[str, Any]) -> dict[str, Any]:
        messages = request.get("messages") if isinstance(request.get("messages"), list) else []
        messages = [item for item in messages if isinstance(item, dict)]
        offered = [
            str(tool.get("function", {}).get("name"))
            for tool in request.get("tools") or [] if isinstance(tool, dict)
        ]
        last = messages[-1] if messages else {}
        reply: dict[str, Any] = {"role": "assistant", "content": "ok"}
        kind = "other"
        if last.get("role") == "tool":
            kind, reply = "final", {"role": "assistant", "content": FINAL_ANSWER}
        elif last.get("role") == "user" and TURN_MARKER in _text(last.get("content")):
            chosen = _choose_tool(offered)
            if chosen is None:
                kind, reply = "no_tool", {"role": "assistant", "content": "no read tool was offered"}
            else:
                kind, reply = "tool_call", {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call_{uuid.uuid4().hex[:16]}", "type": "function",
                    "function": {"name": chosen[0], "arguments": json.dumps(chosen[1])},
                }]}
        with self._lock:
            self.requests.append({
                "kind": kind, "tool_result": _text(last.get("content")) if kind == "final" else None,
                "tool": reply["tool_calls"][0]["function"]["name"] if kind == "tool_call" else None,
                "offered": offered,
            })
        return reply

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "FakeModel":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeModel":
        return self.start()

    def __exit__(self, *_arguments: Any) -> None:
        self.stop()

    def turn_requests(self) -> list[dict[str, Any]]:
        """The requests that belong to the trial's turn, in order."""
        with self._lock:
            return [item for item in self.requests if item["kind"] in {"tool_call", "final", "no_tool"}]
