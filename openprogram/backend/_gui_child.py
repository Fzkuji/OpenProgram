"""Copied into the isolated scratch directory; never imported by the host.

This protocol implementation is untrusted once user code runs. Host validation
and OS isolation, not this module's namespace, enforce the execution boundary.
"""
import ast
import asyncio
import inspect
import json
import struct
import sys
import threading

_reader = sys.stdin.buffer
_writer = sys.stdout.buffer
_namespace = {"__name__": "__main__"}
_sequence = 0
_rpc_sequence = 0
_send_lock = threading.Lock()
_rpc_lock = threading.Lock()


def _send(message):
    payload = json.dumps(message, ensure_ascii=True).encode()
    with _send_lock:
        _writer.write(struct.pack("!I", len(payload)) + payload)
        _writer.flush()


class _Output:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        for start in range(0, len(text), 4096):
            _send({"id": _sequence, "type": "output", "stream": self.stream,
                   "text": text[start:start + 4096]})
        return len(text)

    def flush(self):
        pass


class _UI:
    async def call(self, resource, method, arguments=None, observation=None):
        global _rpc_sequence
        with _rpc_lock:
            _rpc_sequence += 1
            request_id = _rpc_sequence
            _send({"id": _sequence, "type": "rpc", "request_id": request_id,
                   "request": {"resource": resource, "method": method,
                               "arguments": {} if arguments is None else arguments, "observation": observation}})
            length, = struct.unpack("!I", _reader.read(4))
            reply = json.loads(_reader.read(length))
            if reply.get("id") != _sequence or reply.get("request_id") != request_id:
                raise RuntimeError("invalid GUI broker response")
            if reply.get("error") is not None:
                raise RuntimeError(reply["error"])
            return reply["value"]


_namespace["ui"] = _UI()
sys.stdout = _Output("stdout")
sys.stderr = _Output("stderr")
while True:
    header = _reader.read(4)
    if not header:
        break
    length, = struct.unpack("!I", header)
    request = json.loads(_reader.read(length))
    _sequence = request["id"]
    error = None
    try:
        code = compile(request["code"], "<gui_exec>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        result = eval(code, _namespace)
        if inspect.isawaitable(result):
            asyncio.run(result)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    _send({"id": _sequence, "type": "done", "error": error})
