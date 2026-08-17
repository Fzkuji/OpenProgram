"""
Tests for the unified follow-up system.

Three scenarios:
  1. Direct call — run_with_follow_up() returns FollowUp objects
  2. Session-based — file IPC for CLI resume across invocations
  3. Web UI — global handler via set_ask_user() (simulates _web_follow_up)
"""

import os
import queue
import shutil
import threading
import time

import pytest

from openprogram.programs.functions.agentic.ask_user import (
    FollowUp,
    ask_user,
    run_with_follow_up,
    set_ask_user,
)
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.session import (
    Session,
    SESSIONS_DIR,
    list_sessions,
    run_with_session,
)


# ---------------------------------------------------------------------------
# Test fixtures — simple functions that call ask_user()
# ---------------------------------------------------------------------------

def _simple_ask():
    """A plain function that asks one question."""
    name = ask_user("What is your name?")
    return f"Hello, {name}!"


def _multi_ask():
    """A function that asks multiple follow-up questions."""
    color = ask_user("Favorite color?")
    number = ask_user("Favorite number?")
    return f"{color}-{number}"


def _conditional_ask():
    """Only asks follow-up if input is ambiguous."""
    answer = ask_user("Is this clear? (yes/no)")
    if answer and answer.strip().lower() == "yes":
        return "Proceeding without clarification."
    detail = ask_user("Please provide more detail.")
    return f"Got detail: {detail}"


def _no_ask():
    """A function that never calls ask_user."""
    return 42


def _ask_then_fail():
    """Asks a question, then raises an error."""
    ask_user("Ready?")
    raise ValueError("something broke")


@agentic_function
def _agentic_ask(task):
    """An @agentic_function that calls ask_user."""
    answer = ask_user(f"Clarify: {task}")
    return f"Resolved: {answer}"


@agentic_function
def _parent_calls_child(task):
    """An @agentic_function that calls another function with ask_user."""
    # Simulates an agent calling a sub-function
    result = _agentic_ask(task=task)
    return f"Parent got: {result}"


# ===========================================================================
# Scenario 1: Direct call via run_with_follow_up()
# ===========================================================================

class TestRunWithFollowUp:
    """Test the programmatic follow-up interface."""

    def test_single_follow_up(self):
        result = run_with_follow_up(_simple_ask)
        assert isinstance(result, FollowUp)
        assert result.question == "What is your name?"
        final = result.answer("Alice")
        assert final == "Hello, Alice!"

    def test_multiple_follow_ups(self):
        result = run_with_follow_up(_multi_ask)
        assert isinstance(result, FollowUp)
        assert result.question == "Favorite color?"

        result = result.answer("blue")
        assert isinstance(result, FollowUp)
        assert result.question == "Favorite number?"

        final = result.answer("7")
        assert final == "blue-7"

    def test_no_follow_up(self):
        result = run_with_follow_up(_no_ask)
        assert result == 42
        assert not isinstance(result, FollowUp)

    def test_exception_after_follow_up(self):
        result = run_with_follow_up(_ask_then_fail)
        assert isinstance(result, FollowUp)
        with pytest.raises(ValueError, match="something broke"):
            result.answer("yes")

    def test_conditional_follow_up_short_path(self):
        result = run_with_follow_up(_conditional_ask)
        assert isinstance(result, FollowUp)
        final = result.answer("yes")
        assert final == "Proceeding without clarification."

    def test_conditional_follow_up_long_path(self):
        result = run_with_follow_up(_conditional_ask)
        assert isinstance(result, FollowUp)
        result = result.answer("no")
        assert isinstance(result, FollowUp)
        assert "more detail" in result.question
        final = result.answer("here are the details")
        assert final == "Got detail: here are the details"

    def test_follow_up_repr(self):
        result = run_with_follow_up(_simple_ask)
        assert "What is your name?" in repr(result)
        result.answer("test")  # clean up

    def test_agentic_function_with_follow_up(self):
        result = run_with_follow_up(_agentic_ask, task="ambiguous task")
        assert isinstance(result, FollowUp)
        assert "ambiguous task" in result.question
        final = result.answer("it means X")
        assert final == "Resolved: it means X"

    def test_nested_agentic_follow_up(self):
        """Follow-up in a child function bubbles up to run_with_follow_up caller."""
        result = run_with_follow_up(_parent_calls_child, task="vague")
        assert isinstance(result, FollowUp)
        assert "vague" in result.question
        final = result.answer("specific answer")
        assert "Resolved: specific answer" in final


# ===========================================================================
# Scenario 2: Session-based resume (file IPC)
# ===========================================================================

