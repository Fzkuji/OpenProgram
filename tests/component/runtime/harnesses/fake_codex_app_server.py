"""A stand-in for ``codex app-server`` speaking its JSON-RPC lines.

Run as ``python fake_codex_app_server.py app-server``. ``FAKE_CODEX_SCENARIO``
names a JSON file: a list with one entry per ``turn/start`` (counted across
processes in ``FAKE_CODEX_STATE``), each a list of steps:

* ``{"send": {...}}`` sends a notification (``$TURN`` and ``$THREAD`` are filled in);
* ``{"request": {...}}`` sends a request of Codex's and waits for its answer;
* ``{"wait_for": "turn/interrupt"}`` reads until that method arrives;
* ``{"hang": true}`` stops answering (a Codex that ignores Stop);
* ``{"exit": 3}`` exits the process.

The turn completes with ``{"complete": "<status>"}`` or, after the last
step, as ``completed``. Every line read and written is appended to
``FAKE_CODEX_LOG``.
"""
from __future__ import annotations

import json
import os
import sys
import time

LOG = os.environ.get("FAKE_CODEX_LOG")
SCENARIO = os.environ.get("FAKE_CODEX_SCENARIO")
STATE = os.environ.get("FAKE_CODEX_STATE")
_pending_requests = iter(range(1000, 10_000))
_threads = 0
_active_turn = None
_thread = None


def log(direction: str, message: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"dir": direction, "pid": os.getpid(), **message}) + "\n")


def send(message: dict) -> None:
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


def next_turn_index() -> int:
    count = 0
    if STATE and os.path.exists(STATE):
        count = int(open(STATE).read() or 0)
    if STATE:
        with open(STATE, "w") as handle:
            handle.write(str(count + 1))
    return count


def fill(value, turn_id: str, thread_id: str):
    text = json.dumps(value).replace("$TURN", turn_id).replace("$THREAD", thread_id)
    return json.loads(text)


def answer_simple(message: dict) -> bool:
    """Answer a request that needs no turn; False for one that does."""
    global _threads, _active_turn
    method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if request_id is None:
        return True
    if method == "initialize":
        send({"id": request_id, "result": {"userAgent": "fake-codex/0.0"}})
    elif method == "model/list":
        send({"id": request_id, "result": {"data": [
            {"id": "gpt-fake", "model": "gpt-fake", "displayName": "GPT Fake", "isDefault": True,
             "hidden": False, "defaultReasoningEffort": "medium", "description": "",
             "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"},
                                           {"reasoningEffort": "high"}], "supportsPersonality": True},
            {"id": "gpt-hidden", "displayName": "Hidden", "isDefault": False, "hidden": True,
             "supportedReasoningEfforts": []},
        ], "nextCursor": None}})
    elif method == "config/read":
        send({"id": request_id, "result": {"config": {"personality": "pragmatic",
                                                       "profiles": {"fast": {"model": "gpt-fake"}}}}})
    elif method == "account/read":
        send({"id": request_id, "result": {"account": {"type": "chatgpt", "planType": "plus",
                                                        "email": "someone@example.com"}}})
    elif method in ("thread/start", "thread/fork"):
        _threads += 1
        send({"id": request_id, "result": {"thread": {"id": f"thread-{os.getpid()}-{_threads}"}}})
    elif method == "thread/resume":
        if os.environ.get("FAKE_CODEX_RESUME_FAILS"):
            send({"id": request_id, "error": {"code": -32600, "message": "no rollout found"}})
        else:
            send({"id": request_id, "result": {"thread": {"id": params.get("threadId")}}})
    elif method == "turn/interrupt":
        send({"id": request_id, "result": {}})
    elif method == "turn/steer":
        if _active_turn and params.get("expectedTurnId") == _active_turn:
            send({"id": request_id, "result": {"turnId": _active_turn}})
        else:
            send({"id": request_id, "error": {"code": -32600, "message": "no active turn"}})
    elif method == "turn/start":
        return False
    else:
        send({"id": request_id, "error": {"code": -32601, "message": f"unknown {method}"}})
    return True


def run_turn(message: dict) -> None:
    global _active_turn, _thread
    index = next_turn_index()
    turn_id = f"turn-{index}"
    _thread = (message.get("params") or {}).get("threadId") or ""
    _active_turn = turn_id
    send({"id": message["id"], "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
    send({"method": "turn/started", "params": {"threadId": _thread, "turn": {"id": turn_id}}})
    steps = json.load(open(SCENARIO)) if SCENARIO else []
    steps = steps[index] if index < len(steps) else []
    status = "completed"
    error = None
    for step in steps:
        step = fill(step, turn_id, _thread)
        if "send" in step:
            send(step["send"])
        elif "request" in step:
            request_id = next(_pending_requests)
            send({"id": request_id, **step["request"]})
            while True:
                incoming = read()
                if incoming.get("id") == request_id and "method" not in incoming:
                    break
                answer_simple(incoming)
        elif "wait_for" in step:
            while True:
                incoming = read()
                answer_simple(incoming)
                if incoming.get("method") == step["wait_for"]:
                    break
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif step.get("hang"):
            while True:
                incoming = read()
                if incoming.get("method") != "turn/interrupt":
                    answer_simple(incoming)
        elif "exit" in step:
            sys.exit(step["exit"])
        elif "complete" in step:
            status = step["complete"]
            error = step.get("error")
    _active_turn = None
    send({"method": "turn/completed", "params": {"threadId": _thread, "turn": {
        "id": turn_id, "status": status, "error": error}}})


def main() -> None:
    if "--version" in sys.argv:
        print("codex-cli 9.9.9")
        return
    log("start", {"argv": sys.argv[1:]})
    while True:
        message = read()
        if not answer_simple(message):
            run_turn(message)


if __name__ == "__main__":
    main()
