"""
fix() — Analyze and rewrite an existing function based on errors and instructions.

Loop pattern:
    for each round:
        _fix_round(task, feedback) → generates, validates, compiles, verifies
        Each round is a parent node in the execution tree with child steps.

    conclude_fix() → natural language summary
    return compiled callable
"""

from __future__ import annotations

import re
from typing import Optional

from agentic.function import agentic_function
from agentic.runtime import Runtime
from agentic.meta_functions._helpers import (
    extract_code, validate_code, compile_function,
    save_function, get_source, get_error_log,
    _canonicalize_function_code,
    clarify, generate_code,
)


_INLINE_FOLLOW_UP_RE = re.compile(
    r"^(?P<instruction>.*?)(?:\s*\[(?P<follow_up>Q:.*)\]\s*)$",
    re.DOTALL,
)
_FOLLOW_UP_QA_RE = re.compile(
    r"Q:\s*(?P<question>.*?)\s+A:\s*(?P<answer>.*)$",
    re.DOTALL,
)

_FIX_GENERATION_SUFFIX = (
    "\n\nFix the root cause, not just the symptom. "
    "Respond with ONLY the fixed Python code in a ```python fence."
)


def _split_follow_up_instruction(instruction: str | None) -> tuple[str, Optional[str]]:
    """Split a trailing inline follow-up block off an instruction string.

    The visualizer currently serializes follow-up answers as:
        "<instruction> [Q: ... A: ...]"

    That inline form is easy to misread as part of the main instruction, so
    we normalize it into a separate prompt section before calling the LLM.
    """
    if not instruction:
        return "", None

    text = instruction.strip()
    match = _INLINE_FOLLOW_UP_RE.match(text)
    if not match:
        return text, None

    main_instruction = match.group("instruction").strip()
    follow_up = match.group("follow_up").strip()
    if not main_instruction or "Q:" not in follow_up or "A:" not in follow_up:
        return text, None
    return main_instruction, follow_up


def _format_follow_up_context(follow_up: str) -> str:
    """Render follow-up context in a clearer, multi-line form."""
    follow_up = follow_up.strip()
    if not follow_up:
        return ""

    match = _FOLLOW_UP_QA_RE.search(follow_up)
    if not match:
        return follow_up

    question = match.group("question").strip()
    answer = match.group("answer").strip()
    if question and answer:
        return f"Q: {question}\nA: {answer}"
    return follow_up


# ---------------------------------------------------------------------------
# Inner functions — each creates a node in the execution tree
# ---------------------------------------------------------------------------

@agentic_function(compress=True, summarize={"siblings": -1})
def _fix_round(
    task: str,
    original_code: str,
    error_log: str,
    instruction: str,
    round_num: int,
    fn_name: str,
    runtime: Runtime,
) -> dict:
    """Execute one round of fix: clarify → generate code → validate → compile → verify.

    Returns a dict describing the outcome:
      {"status": "approved", "code": "...", "compiled": <callable>}
      {"status": "rejected", "feedback": "reason"}
      {"status": "error", "feedback": "error message"}
      {"status": "follow_up", "question": "..."}
      {"status": "exit", "reason": "why the task should stop"}

    Args:
        task: The full task string (base context + previous feedback).
        original_code: The original source code being fixed.
        error_log: Error log from the original function.
        instruction: User's fix instruction.
        round_num: Current round number (for display).
        runtime: LLM runtime instance.

    Returns:
        Dict with status and round-specific data.
    """
    # Step 1: Clarify — do we have enough info?
    # On the first round (round_num == 0), always ask a follow-up question
    # so the user can confirm/clarify what they want fixed before we generate code.
    # Exit is only allowed on round 1+ (after user has had a chance to respond).
    check = clarify(task=task, runtime=runtime)
    if round_num == 0:
        question = check.get("question") or check.get("reason") or "Need more information."
        return {"status": "follow_up", "question": question}
    if check.get("exit"):
        return {"status": "exit", "reason": check.get("reason", "Task cannot proceed.")}
    if not check.get("ready", True):
        question = check.get("question") or "Need more information."
        return {"status": "follow_up", "question": question}

    # Step 2: Generate fix attempt
    response = generate_code(task=f"{task}{_FIX_GENERATION_SUFFIX}", runtime=runtime)

    # Step 3: Extract, validate, compile
    try:
        fixed_code = extract_code(response)
        fixed_code = _canonicalize_function_code(fixed_code, fn_name)
        validate_code(fixed_code, response)
        compiled_fn = compile_function(fixed_code, runtime, fn_name)
    except (SyntaxError, ValueError, RuntimeError) as e:
        return {"status": "error", "feedback": f"Code failed: {e}"}

    # Step 4: Verify
    verify_result = verify_fix(
        original_code=original_code,
        fixed_code=fixed_code,
        error_log=error_log,
        instruction=instruction,
        runtime=runtime,
    )

    if verify_result.get("approved", False):
        return {"status": "approved", "code": fixed_code, "compiled": compiled_fn}
    else:
        reason = verify_result.get("reasoning", "Fix was rejected.")
        return {"status": "rejected", "feedback": reason}