class TestSessionResume:
    """Test the file-based session IPC for CLI resume."""

    def setup_method(self):
        """Clean up any stale test sessions."""
        self._cleanup_dir = os.path.join(SESSIONS_DIR, "_test_")
        if os.path.exists(self._cleanup_dir):
            shutil.rmtree(self._cleanup_dir)

    def teardown_method(self):
        if os.path.exists(self._cleanup_dir):
            shutil.rmtree(self._cleanup_dir)

    def test_session_write_and_read_meta(self):
        session = Session("_test_meta")
        session.write_meta("What color?")
        meta = session.read_meta()
        assert meta["question"] == "What color?"
        assert meta["status"] == "waiting"
        assert "pid" in meta
        session.cleanup()

    def test_session_send_and_receive_answer(self):
        session = Session("_test_answer")
        session.write_meta("question")

        # Simulate resume in another thread
        def _resume():
            time.sleep(0.3)
            session.send_answer("the answer")

        t = threading.Thread(target=_resume)
        t.start()

        answer = session.wait_for_answer(timeout=5)
        assert answer == "the answer"
        t.join()
        session.cleanup()

    def test_session_timeout(self):
        session = Session("_test_timeout")
        session.write_meta("question")
        answer = session.wait_for_answer(timeout=0.5)
        assert answer is None
        session.cleanup()

    def test_session_not_exists(self):
        session = Session("_test_nonexistent")
        assert not session.exists()
        assert session.read_meta() is None

    def test_list_sessions_ignores_root_metadata_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "openprogram.agentic_programming.session._sessions_dir",
            lambda: str(tmp_path),
        )
        (tmp_path / "locations.json").write_text("{}", encoding="utf-8")
        session = Session("waiting")
        session.write_meta("Which option?")

        rows = list_sessions()

        assert len(rows) == 1
        assert rows[0]["question"] == "Which option?"
        assert rows[0]["session_id"] == "waiting"

    def test_run_with_session_no_follow_up(self, capsys):
        """Function without follow-up outputs result directly."""
        result = run_with_session(_no_ask)
        assert result == 42
        captured = capsys.readouterr()
        assert '"type": "result"' in captured.out

    def test_run_with_session_with_follow_up(self, capsys):
        """Function with follow-up outputs follow-up JSON, waits for answer file."""
        output_lines = []

        def _capture_and_resume():
            """Wait for follow-up output, then send answer via session file."""
            # Wait for the follow-up line to appear
            time.sleep(0.5)
            captured = capsys.readouterr()
            output_lines.append(captured.out)
            import json
            for line in captured.out.strip().split("\n"):
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "follow_up":
                        session = Session(msg["session"])
                        session.send_answer("TestUser")
                        return
                except Exception:
                    pass

        t = threading.Thread(target=_capture_and_resume)
        t.start()

        result = run_with_session(_simple_ask)
        t.join(timeout=10)
        assert result == "Hello, TestUser!"


# ===========================================================================
# Scenario 3: Web UI handler (simulates _web_follow_up)
# ===========================================================================

class TestWebFollowUp:
    """Test the global handler pattern used by the web server."""

    def test_global_handler_intercepts_ask_user(self):
        """set_ask_user registers a handler that ask_user() calls."""
        answers = iter(["Alice"])
        set_ask_user(lambda q: next(answers))
        try:
            result = _simple_ask()
            assert result == "Hello, Alice!"
        finally:
            set_ask_user(None)

    def test_global_handler_multi_question(self):
        """Handler called multiple times for multiple ask_user() calls."""
        answers = iter(["red", "42"])
        set_ask_user(lambda q: next(answers))
        try:
            result = _multi_ask()
            assert result == "red-42"
        finally:
            set_ask_user(None)

    def test_queue_based_handler(self):
        """Simulates the web server's queue-based follow-up mechanism."""
        q_out = queue.Queue()  # questions go here
        q_in = queue.Queue()   # answers come from here

        def handler(question):
            q_out.put(question)
            return q_in.get(timeout=5)

        set_ask_user(handler)

        # Run function in background thread (like server does)
        result_holder = [None]
        def _run():
            result_holder[0] = _multi_ask()

        t = threading.Thread(target=_run)
        t.start()

        # Simulate user answering questions via "WebSocket"
        q1 = q_out.get(timeout=5)
        assert q1 == "Favorite color?"
        q_in.put("green")

        q2 = q_out.get(timeout=5)
        assert q2 == "Favorite number?"
        q_in.put("99")

        t.join(timeout=5)
        set_ask_user(None)

        assert result_holder[0] == "green-99"

    def test_no_handler_returns_none_in_noninteractive(self):
        """With no handler and non-TTY stdin, ask_user returns None."""
        set_ask_user(None)
        # In test environment, stdin may not be a TTY
        import sys
        if not (sys.stdin and sys.stdin.isatty()):
            result = ask_user("question")
            assert result is None


# ===========================================================================
# Integration: agentic_function + follow-up in all modes
# ===========================================================================

class TestAgenticFunctionFollowUp:
    """Test that @agentic_function works with follow-up in all modes."""

    def test_agentic_fn_global_handler(self):
        """@agentic_function + global handler."""
        set_ask_user(lambda q: "handled")
        try:
            result = _agentic_ask(task="test")
            assert result == "Resolved: handled"
        finally:
            set_ask_user(None)

    def test_agentic_fn_run_with_follow_up(self):
        """@agentic_function + run_with_follow_up."""
        result = run_with_follow_up(_agentic_ask, task="test")
        assert isinstance(result, FollowUp)
        final = result.answer("clarified")
        assert final == "Resolved: clarified"

    def test_nested_fn_follow_up_bubbles(self):
        """Child @agentic_function follow-up bubbles through parent."""
        set_ask_user(lambda q: "bubbled answer")
        try:
            result = _parent_calls_child(task="nested test")
            assert "bubbled answer" in result
        finally:
            set_ask_user(None)
