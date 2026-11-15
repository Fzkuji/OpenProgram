"""Words written instead of an answer travel with the decline."""
import pytest

from openprogram.runtime.agent.questions import UserDeclined
from openprogram.runtime.agent.runtime.questions import QuestionsOperations


class _Runtime(QuestionsOperations):
    def __init__(self, value):
        self.value = value

    def _ask_raw(self, **kwargs):
        return "declined", self.value


@pytest.mark.parametrize("questions", [None, [{"prompt": "Which?", "options": ["A"]}]])
@pytest.mark.parametrize("value,reason", [("  Why ask?  ", "Why ask?"), (None, "")])
def test_a_decline_carries_what_the_user_wrote(questions, value, reason):
    with pytest.raises(UserDeclined) as declined:
        _Runtime(value).ask("Which?", options=["A"], questions=questions)
    assert declined.value.reason == reason
