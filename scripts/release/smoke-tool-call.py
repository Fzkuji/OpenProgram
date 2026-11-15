"""One real tool call through an installed OpenProgram, with no provider account.

    python smoke-tool-call.py --openprogram PATH --home DIR [--in-process]

A loopback server stands in for an OpenAI-compatible model. First the
prompt arrives through a pipe (``echo hi | openprogram --print -``), which
on Windows reads standard input from a pipe that ``select`` cannot wait on;
the stand-in answers it plainly. Then, asked for the
smoke turn, it calls the command tool (``bash``) once with a command every
shell the command tool may use reads the same way (bash, Git Bash,
PowerShell); given the tool's result it answers. The smoke passes when the
command's UTF-8 output and its exit code 3 reached the model and the reply
came back, which needs the installed runtime, its shell resolution, the
command backend and the agent loop all to work.

``DIR`` is the home folder of the install under test: the command runs with
it as its home, so its state is ``DIR/.openprogram``, where the provider is
written into ``config.json``; a running worker reads it on the next turn.
With no worker, or with ``--in-process``, the turn runs in the command's own
process.

Only the standard library is used, so any Python 3.9+ on the runner can run
it. Release workflows run it after installing a package; a failure keeps
that package out of the release.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROVIDER_ID = "openprogram-smoke"
MODEL_ID = "smoke-model"
TURN_MARKER = "OPENPROGRAM-SMOKE-TOOL-CALL"
FINAL_ANSWER = "OPENPROGRAM-SMOKE-FINAL"
# `echo` is Write-Output in PowerShell; `exit 3` ends either shell with 3.
COMMAND = "echo 你好; exit 3"
EXPECTED_TEXT = "你好"
EXPECTED_EXIT = "3"
# How the command tool reports an exit code: "Process exited with code 3"
# for a finished command, "exit_code=3" for a failed one.
EXIT_REPORTED = re.compile(r"(?:\bcode |\bexit_code=)" + EXPECTED_EXIT + r"\b")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


class StubModel:
    """An OpenAI-compatible chat completions server on loopback."""

    def __init__(self) -> None:
        self.turn: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server hook
                self._send(200, "application/json", json.dumps({"object": "list", "data": [
                    {"id": MODEL_ID, "object": "model", "owned_by": PROVIDER_ID}]}).encode())

            def do_POST(self) -> None:  # noqa: N802 - http.server hook
                try:
                    size = int(self.headers.get("content-length", "0"))
                    request = json.loads(self.rfile.read(size))
                    if not isinstance(request, dict):
                        raise ValueError
                except ValueError:
                    self._send(400, "application/json", b'{"error":{"message":"invalid request"}}')
                    return
                message = owner.reply(request)
                finish = "tool_calls" if message.get("tool_calls") else "stop"
                if not request.get("stream"):
                    self._send(200, "application/json", json.dumps({
                        "id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
                        "created": int(time.time()), "model": MODEL_ID,
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }).encode())
                    return
                ident = f"chatcmpl-{uuid.uuid4().hex}"

                def chunk(delta: dict[str, Any], done: str | None = None) -> bytes:
                    body: dict[str, Any] = {"id": ident, "object": "chat.completion.chunk",
                                            "created": int(time.time()), "model": MODEL_ID,
                                            "choices": [{"index": 0, "delta": delta, "finish_reason": done}]}
                    if done:
                        body["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
                    return f"data: {json.dumps(body)}\n\n".encode()

                frames = [chunk({"role": "assistant", "content": ""})]
                if message.get("tool_calls"):
                    call = message["tool_calls"][0]
                    frames.append(chunk({"tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                                         "function": call["function"]}]}))
                else:
                    frames.append(chunk({"content": message["content"]}))
                frames.append(chunk({}, finish))
                frames.append(b"data: [DONE]\n\n")
                payload = b"".join(frames)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send(self, status: int, kind: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def reply(self, request: dict[str, Any]) -> dict[str, Any]:
        messages = [m for m in request.get("messages") or [] if isinstance(m, dict)]
        offered = [str((tool.get("function") or {}).get("name"))
                   for tool in request.get("tools") or [] if isinstance(tool, dict)]
        last = messages[-1] if messages else {}
        in_turn = any(m.get("role") == "user" and TURN_MARKER in _text(m.get("content"))
                      for m in messages)
        if in_turn and last.get("role") == "tool":
            with self._lock:
                self.turn.append({"kind": "result", "content": _text(last.get("content"))})
            return {"role": "assistant", "content": FINAL_ANSWER}
        if last.get("role") == "user" and TURN_MARKER in _text(last.get("content")):
            with self._lock:
                self.turn.append({"kind": "call", "offered": offered})
            if "bash" not in offered:
                return {"role": "assistant", "content": "the command tool was not offered"}
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex[:16]}", "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": COMMAND})},
            }]}
        return {"role": "assistant", "content": "ok"}

    def __enter__(self) -> "StubModel":
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def write_provider(state_dir: Path, base_url: str) -> None:
    """Add the stub as a local custom provider, keeping the rest of the config."""
    path = state_dir / "config.json"
    config: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(loaded, dict):
            config = loaded
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    providers[PROVIDER_ID] = {
        "enabled": True, "source": "custom", "local": True, "label": "Smoke", "base_url": base_url,
        "models": [{
            "id": MODEL_ID, "name": "Smoke model", "provider": PROVIDER_ID, "api": "openai-completions",
            "base_url": base_url, "enabled": True, "source": "manual", "input": ["text"],
            "context_window": 200_000, "max_tokens": 4096,
        }],
    }
    config["providers"] = providers
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.smoke.tmp")
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def check(turn: list[dict[str, Any]], transcript: list[dict[str, Any]], reply: str) -> list[str]:
    """What went wrong, or nothing."""
    problems = []
    kinds = [item["kind"] for item in turn]
    if kinds[:2] != ["call", "result"]:
        problems.append(f"the model did not get a tool call and its result: {kinds}")
        return problems
    result = turn[1]["content"]
    if EXPECTED_TEXT not in result:
        problems.append(f"the command's UTF-8 output did not reach the model: {result[:400]!r}")
    if not EXIT_REPORTED.search(result):
        problems.append(f"the command's exit code {EXPECTED_EXIT} did not reach the model: {result[:400]!r}")
    if FINAL_ANSWER not in reply:
        problems.append(f"the reply did not come back: {reply[:400]!r}")
    if not any("bash" in json.dumps(record, ensure_ascii=False) for record in transcript):
        problems.append("the transcript has no command tool call")
    return problems


def _run(command: str | list[str], *, env: dict[str, str], cwd: str, input: str | None,
         timeout: float) -> subprocess.CompletedProcess:
    """Run one turn; a turn that hangs is reported with what it printed, as exit -1."""
    try:
        return subprocess.run(command, env=env, cwd=cwd, input=input,
                              stdin=subprocess.DEVNULL if input is None else None,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        def text(value: str | bytes | None) -> str:
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
        print(f"FAILED: the turn did not finish in {timeout:.0f}s")
        return subprocess.CompletedProcess(command, -1, text(exc.stdout), text(exc.stderr))


def utf8_output() -> None:
    """Print UTF-8 whatever the console code page.

    On a Windows runner standard output is a pipe in the ANSI code page
    (cp1252), which cannot hold the user folder's name or the command's
    output. The workflows do not set PYTHONUTF8, because the product's own
    Pythons must prove they need no help from the environment.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--openprogram", required=True, help="the installed openprogram command")
    parser.add_argument("--home", required=True, type=Path, help="the install's home folder")
    parser.add_argument("--in-process", action="store_true", help="run the turn in the command's process")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)

    with StubModel() as model, tempfile.TemporaryDirectory() as scratch:
        home = args.home.resolve()
        write_provider(home / ".openprogram", model.base_url)
        transcript_path = Path(scratch) / "transcript.jsonl"
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        env.pop("OPENPROGRAM_PROFILE", None)
        if args.in_process:
            env["OPENPROGRAM_PRINT_IN_PROCESS"] = "1"
        program = str(Path(args.openprogram).resolve()) if Path(args.openprogram).exists() else args.openprogram
        def runnable(command: list[str]) -> str | list[str]:
            if program.lower().endswith((".cmd", ".bat")):
                # A batch file runs in cmd.exe; /s with one outer pair of quotes
                # keeps every quote of the command line as it was written.
                comspec = os.environ.get("COMSPEC", "cmd.exe")
                return f'"{comspec}" /d /s /c "{subprocess.list2cmdline(command)}"'
            return command

        # The two runs are independent: a failed pipe still lets the tool call
        # run, so one run of a workflow reports both.
        failed = False
        piped = _run(runnable([program, "--print", "-", "--model", f"{PROVIDER_ID}/{MODEL_ID}"]),
                     env=env, cwd=scratch, input="hi\n", timeout=args.timeout)
        if piped.returncode != 0 or "ok" not in piped.stdout:
            failed = True
            print(f"FAILED: a prompt piped to --print did not come back: exit {piped.returncode}")
            print("stdout:", piped.stdout[-2000:], "stderr:", piped.stderr[-4000:], sep="\n")
        else:
            print("a prompt piped to --print came back")
        command = [program, "--print", f"{TURN_MARKER}: run the command tool once",
                   "--model", f"{PROVIDER_ID}/{MODEL_ID}", "--transcript-file", str(transcript_path)]
        run_as = runnable(command)
        started = time.monotonic()
        done = _run(run_as, env=env, cwd=scratch, input=None, timeout=args.timeout)
        seconds = time.monotonic() - started
        transcript = []
        if transcript_path.is_file():
            for line in transcript_path.read_text(encoding="utf-8").splitlines():
                try:
                    transcript.append(json.loads(line))
                except ValueError:
                    pass
        problems = check(model.turn, transcript, done.stdout)
        if done.returncode != 0:
            problems.insert(0, f"--print exited {done.returncode}")
        print(f"one tool call through {args.openprogram}: {seconds:.1f}s, exit {done.returncode}")
        if problems:
            print("stdout:", done.stdout[-2000:], sep="\n")
            print("stderr:", done.stderr[-4000:], sep="\n")
            for problem in problems:
                print(f"FAILED: {problem}")
            return 1
        print(f"the command's output and exit code {EXPECTED_EXIT} reached the model; the reply came back")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
