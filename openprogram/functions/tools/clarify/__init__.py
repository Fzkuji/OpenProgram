"""AskUserQuestion (ask_user_question) — self-registers via @function on
import. (Directory still named clarify for git-move stability; the tool
name is ask_user_question.)"""

from .clarify import DESCRIPTION, NAME, SPEC, ask_user_question, clarify

__all__ = ["NAME", "SPEC", "DESCRIPTION", "ask_user_question", "clarify"]
