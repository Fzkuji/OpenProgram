"""
Session management — persist follow-up state across CLI invocations.

When a function calls ask_user() during CLI execution, the process can't
just block on stdin if the caller is an external agent. Instead:

  1. The CLI process stays alive, outputs a follow-up JSON to stdout.
  2. The agent reads the follow-up, gets the answer from the user.
  3. The agent calls `agentic resume <session_id> <answer>`.
  4. The resume command writes the answer to a file.
  5. The original process picks it up, the function thread resumes.

Session directory layout:
    ~/.agentic/sessions/<id>/
        meta.json       — {"question": "...", "pid": 12345, "status": "waiting"}
        answer          — written by `resume` command (trigger file)

The original process polls for `answer`. When found, it feeds the answer
to the FollowUp object, and the function continues from where it paused.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid


# Kept as a legacy constant for back-compat; _sessions_dir() is the
# live accessor that respects --profile / OPENPROGRAM_PROFILE.
SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".agentic", "sessions")


def _sessions_dir() -> str:
    from openprogram.paths import get_sessions_dir
    return str(get_sessions_dir())


class Session:
    """Manage a single follow-up session with file-based IPC."""

    def __init__(self, session_id: str = None):
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.dir = os.path.join(_sessions_dir(), self.session_id)

    def _meta_path(self):
        return os.path.join(self.dir, "meta.json")

    def _answer_path(self):
        return os.path.join(self.dir, "answer")

    def exists(self) -> bool:
        return os.path.isdir(self.dir)

    def write_meta(self, question: str):
        """Write session metadata (called by the original process)."""
        os.makedirs(self.dir, exist_ok=True)
        with open(self._meta_path(), "w") as f:
            json.dump({
                "question": question,
                "pid": os.getpid(),
                "status": "waiting",
                "created": time.time(),
            }, f, ensure_ascii=False)

    def read_meta(self) -> dict | None:
        """Read session metadata."""
        try:
            with open(self._meta_path()) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def wait_for_answer(self, timeout: float = 300) -> str | None:
        """Block until the answer file appears (called by original process).

        Returns the answer text, or None on timeout.
        """
        path = self._answer_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                with open(path) as f:
                    answer = f.read()
                os.remove(path)  # consumed
                return answer
            time.sleep(0.3)
        return None

    def send_answer(self, answer: str):
        """Write the answer file (called by `agentic resume`)."""
        os.makedirs(self.dir, exist_ok=True)
        with open(self._answer_path(), "w") as f:
            f.write(answer)

    def cleanup(self):
        """Remove the session directory."""
        shutil.rmtree(self.dir, ignore_errors=True)


def list_sessions() -> list[dict]:
    """List all active sessions with their metadata."""
    results = []
    if not os.path.isdir(_sessions_dir()):
        return results
    for name in os.listdir(_sessions_dir()):
        session = Session(name)
        meta = session.read_meta()
        if meta:
            meta["session_id"] = name
            results.append(meta)
    return results


def cleanup_stale_sessions(max_age: float = 3600):
    """Remove sessions older than max_age seconds."""
    if not os.path.isdir(_sessions_dir()):
        return
    now = time.time()
    for name in os.listdir(_sessions_dir()):
        session = Session(name)
        meta = session.read_meta()
        if meta and now - meta.get("created", 0) > max_age:
            session.cleanup()


def run_with_session(func, *args, **kwargs):
    """Run a function with session-based follow-up support.

    Integrates with ask_user() via a file-based handler:
    - When ask_user() is called, outputs a follow-up JSON to stdout
    - Waits for `agentic resume <id> <answer>` to provide the answer
    - Function resumes from where it paused

    For functions that don't call ask_user(), returns the result directly.

    Output protocol (one JSON object per line on stdout):
        {"type": "follow_up", "question": "...", "session": "<id>"}
        {"type": "result", "value": "..."}
        {"type": "error", "message": "..."}

    Returns:
        The function's return value (for programmatic use).
        Side effect: prints JSON lines to stdout (for CLI/agent use).
    """
    from openprogram.programs.functions.buildin.ask_user import set_ask_user

    session = Session()

    def _handler(question: str) -> str:
        """ask_user handler: output follow-up to stdout, wait for answer file."""
        session.write_meta(question)
        _output({"type": "follow_up", "question": question, "session": session.session_id})
        answer = session.wait_for_answer(timeout=300)
        if answer is None:
            session.cleanup()
            return ""
        return answer

    set_ask_user(_handler)
    try:
        result = func(*args, **kwargs)
        _output({"type": "result", "value": result})
        return result
    except Exception as e:
        _output({"type": "error", "message": str(e)})
        raise
    finally:
        set_ask_user(None)
        session.cleanup()


def _output(msg: dict):
    """Write a JSON line to stdout and flush."""
    print(json.dumps(msg, ensure_ascii=False, default=str))
    sys.stdout.flush()
