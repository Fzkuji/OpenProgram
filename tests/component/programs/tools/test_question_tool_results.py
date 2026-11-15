"""Question results at the registered entry without real user prompts."""

from __future__ import annotations

import asyncio
import builtins

import pytest

from openprogram.runtime.agent.questions import AskTimeout, UserDeclined
from openprogram.runtime.agent.call_state import _current_runtime
from openprogram.programs._host.runtime import get
from openprogram.programs.tools.interaction import clarify


QUESTIONS = [
    {
        "question": "Controlled question",
        "header": "Owned",
        "options": [
            {"label": "A", "description": "First choice"},
            {"label": "B"},
        ],
    }
]


def invoke(questions=QUESTIONS):
    assert get("ask_user_question") is clarify.ask_user_question
    return asyncio.run(
        get("ask_user_question").execute(
            "controlled-question-call",
            {"questions": questions},
            None,
            None,
        )
    )


def text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


@pytest.fixture(autouse=True)
def no_frontend():
    token = _current_runtime.set(None)
    try:
        yield
    finally:
        _current_runtime.reset(token)


def test_unavailable_frontend_is_typed_failure():
    result = invoke()
    assert "no interactive frontend" in text(result)
    assert result.is_error is True


@pytest.mark.parametrize("questions", [[], None, "invalid"])
def test_invalid_question_array_is_typed_failure(questions):
    result = invoke(questions)
    assert text(result) == "Error: `questions` must be a non-empty array."
    assert result.is_error is True


def test_unavailable_input_infrastructure_is_typed_failure(monkeypatch):
    real_import = builtins.__import__

    def controlled_import(name, *args, **kwargs):
        if name == "openprogram.runtime.agent.questions":
            raise ImportError("controlled missing input infrastructure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", controlled_import)
    result = invoke()
    assert "user-input infrastructure not available" in text(result)
    assert result.is_error is True


def test_valid_answer_and_question_manifest_remain_compatible():
    seen = []

    class Frontend:
        def can_ask(self):
            return True

        def ask(self, *, questions):
            seen.append(questions)
            return [["Error: literal custom answer"]]

    token = _current_runtime.set(Frontend())
    try:
        result = invoke()
    finally:
        _current_runtime.reset(token)
    assert text(result) == "Owned: Error: literal custom answer"
    assert result.is_error is False
    assert seen == [clarify.interaction_manifest({"questions": QUESTIONS})["questions"]]
    assert seen[0][0]["allow_custom"] is True
    assert seen[0][0]["options"] == ["A", "B"]
    assert seen[0][0]["question_title"] == "Controlled question"
    assert seen[0][0]["option_descriptions"] == {"A": "First choice"}
    assert "A: First choice" in seen[0][0]["prompt"]  # Plain clients keep their hint.


@pytest.mark.parametrize(
    "exception,expected",
    [
        (UserDeclined, "The user declined to answer."),
        (AskTimeout, "The user did not answer in time."),
    ],
)
def test_existing_control_status_text_is_preserved(exception, expected):
    class Frontend:
        def can_ask(self):
            return True

        def ask(self, **kwargs):
            raise exception()

    token = _current_runtime.set(Frontend())
    try:
        result = invoke()
    finally:
        _current_runtime.reset(token)
    assert text(result) == expected
    assert result.is_error is False


def test_words_written_instead_of_an_answer_reach_the_model():
    class Frontend:
        def can_ask(self):
            return True

        def ask(self, **kwargs):
            raise UserDeclined("ask", reason="Why do you need this?")

    token = _current_runtime.set(Frontend())
    try:
        result = invoke()
    finally:
        _current_runtime.reset(token)
    assert "Why do you need this?" in text(result)
    assert "Respond to this" in text(result)
    assert result.is_error is False


def test_cancellation_is_not_converted_to_answer_or_error():
    class Frontend:
        def can_ask(self):
            return True

        def ask(self, **kwargs):
            raise asyncio.CancelledError()

    token = _current_runtime.set(Frontend())
    try:
        with pytest.raises(asyncio.CancelledError):
            invoke()
    finally:
        _current_runtime.reset(token)
