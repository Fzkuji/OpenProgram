"""A stand-in for ``claude -p`` in stream-JSON mode.

``FAKE_CLAUDE_SCENARIO`` names a JSON file: a list with one entry per user
message, each a list of steps:

* ``{"send": {...}}`` writes that message (``$SESSION`` is filled in);
* ``{"ask": {...}}`` sends a ``can_use_tool`` control request and waits for the answer;
* ``{"wait_interrupt": true}`` reads until an ``interrupt`` control request, then ends the turn;
* ``{"fail": "<text>"}`` ends the turn with an error result.

Otherwise the turn ends with a successful ``result``. The session id is
the one given by ``--session-id`` or ``--resume``, or a new one for
``--fork-session``. Every line read and the arguments are appended to
``FAKE_CLAUDE_LOG``.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

LOG = os.environ.get("FAKE_CLAUDE_LOG")
SCENARIO = os.environ.get("FAKE_CLAUDE_SCENARIO")
STATE = os.environ.get("FAKE_CLAUDE_STATE")


def log(entry: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), **entry}) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read() -> dict:
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log({"in": message})
    return message


def next_turn() -> int:
    count = int(open(STATE).read() or 0) if STATE and os.path.exists(STATE) else 0
    if STATE:
        with open(STATE, "w") as handle:
            handle.write(str(count + 1))
    return count


def arg(name: str):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def main() -> None:
    log({"argv": sys.argv[1:], "cwd": os.getcwd()})
    session = arg("--session-id") or arg("--resume")
    if "--fork-session" in sys.argv:
        session = str(uuid.uuid4())
    if session == "gone":
        sys.stderr.write("No conversation found with session ID: gone\n")
        sys.exit(1)
    started = False
    while True:
        message = read()
        if message.get("type") == "control_request":
            request = message.get("request") or {}
            answer = {"models": [{"value": "default"}]} if request.get("subtype") == "initialize" else {}
            send({"type": "control_response", "response": {"subtype": "success",
                                                           "request_id": message["request_id"], "response": answer}})
            continue
        if message.get("type") != "user":
            continue
        if not started:
            send({"type": "system", "subtype": "init", "session_id": session})
            started = True
        steps = json.loads(open(SCENARIO).read())
        index = next_turn()
        turn = steps[index] if index < len(steps) else []
        ended = False
        for step in turn:
            if "send" in step:
                send(json.loads(json.dumps(step["send"]).replace("$SESSION", session or "")))
            elif "ask" in step:
                send({"type": "control_request", "request_id": f"perm-{index}", "request": step["ask"]})
                while True:
                    answer = read()
                    if answer.get("type") == "control_response":
                        break
            elif step.get("wait_interrupt"):
                while True:
                    request = read()
                    if request.get("type") == "control_request" and request["request"].get("subtype") == "interrupt":
                        send({"type": "control_response", "response": {
                            "subtype": "success", "request_id": request["request_id"], "response": {}}})
                        break
                send({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session})
                ended = True
                break
            elif "fail" in step:
                send({"type": "result", "subtype": "success", "is_error": True, "result": step["fail"],
                      "session_id": session})
                ended = True
                break
        if not ended:
            send({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": session,
                  "modelUsage": {"claude-test": {"contextWindow": 200000}}})


if __name__ == "__main__":
    main()
