"""A stand-in for ``gemini --acp`` speaking ACP's JSON-RPC 2.0 lines as Gemini CLI does.

Its sign-in follows Gemini CLI's ACP server (packages/cli/src/acp/acpSessionManager.ts,
v0.71.1): the auth type is ``security.auth.selectedType`` of the merged settings
(system settings over the user's over system defaults, the files Gemini CLI reads,
``GEMINI_CLI_HOME`` and the ``GEMINI_CLI_SYSTEM_*_PATH`` variables honoured);
``session/new`` falls back to ``gemini-api-key``, ``session/load`` refuses
without a chosen type ("Authentication required"), and ``gemini-api-key`` needs
``GEMINI_API_KEY`` in the environment or ``~/.gemini/.env``. ``authenticate``
writes its method into the user's ``settings.json``, as the real one does.

``session/load`` of a known id answers first and replays the session's history
behind the answer, as releases before 0.71 do (the hardest order for a client);
a ``session/load`` of ``gone`` fails.

``FAKE_GEMINI_SCENARIO`` names a JSON file: a list with one entry per
``session/prompt`` (counted across processes), each a list of steps:

* ``{"update": {...}}`` sends a ``session/update`` of the prompt's session;
* ``{"permission": {...}}`` asks ``session/request_permission`` about that tool call and waits;
* ``{"wait_cancel": true}`` reads until ``session/cancel`` and ends the prompt as ``cancelled``;
* ``{"error": "<message>"}`` fails the prompt with that message.

Otherwise the prompt ends as ``end_turn`` with its token use. Every line read and
written is appended to ``FAKE_GEMINI_LOG``, with the key it was given and its
system-defaults file.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

LOG = os.environ.get("FAKE_GEMINI_LOG")
SCENARIO = os.environ.get("FAKE_GEMINI_SCENARIO")
HOME = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"

MODELS = {"availableModels": [{"modelId": "auto-gemini-2.5", "name": "Auto (Gemini 2.5)"},
                              {"modelId": "gemini-2.5-pro", "name": "gemini-2.5-pro"},
                              {"modelId": "gemini-2.5-flash", "name": "gemini-2.5-flash"}],
          "currentModelId": "auto-gemini-2.5"}
OPTIONS = [{"optionId": "proceed_always", "kind": "allow_always", "name": "Always Allow"},
           {"optionId": "proceed_once", "kind": "allow_once", "name": "Allow"},
           {"optionId": "cancel", "kind": "reject_once", "name": "Reject"}]
HISTORY = [
    {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "old question"}},
    {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old answer"}},
    {"sessionUpdate": "tool_call", "toolCallId": "old-1", "status": "completed", "title": "old tool",
     "kind": "read", "content": []},
    {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old ending"}},
]
_api_key = None  # one ``authenticate`` passed


def log(direction: str, message: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "dir": direction, "pid": os.getpid(), "argv": sys.argv[1:],
                "key": os.environ.get("GEMINI_API_KEY") or None,
                "defaults": os.environ.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH"), **message}) + "\n")


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


def _settings(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    text = re.sub(r"^\s*//[^\n]*", "", text, flags=re.M)
    try:
        return json.loads(text)
    except ValueError:
        return {}


def _system_settings() -> Path:
    return Path(os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH") or "/nonexistent/settings.json")


def selected_type():
    defaults = Path(os.environ.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH")
                    or _system_settings().parent / "system-defaults.json")
    for path in (_system_settings(), HOME / "settings.json", defaults):
        chosen = ((_settings(path).get("security") or {}).get("auth") or {}).get("selectedType")
        if chosen:
            return chosen
    return None


def has_key() -> bool:
    if _api_key or os.environ.get("GEMINI_API_KEY"):
        return True
    try:
        return any(line.startswith("GEMINI_API_KEY=") for line in (HOME / ".env").read_text().splitlines())
    except OSError:
        return False


def auth_error(auth_type):
    if auth_type == "gemini-api-key" and not has_key():
        return "Gemini API key is missing or not configured."
    return None


def scenario() -> list:
    if SCENARIO and os.path.exists(SCENARIO):
        return json.loads(open(SCENARIO).read())
    return []


def prompt_index() -> int:
    """This prompt's place among every process's: each turn starts a process of its own."""
    if not LOG or not os.path.exists(LOG):
        return 0
    rows = [json.loads(line) for line in open(LOG, encoding="utf-8")]
    return sum(1 for r in rows if r["dir"] == "in" and r.get("method") == "session/prompt") - 1


def update(session_id: str, body: dict) -> None:
    send({"method": "session/update", "params": {"sessionId": session_id, "update": body}})


def run_prompt(request_id, session_id: str) -> None:
    index = prompt_index()
    steps = scenario()[index] if index < len(scenario()) else []
    for step in steps:
        if "update" in step:
            update(session_id, step["update"])
        elif "permission" in step:
            send({"id": 900 + index, "method": "session/request_permission", "params": {
                "sessionId": session_id, "toolCall": step["permission"], "options": OPTIONS}})
            while "result" not in read():
                pass
        elif step.get("wait_cancel"):
            while read().get("method") != "session/cancel":
                pass
            send({"id": request_id, "result": {"stopReason": "cancelled"}})
            return
        elif "error" in step:
            send({"id": request_id, "error": {"code": 500, "message": step["error"]}})
            return
    send({"id": request_id, "result": {"stopReason": "end_turn", "usage": {
        "inputTokens": 1500, "outputTokens": 40, "cachedReadTokens": 1000, "totalTokens": 1540}}})


def main() -> None:
    global _api_key
    sessions = 0
    while True:
        message = read()
        method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if request_id is None:
            continue
        if method == "initialize":
            send({"id": request_id, "result": {
                "protocolVersion": 1, "agentInfo": {"name": "gemini-cli", "title": "Gemini CLI", "version": "0.99.0"},
                "authMethods": [{"id": "oauth-personal", "name": "Log in with Google"},
                                {"id": "gemini-api-key", "name": "Gemini API key"}],
                "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True}}}})
        elif method == "authenticate":
            _api_key = ((params.get("_meta") or {}).get("api-key")) or _api_key
            settings = _settings(HOME / "settings.json")
            settings.setdefault("security", {}).setdefault("auth", {})["selectedType"] = params.get("methodId")
            (HOME / "settings.json").write_text(json.dumps(settings))
            send({"id": request_id, "result": {}})
        elif method == "session/new":
            problem = auth_error(selected_type() or "gemini-api-key")
            if problem:
                send({"id": request_id, "error": {"code": -32000, "message": problem}})
                continue
            sessions += 1
            send({"id": request_id, "result": {"sessionId": f"g-{os.getpid()}-{sessions}", "models": MODELS}})
        elif method == "session/load":
            chosen = selected_type()
            if not chosen or auth_error(chosen):
                send({"id": request_id, "error": {"code": -32000, "message": "Authentication required"}})
            elif params.get("sessionId") == "gone":
                send({"id": request_id, "error": {"code": -32603, "message": "No session found"}})
            else:
                send({"id": request_id, "result": {"models": MODELS}})
                for body in HISTORY:  # behind the answer, as releases before 0.71 send it
                    time.sleep(0.05)
                    update(params["sessionId"], body)
        elif method == "session/prompt":
            run_prompt(request_id, params.get("sessionId"))
        else:
            send({"id": request_id, "result": {}})


if __name__ == "__main__":
    main()
