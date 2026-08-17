"""Test function for follow-up / resume functionality."""

from openprogram.agentic_programming.function import agentic_function
from openprogram.programs.functions.agentic.ask_user import ask_user


@agentic_function
def test_resume():
    """Test the follow-up resume mechanism.

    Asks a series of questions via ask_user() to verify that
    follow-up works in all calling contexts (web UI, CLI, agent).
    """
    name = ask_user("What is your name?")
    color = ask_user(f"Hi {name}, what is your favorite color?")
    confirm = ask_user(f"So your name is {name} and you like {color}. Correct? (yes/no)")

    if confirm and confirm.strip().lower() == "yes":
        return f"Confirmed: {name} likes {color}."
    else:
        correction = ask_user("What should I correct?")
        return f"Correction noted: {correction}"