@agentic_function(summarize={"depth": 0, "siblings": 0})
def verify_fix(
    original_code: str,
    fixed_code: str,
    error_log: str,
    instruction: str,
    runtime: Runtime,
) -> dict:
    """Review a proposed code fix and decide if it correctly addresses the problem.

    Compare the original code with the fixed version. Check:
    1. Does the fix address the root cause (not just the symptom)?
    2. Is the fix correct and complete?
    3. Does it introduce any new issues?

    Return JSON:
    {
      "approved": true/false,
      "reasoning": "why approved or what's still wrong"
    }

    Args:
        original_code: The original broken code.
        fixed_code: The proposed fix.
        error_log: Error messages from the original code.
        instruction: What the user asked to fix.
        runtime: LLM runtime instance.

    Returns:
        Dict with approved (bool) and reasoning (str).
    """
    context = (
        f"Original code:\n```python\n{original_code}\n```\n\n"
        f"Fixed code:\n```python\n{fixed_code}\n```"
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
    # Fallback: check for rejection keywords; approve by default
    lower = reply.lower()
    rejected = any(w in lower for w in ["reject", "wrong", "incorrect", "not fix", "doesn't fix", "fail"])
    return {"approved": not rejected, "reasoning": reply[:300]}


@agentic_function(summarize={"depth": 0, "siblings": 0})
def conclude_fix(task: str, runtime: Runtime) -> str:
    """Summarize what was fixed based on all the steps taken.

    Look at the execution history (visible as siblings in the context tree)
    and produce a concise natural language summary of:
    - What was wrong
    - What was changed
    - Whether the fix was successful

    Args:
        task: The original fix task description.
        runtime: LLM runtime instance.

    Returns:
        Natural language summary of the fix.
    """
    return runtime.exec(content=[
        {"type": "text", "text": task},
    ])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@agentic_function(input={
    "fn": {
        "description": "Function to fix",
        "options_from": "functions",
        "multiline": False,
    },
    "runtime": {"hidden": True},
    "instruction": {
        "description": "What to fix or change",
        "placeholder": "e.g. handle empty input gracefully",
        "multiline": True,
    },
    "name": {
        "description": "Rename the fixed function",
        "placeholder": "e.g. sentiment_v2",
        "multiline": False,
    },
    "max_rounds": {
        "description": "Max retry rounds",
        "options": ["3", "5", "10"],
    },
})
def fix(
    fn,
    runtime: Runtime,
    instruction: str = None,
    name: str = None,
    max_rounds: int = 5,
):
    """Fix a broken function based on its code, errors, and optional instruction.

    Runs _fix_round() in a loop. Each round is a distinct node in the
    execution tree with its own children (generate, validate, verify).

    Args:
        fn: The function to fix.
        runtime: Runtime instance for LLM calls.
        instruction: Optional manual instruction ("change X to Y").
        name: Optional name override.
        max_rounds: Maximum rounds (default 5).

    Returns:
        callable — the fixed function.
        If a follow-up question arises and ask_user handler is registered
        (e.g. in the visualizer), the loop blocks until the user answers.
        If no handler, returns {"type": "follow_up", "question": "..."}.
    """
    import inspect as _inspect

    description = getattr(fn, '__doc__', '') or getattr(fn, '__name__', 'unknown')
    code = get_source(fn)
    error_log = get_error_log(fn)
    fn_name = name or getattr(fn, '__name__', 'fixed')
    instruction_text, follow_up_context = _split_follow_up_instruction(instruction)

    # Resolve file path for context
    try:
        _inner = getattr(fn, '__wrapped__', fn)
        fn_filepath = _inspect.getfile(_inner)
    except (TypeError, OSError):
        fn_filepath = getattr(fn, '__file__', None)

    # Base task — fixed context that doesn't change between rounds.
    # The per-round generation suffix is appended only when calling generate_code().
    header = f"Function: {fn_name}"
    if fn_filepath:
        header += f"\nFile: {fn_filepath}"
    base_parts = [f"{header}\n\nCurrent code:\n```python\n{code}\n```"]
    if error_log:
        base_parts.append(f"Error log:\n{error_log}")
    if instruction_text:
        base_parts.append(f"Instruction:\n{instruction_text}")
    if follow_up_context:
        base_parts.append(
            "Follow-up context:\n"
            f"{_format_follow_up_context(follow_up_context)}\n"
            "Treat this as prior clarification context, not as a new instruction."
        )
    base_task = "\n\n".join(base_parts)

    compiled_fn = None
    feedback = None

    for round_num in range(max_rounds):
        # Build task: base context + last round's feedback.
        task = base_task
        if feedback:
            task += f"\n\n── Previous attempt feedback ──\n{feedback}"

        # Run one round (creates a parent node in execution tree)
        round_result = _fix_round(
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
            # LLM decided this task should stop (impossible, mismatched, etc.)
            reason = round_result.get("reason", "Task cannot proceed.")
            conclude_task = (
                f"Fix task for '{fn_name}' was stopped by the model.\n"
                f"Reason: {reason}\n"
                f"Instruction: {instruction_text or description}\n"
                "Summarize why the task was stopped."
            )
            return conclude_fix(task=conclude_task, runtime=runtime)

        if status == "follow_up":
            from agentic.context import ask_user
            answer = ask_user(round_result["question"])
            if answer is not None and answer.strip():
                # Got a real answer from user — continue loop
                feedback = f"Q: {round_result['question']}\nA: {answer}"
                continue
            # No handler or no answer — stop loop, return follow_up
            return {"type": "follow_up", "question": round_result["question"]}

        if status == "approved":
            compiled_fn = round_result["compiled"]
            fixed_code = round_result["code"]
            # Re-assign name if needed
            if fn_name and compiled_fn:
                compiled_fn.__name__ = fn_name
                compiled_fn.__qualname__ = fn_name
            save_function(
                fixed_code,
                fn_name,
                f"Fixed: {description}",
                source_path=fn_filepath,
                action="fix",
            )
            break

        # "error" or "rejected" — use feedback for next round
        feedback = round_result.get("feedback", "Unknown issue.")
        compiled_fn = None

    # Conclude — summary recorded in context tree
    if compiled_fn is not None:
        conclude_task = f"Fix task for '{fn_name}': {instruction_text or description}"
    else:
        conclude_task = (
            f"Fix task for '{fn_name}' failed after {max_rounds} rounds.\n"
            f"Instruction: {instruction_text or description}\n"
            f"Last feedback: {feedback or 'N/A'}\n"
            "Summarize what was attempted and why it failed."
        )
    summary = conclude_fix(task=conclude_task, runtime=runtime)

    return summary
