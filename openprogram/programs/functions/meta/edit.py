"""
edit() — Analyze and rewrite an existing function based on errors and instructions.

Loop pattern:
    for each round:
        _edit_round(task, feedback) → generates, validates, compiles, verifies
        Each round is a parent node in the execution tree with child steps.

    conclude_edit() → natural language summary
    return compiled callable
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.meta.generation.clarify import clarify
from openprogram.programs.functions.meta.validation.compile_function import compile_function
from openprogram.programs.functions.meta.generation.extract_code import extract_code
from openprogram.programs.functions.meta.generation.generate_code import generate_code
from openprogram.programs.functions.meta.introspection.get_source import get_error_log, get_source
from openprogram.programs.functions.meta.storage.save_function import save_function
from openprogram.programs.functions.meta.validation.validate_code import validate_code


_EDIT_GENERATION_SUFFIX = (
    "\n\nFix the root cause, not just the symptom. "
    "Respond with ONLY the edited Python code in a ```python fence."
)


# ---------------------------------------------------------------------------
# Inner functions — each creates a node in the execution tree
# ---------------------------------------------------------------------------


@agentic_function(render_range={"siblings": -1})
def _edit_round(
    task: str,
    original_code: str,
    error_log: str,
    instruction: str,
    round_num: int,
    fn_name: str,
    runtime: Runtime,
) -> dict:
    """Execute one round of edit: clarify → generate code → validate → compile → verify.

    Returns a dict describing the outcome:
      {"status": "approved", "code": "...", "compiled": <callable>}
      {"status": "rejected", "feedback": "reason"}
      {"status": "error", "feedback": "error message"}
      {"status": "follow_up", "question": "..."}
      {"status": "exit", "reason": "why the task should stop"}

    Args:
        task: The full task string (base context + previous feedback).
        original_code: The original source code being edited.
        error_log: Error log from the original function.
        instruction: User's edit instruction.
        round_num: Current round number (for display).
        runtime: LLM runtime instance.

    Returns:
        Dict with status and round-specific data.
    """
    check = clarify(task=task, runtime=runtime)
    if round_num == 0:
        question = check.get("question") or "Can you confirm what needs editing?"
        return {"status": "follow_up", "question": question}
    if check.get("exit"):
        return {"status": "exit", "reason": check.get("reason", "Task cannot proceed.")}
    if not check.get("ready", True):
        question = check.get("question") or "Need more information."
        return {"status": "follow_up", "question": question}

    response = generate_code(task=f"{task}{_EDIT_GENERATION_SUFFIX}", runtime=runtime)

    try:
        edited_code = extract_code(response)
        validate_code(edited_code, response)
        compiled_fn = compile_function(edited_code, runtime, fn_name)
    except (SyntaxError, ValueError, RuntimeError) as e:
        return {"status": "error", "feedback": f"Code failed: {e}"}

    verify_result = verify_edit(
        original_code=original_code,
        edited_code=edited_code,
        error_log=error_log,
        instruction=instruction,
        runtime=runtime,
    )

    if verify_result.get("approved", False):
        return {"status": "approved", "code": edited_code, "compiled": compiled_fn}
    else:
        reason = verify_result.get("reasoning", "Edit was rejected.")
        return {"status": "rejected", "feedback": reason}


@agentic_function(render_range={"depth": 0, "siblings": 0})
def verify_edit(
    original_code: str,
    edited_code: str,
    error_log: str,
    instruction: str,
    runtime: Runtime,
) -> dict:
    """Review a proposed code edit and decide if it correctly addresses the problem.

    Compare the original code with the edited version. Check:
    1. Does the edit address the root cause (not just the symptom)?
    2. Is the edit correct and complete?
    3. Does it introduce any new issues?

    Return JSON:
    {
      "approved": true/false,
      "reasoning": "why approved or what's still wrong"
    }

    Args:
        original_code: The original code.
        edited_code: The proposed edit.
        error_log: Error messages from the original code.
        instruction: What the user asked to change.
        runtime: LLM runtime instance.

    Returns:
        Dict with approved (bool) and reasoning (str).
    """
    context = (
        f"Original code:\n```python\n{original_code}\n```\n\n"
        f"Edited code:\n```python\n{edited_code}\n```"
    )
    if error_log:
        context += f"\n\nError log:\n{error_log}"
    if instruction:
        context += f"\n\nInstruction: {instruction}"

    reply = runtime.exec(content=[{"type": "text", "text": context}])

    try:
        import json
        import re
        match = re.search(r'\{[^{}]*\}', reply)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    lower = reply.lower()
    rejected = any(w in lower for w in ["reject", "wrong", "incorrect", "doesn't address", "fail"])
    return {"approved": not rejected, "reasoning": reply[:300]}


@agentic_function(render_range={"depth": 0, "siblings": 0})
def conclude_edit(task: str, runtime: Runtime) -> str:
    """Summarize what was edited based on all the steps taken.

    Look at the execution history (visible as siblings in the context tree)
    and produce a concise natural language summary of:
    - What was wrong
    - What was changed
    - Whether the edit was successful

    Args:
        task: The original edit task description.
        runtime: LLM runtime instance.

    Returns:
        Natural language summary of the edit.
    """
    return runtime.exec(content=[
        {"type": "text", "text": task},
    ])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@agentic_function(input={
    "fn": {
        "description": "Function to edit",
        "options_from": "functions",
        "multiline": False,
    },
    "runtime": {"hidden": True},
    "instruction": {
        "description": "What to change",
        "placeholder": "e.g. handle empty input gracefully",
        "multiline": True,
    },
    "name": {
        "description": "Rename the edited function",
        "placeholder": "e.g. sentiment_v2",
        "multiline": False,
    },
    "max_rounds": {
        "description": "Max retry rounds",
        "options": ["3", "5", "10"],
    },
})
def edit(
    fn,
    runtime: Runtime,
    instruction: str = None,
    name: str = None,
    max_rounds: int = 5,
):
    """Edit a function based on its code, errors, and optional instruction.

    Runs _edit_round() in a loop. Each round is a distinct node in the
    execution tree with its own children (generate, validate, verify).

    Args:
        fn: The function to edit.
        runtime: Runtime instance for LLM calls.
        instruction: Optional manual instruction ("change X to Y").
        name: Optional name override.
        max_rounds: Maximum rounds (default 5).

    Returns:
        callable — the edited function.
        If a follow-up question arises and ask_user handler is registered
        (e.g. in the visualizer), the loop blocks until the user answers.
        If no handler, returns {"type": "follow_up", "question": "..."}.
    """
    import inspect as _inspect

    description = getattr(fn, '__doc__', '') or getattr(fn, '__name__', 'unknown')
    code = get_source(fn)
    error_log = get_error_log(fn)
    fn_name = name or getattr(fn, '__name__', 'edited')
    instruction_text = (instruction or "").strip()

    try:
        _inner = getattr(fn, '__wrapped__', fn)
        fn_filepath = _inspect.getfile(_inner)
    except (TypeError, OSError):
        fn_filepath = getattr(fn, '__file__', None)

    header = f"Function: {fn_name}"
    if fn_filepath:
        header += f"\nFile: {fn_filepath}"
    base_parts = [f"{header}\n\nCurrent code:\n```python\n{code}\n```"]
    if error_log:
        base_parts.append(f"Error log:\n{error_log}")
    if instruction_text:
        base_parts.append(f"Instruction:\n{instruction_text}")
    base_task = "\n\n".join(base_parts)

    compiled_fn = None
    feedback = None

    for round_num in range(max_rounds):
        task = base_task
        if feedback:
            task += f"\n\n── Previous attempt feedback ──\n{feedback}"

        round_result = _edit_round(
            task=task,
            original_code=code,
            error_log=error_log or "",
            instruction=instruction_text or "",
            round_num=round_num,
            fn_name=fn_name,
            runtime=runtime,
        )

        status = round_result.get("status")

        if status == "exit":
            reason = round_result.get("reason", "Task cannot proceed.")
            conclude_task = (
                f"Edit task for '{fn_name}' was stopped by the model.\n"
                f"Reason: {reason}\n"
                f"Instruction: {instruction_text or description}\n"
                "Summarize why the task was stopped."
            )
            return conclude_edit(task=conclude_task, runtime=runtime)

        if status == "follow_up":
            from openprogram.programs.functions.buildin.ask_user import ask_user
            answer = ask_user(round_result["question"])
            if answer is not None and answer.strip():
                feedback = f"Q: {round_result['question']}\nA: {answer}"
                continue
            return round_result["question"]

        if status == "approved":
            compiled_fn = round_result["compiled"]
            edited_code = round_result["code"]
            if fn_name and compiled_fn:
                compiled_fn.__name__ = fn_name
                compiled_fn.__qualname__ = fn_name
            save_function(
                edited_code,
                fn_name,
                f"Edited: {description}",
                source_path=fn_filepath,
                action="edit",
            )
            break

        feedback = round_result.get("feedback", "Unknown issue.")
        compiled_fn = None

    if compiled_fn is not None:
        conclude_task = f"Edit task for '{fn_name}': {instruction_text or description}"
        conclude_edit(task=conclude_task, runtime=runtime)
        return compiled_fn
    else:
        conclude_task = (
            f"Edit task for '{fn_name}' failed after {max_rounds} rounds.\n"
            f"Instruction: {instruction_text or description}\n"
            f"Last feedback: {feedback or 'N/A'}\n"
            "Summarize what was attempted and why it failed."
        )
        summary = conclude_edit(task=conclude_task, runtime=runtime)
        return summary
