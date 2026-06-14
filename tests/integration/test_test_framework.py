import json

from openprogram.functions.agentics.test_framework import test_framework


def test_framework_flags_vague_instruction_even_with_code_context():
    task = (
        "Function: test_framework\n"
        "File: /tmp/test_framework.py\n\n"
        "Current code:\n"
        "```python\n"
        "print('hi')\n"
        "```\n\n"
        "Instruction:\n"
        "你看看"
    )

    result = json.loads(test_framework(task))

    assert result["ready"] is False
    assert result["question"]


def test_framework_allows_clear_instruction_with_code_context():
    task = (
        "Function: test_framework\n"
        "File: /tmp/test_framework.py\n\n"
        "Current code:\n"
        "```python\n"
        "print('hi')\n"
        "```\n\n"
        "Instruction:\n"
        "Fix the crash when the input is empty."
    )

    result = json.loads(test_framework(task))

    assert result == {"ready": True}
