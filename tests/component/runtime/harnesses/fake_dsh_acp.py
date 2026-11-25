"""A stand-in for ``dsh --profile acp`` speaking ACP's JSON-RPC 2.0 lines.

``FAKE_DSH_SCENARIO`` names a JSON file: a list with one entry per
``session/prompt``, each a list of steps:

* ``{"update": {...}}`` sends a ``session/update`` of the prompt's session;
* ``{"permission": "<toolCallId>"}`` asks ``session/request_permission`` and waits for the answer;
* ``{"wait_cancel": true}`` reads until ``session/cancel`` and ends the prompt as ``cancelled``;
* ``{"error": "<message>"}`` fails the prompt with that message.

Otherwise the prompt ends as ``end_turn``. A ``session/resume`` of the id
``gone`` fails. Every line read and written is appended to ``FAKE_DSH_LOG``.
"""
from __future__ import annotations

import json
import os
import sys

LOG = os.environ.get("FAKE_DSH_LOG")
SCENARIO = os.environ.get("FAKE_DSH_SCENARIO")
_prompts = 0
_sessions = 0

OPTIONS = [
    {"id": "model", "name": "Model", "type": "select", "currentValue": '["deepseek-official","deepseek-flash"]',
     "options": [{"group": "deepseek-official", "name": "DeepSeek", "options": [
         {"value": '["deepseek-official","deepseek-flash"]', "name": "DeepSeek Flash"},
         {"value": '["deepseek-official","deepseek-pro"]', "name": "DeepSeek Pro"}]}]},
    {"id": "reasoning_effort", "name": "Effort", "type": "select", "currentValue": "high",
     "options": [{"value": v, "name": v} for v in ("off", "low", "high", "max")]},
]


def log(direction: str, message: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"dir": direction, "pid": os.getpid(), **message}) + "\n")


def send(message: dict) -> None:
    message = {"jsonrpc": "2.0", **message}
    log("out", message)
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read() -> dict:
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log("in", message)
    return message


def scenario() -> list:
    if SCENARIO and os.path.exists(SCENARIO):
        return json.loads(open(SCENARIO).read())
    return []


def run_prompt(request_id, session_id: str) -> None:
    global _prompts
    steps = scenario()[_prompts] if _prompts < len(scenario()) else []
    _prompts += 1
    for step in steps:
        if "update" in step:
            send({"method": "session/update", "params": {"sessionId": session_id, "update": step["update"]}})
        elif "permission" in step:
            send({"id": 900 + _prompts, "method": "session/request_permission", "params": {
                "sessionId": session_id, "toolCall": {"toolCallId": step["permission"]},
                "options": [{"optionId": "allow-once", "kind": "allow_once", "name": "Allow"},
                            {"optionId": "reject-once", "kind": "reject_once", "name": "Reject"}]}})
            while "result" not in read():
                pass
        elif step.get("wait_cancel"):
            while read().get("method") != "session/cancel":
                pass
            send({"id": request_id, "result": {"stopReason": "cancelled"}})
            return
        elif "error" in step:
            send({"id": request_id, "error": {"code": -32603, "message": f"Internal error: turn failed: {step['error']}"}})
            return
    send({"id": request_id, "result": {"stopReason": "end_turn"}})


def main() -> None:
    global _sessions
    while True:
        message = read()
        method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if request_id is None:
            continue
        if method == "initialize":
            send({"id": request_id, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            _sessions += 1
            send({"id": request_id, "result": {"sessionId": f"dsh-{os.getpid()}-{_sessions}", "configOptions": OPTIONS}})
        elif method == "session/resume":
            if params.get("sessionId") == "gone":
                send({"id": request_id, "error": {"code": -32603, "message": "session not found"}})
            else:
                send({"id": request_id, "result": {"configOptions": OPTIONS}})
        elif method == "session/set_config_option":
            send({"id": request_id, "result": {"configOptions": OPTIONS}})
        elif method == "session/prompt":
            run_prompt(request_id, params.get("sessionId"))
        else:
            send({"id": request_id, "result": {}})


if __name__ == "__main__":
    main()
