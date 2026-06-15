"""ask_user_question tool — self-registers via @function on import.
(Directory kept named ``clarify`` for git-history / import stability;
the registered tool name is ``ask_user_question``.)"""

from .clarify import DESCRIPTION, NAME, SPEC, ask_user_question

__all__ = ["NAME", "SPEC", "DESCRIPTION", "ask_user_question"]
