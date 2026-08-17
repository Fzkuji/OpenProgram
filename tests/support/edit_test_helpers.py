"""
Shared helpers for edit() tests.

The edit() flow:
  round 0: check_task() → LLM calls ask_user via catalog → user answers
  round 1: check_task() → ready → generate_code() → verify_fix() → conclude_fix()

So a successful edit needs at least 5 LLM calls:
  1. check_task (round 0) — LLM calls ask_user
  2. check_task (round 1) — LLM replies directly (ready)
  3. generate_code — should return fixed code
  4. verify_fix — should return {"approved": true, ...}
  5. conclude_fix — returns summary string
"""

from openprogram.programs.functions.agentic.ask_user import set_ask_user


def make_edit_mock(edited_code, *, answer="Proceed with the edit.", check_prompts=None):
    """Create a mock_call that handles the full edit() flow.

    Args:
        edited_code: The Python code string to return from generate_code.
        answer: The answer to give when ask_user is called (round 0 follow-up).
        check_prompts: Optional list to append all received prompts to.

    Returns:
        (mock_call, cleanup) — call cleanup() after test to reset ask_user.
    """
    call_count = [0]
    prompts = check_prompts if check_prompts is not None else []

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        text = content[-1]["text"] if content else ""
        prompts.append(text)

        # Call 1: check_task (round 0) — LLM calls ask_user via catalog
        if call_count[0] == 1:
            return '{"call": "ask_user", "args": {"question": "Can you confirm what needs fixing?"}}'

        # Call 2: check_task (round 1) — ready (no function call)
        if call_count[0] == 2:
            return "Ready to proceed."

        # Call 3: generate_code — return the fixed code
        if call_count[0] == 3:
            return edited_code

        # Call 4: verify_fix — approve
        if call_count[0] == 4:
            return '{"approved": true, "reasoning": "Fix looks correct."}'

        # Call 5+: conclude_fix or anything else
        return "Fix completed successfully."

    set_ask_user(lambda question: answer)

    def cleanup():
        set_ask_user(None)

    return mock_call, cleanup


def make_simple_edit_mock(edited_code):
    """Even simpler: returns a mock_call and context manager.

    Usage:
        mock_call, cleanup = make_simple_edit_mock(code)
        try:
            result = edit(fn=..., runtime=Runtime(call=mock_call))
        finally:
            cleanup()
    """
    return make_edit_mock(edited_code)
