"""
deep_work — autonomous agent for complex, high-quality tasks.

For tasks that demand sustained effort and high standards (writing papers,
building features, investigating complex bugs), this function runs a
plan-execute-evaluate loop until the result meets the specified quality.

Flow:
  1. Clarify: analyze the task, ask the user any questions upfront
  2. Work: plan and execute steps autonomously (no more user interaction)
  3. Evaluate: the agent independently reviews its own output
  4. Revise: if evaluation fails, feed back and continue
  5. Repeat 2-4 until evaluation passes

Quality levels (from low to high):
  - high_school: basic correctness, simple structure
  - bachelor: solid understanding, proper methodology
  - master: depth of analysis, critical thinking, good writing
  - phd: novel contribution, rigorous methodology, publication-ready
  - professor: expert-level, authoritative, top-venue quality

Usage:
    from openprogram import create_runtime
    from openprogram.programs.functions.buildin.deep_work import deep_work

    runtime = create_runtime()

    result = deep_work(
        task="Write a survey paper on context management in LLM agents. "
             "Focus on compaction vs sub-agent trade-offs. Target NeurIPS workshop.",
        level="phd",
        runtime=runtime,
    )
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json
from openprogram.programs.functions.buildin.wait import wait


# ---------------------------------------------------------------------------
# Quality levels
# ---------------------------------------------------------------------------

LEVELS = {
    "high_school": (
        "High school level. "
        "Basic correctness, clear structure, demonstrates understanding of core concepts. "
        "Minor errors acceptable. Simple language, straightforward reasoning."
    ),
    "bachelor": (
        "Undergraduate level. "
        "Solid understanding, proper methodology, well-organized. "
        "Shows ability to synthesize information from multiple sources. "
        "Correct terminology, logical flow, adequate references."
    ),
    "master": (
        "Master's level. "
        "Depth of analysis, critical thinking, good academic writing. "
        "Demonstrates ability to evaluate and compare approaches. "
        "Clear methodology, thorough literature review, well-supported conclusions."
    ),
    "phd": (
        "PhD researcher level. "
        "Novel contribution or insightful synthesis, rigorous methodology, "
        "publication-ready quality. Strong theoretical grounding, "
        "comprehensive related work, clear positioning of contribution. "
        "Addresses potential counterarguments and limitations."
    ),
    "professor": (
        "Professor / expert level. "
        "Authoritative, top-venue quality (NeurIPS/ICML/Nature/OSDI). "
        "Defines or redefines the field. Impeccable methodology, "
        "compelling narrative, significant impact. "
        "Could serve as a reference work in the area."
    ),
}


# ---------------------------------------------------------------------------
# Inner functions
# ---------------------------------------------------------------------------

@agentic_function(render_range={"depth": 0, "siblings": 0})
def _clarify(task: str, level: str, runtime: Runtime) -> dict:
    """Clarify a complex, autonomous task before execution.

    Analyze the task and identify anything that is ambiguous,
    underspecified, or needs confirmation. This is the ONLY chance to
    ask the user — after this the task runs fully autonomously.

    Think about:
    - Is the scope clear? What exactly should be delivered?
    - Are there format/structure requirements not mentioned?
    - Are there constraints (language, tools, frameworks)?
    - Is the quality level appropriate for the task?
    - Any assumptions that should be confirmed?

    Return JSON:
    {
      "clear": true/false,
      "questions": ["list of questions for the user, if any"],
      "plan_summary": "brief outline of how you intend to approach this",
      "estimated_steps": 10
    }

    Set clear=true if you have enough information to proceed without asking.
    Keep questions concise and essential — don't ask about things you can
    reasonably decide yourself.
    """
    reply = runtime.exec(content=[
        {"type": "text", "text": (
            f"Task: {task}\n"
            f"Quality level: {level}\n\n"
            "Analyze this task. Is anything unclear? "
            "Return JSON with clear/questions/plan_summary/estimated_steps."
        )},
    ])

    try:
        return parse_json(reply)
    except ValueError:
        return {
            "clear": True,
            "questions": [],
            "plan_summary": reply[:300],
            "estimated_steps": 20,
        }


@agentic_function(render_range={"siblings": -1})
def _step(task: str, standard: str, step_number: int,
          feedback: Optional[str], runtime: Runtime) -> dict:
    """Autonomously advance one step toward a complex, high-standard task.

    Based on the task, quality standard, the execution history (visible
    in context), and any evaluation feedback, decide what to do next and
    do it.

    You have full freedom to:
    - Run shell commands
    - Read and write files
    - Browse the web
    - Install packages
    - Use any tools available
    - Break the task into sub-tasks
    - Research, draft, revise, refactor

    Return JSON:
    {
      "action": "what you did this step",
      "result": "outcome of the action",
      "next": "what should be done next",
      "ready_for_review": true/false,
      "error": null or "error description"
    }

    Set ready_for_review=true when you believe the work meets the quality
    standard and is ready for independent evaluation. Do NOT set this
    prematurely — only when you have genuinely completed the work to the
    required standard.
    """
    parts = [
        f"Task: {task}",
        f"Quality standard: {standard}",
        f"Step #{step_number}",
    ]
    if feedback:
        parts.append(f"Evaluation feedback from last review (address these issues):\n{feedback}")
    parts.append(
        "Decide what to do next, do it, then return JSON with "
        "action/result/next/ready_for_review/error."
    )

    reply = runtime.exec(content=[
        {"type": "text", "text": "\n\n".join(parts)},
    ])

    try:
        return parse_json(reply)
    except ValueError:
        return {
            "action": "executed",
            "result": reply[:500],
            "next": "continue",
            "ready_for_review": False,
            "error": None,
        }


@agentic_function(render_range={"depth": 0, "siblings": 0})
def _evaluate(task: str, standard: str, work_summary: str,
              runtime: Runtime) -> dict:
    """Evaluate work quality. Return JSON with passed/score/verdict."""
    reply = runtime.exec(content=[
        {"type": "text", "text": (
            "Evaluate the work below against the quality standard. "
            "Be thorough, critical, and strict.\n\n"
            "Evaluate on: completeness, quality, correctness, polish.\n\n"
            f"Task: {task}\n"
            f"Quality standard: {standard}\n\n"
            f"Work to evaluate:\n{work_summary}\n\n"
            "Return ONLY a JSON object:\n"
            '{"passed": true/false, "score": 1-10, '
            '"strengths": [...], "weaknesses": [...], '
            '"feedback": "actionable feedback", '
            '"verdict": "one-line summary"}\n\n'
            "passed=true only if score >= 8."
        )},
    ])

    try:
        return parse_json(reply)
    except ValueError:
        return {
            "passed": False,
            "score": 0,
            "strengths": [],
            "weaknesses": ["Could not parse evaluation"],
            "feedback": reply[:500],
            "verdict": "Evaluation failed to parse",
        }


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_path(state_dir: str, task: str) -> str:
    import hashlib
    name = hashlib.sha256(task.encode()).hexdigest()[:12]
    return os.path.join(state_dir, f"deep_work_{name}.json")


def _load_state(path: str) -> Optional[dict]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_state(path: str, state: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def deep_work(
    task: str,
    level: str = "bachelor",
    runtime: Runtime = None,
    max_steps: Optional[int] = 100,
    max_revisions: int = 5,
    state_dir: Optional[str] = None,
    callback: Optional[callable] = None,
    interactive: bool = True,
) -> dict:
    """Run an autonomous agent loop with quality evaluation.

    Before starting, the agent analyzes the task and asks clarifying
    questions (if interactive=True). After that, it works fully
    autonomously — no more user interaction needed.

    Args:
        task:           The task to accomplish. Include all details,
                        requirements, and context in this single field.
        level:          Quality level: "high_school", "bachelor", "master",
                        "phd", "professor". Determines evaluation strictness.
        runtime:        The LLM runtime to use.
        max_steps:      Max total steps (None = unlimited).
        max_revisions:  Max evaluation-revision cycles (default: 5).
        state_dir:      Directory for state persistence (default: ~/.agentic/logs/).
        callback:       Called after each step/evaluation. Return False to stop.
        interactive:    If True, ask clarifying questions at start (default: True).
                        Set False for fully non-interactive execution.

    Returns:
        dict with: done, steps, revisions, evaluations, history, error
    """
    if runtime is None:
        raise ValueError("runtime is required for deep_work()")

    # Build quality standard from level
    standard = LEVELS.get(level, LEVELS["bachelor"])

    # State
    if state_dir is None:
        state_dir = os.path.join(os.path.expanduser("~"), ".agentic", "logs")
    sp = _state_path(state_dir, task)
    state = _load_state(sp) or {
        "task": task,
        "level": level,
        "steps": 0,
        "revisions": 0,
        "evaluations": [],
        "history": [],
        "done": False,
        "feedback": None,
        "clarified": False,
    }

    # --- Clarification phase (only once, only if not already done) ---
    if not state.get("clarified") and interactive:
        clarify_result = _clarify(
            task=task, level=level, runtime=runtime,
        )
        state["plan_summary"] = clarify_result.get("plan_summary", "")

        if not clarify_result.get("clear") and clarify_result.get("questions"):
            from openprogram.programs.functions.buildin.ask_user import ask_user
            questions = clarify_result["questions"]
            answers = []
            for q in questions:
                ans = ask_user(q)
                if ans and ans.strip():
                    answers.append(f"Q: {q}\nA: {ans}")
                else:
                    break

            if answers:
                clarification = "\n".join(answers)
                # Append clarifications to task for future context
                task += f"\n\nUser clarifications:\n{clarification}"
                state["task"] = task
                state["clarifications"] = answers

        state["clarified"] = True
        _save_state(sp, state)

        if callback is not None:
            callback({"type": "clarify", **clarify_result})

    step_num = state["steps"]
    revisions = state["revisions"]
    feedback = state.get("feedback")

    while not state["done"]:
        # Safety limits
        if max_steps is not None and step_num >= max_steps:
            state["error"] = f"max_steps ({max_steps}) reached"
            break
        if revisions >= max_revisions:
            state["error"] = f"max_revisions ({max_revisions}) reached"
            break

        # --- Work phase ---
        step_num += 1
        try:
            result = _step(
                task=task,
                standard=standard,
                step_number=step_num,
                feedback=feedback,
                runtime=runtime,
            )
        except Exception as e:
            result = {
                "action": "error",
                "result": None,
                "next": "retry or adjust",
                "ready_for_review": False,
                "error": f"{type(e).__name__}: {e}",
            }

        state["steps"] = step_num
        state["history"].append({
            "step": step_num,
            "timestamp": time.time(),
            "type": "work",
            **result,
        })
        _save_state(sp, state)

        if callback is not None:
            if callback({"type": "step", **result}) is False:
                state["done"] = True
                state["cancelled"] = True
                _save_state(sp, state)
                break

        # --- Wait phase ---
        if not result.get("ready_for_review"):
            action_desc = result.get("action", "completed a step")
            wait(action=action_desc, runtime=runtime)
            continue

        # --- Evaluation phase ---
        # Fresh runtime so the evaluator is not influenced by the
        # execution agent's accumulated context.
        from openprogram.legacy_providers import create_runtime as _create_rt

        work_summary = _build_work_summary(state["history"])

        try:
            with _create_rt(model=runtime.model) as eval_runtime:
                eval_result = _evaluate(
                    task=task,
                    standard=standard,
                    work_summary=work_summary,
                    runtime=eval_runtime,
                )
        except Exception as e:
            eval_result = {
                "passed": False,
                "score": 0,
                "strengths": [],
                "weaknesses": [str(e)],
                "feedback": f"Evaluation failed: {e}",
                "verdict": "error",
            }

        revisions += 1
        state["revisions"] = revisions
        state["evaluations"].append({
            "revision": revisions,
            "timestamp": time.time(),
            **eval_result,
        })
        _save_state(sp, state)

        if callback is not None:
            if callback({"type": "evaluation", **eval_result}) is False:
                state["done"] = True
                state["cancelled"] = True
                _save_state(sp, state)
                break

        if eval_result.get("passed"):
            state["done"] = True
            _save_state(sp, state)
        else:
            feedback = eval_result.get("feedback", "Improve the work.")
            state["feedback"] = feedback
            _save_state(sp, state)

    return state


def _build_work_summary(history: list, max_entries: int = 20) -> str:
    """Build a summary of recent work from history entries.

    Includes step descriptions and attempts to read any files that
    were created/modified, so the evaluator has the actual content.
    """
    work_entries = [h for h in history if h.get("type") == "work"]
    recent = work_entries[-max_entries:]
    lines = []
    files_seen = set()

    for h in recent:
        action = h.get("action", "?")
        result = h.get("result", "?")
        lines.append(f"Step {h.get('step', '?')}: {action} → {result}")

        # Extract file paths from action/result text
        for text in [str(action), str(result)]:
            for word in text.split():
                word = word.strip("',\"()[]{}:")
                if "/" in word and "." in word.split("/")[-1]:
                    files_seen.add(word)

    # Append file contents for evaluation (truncated to keep prompt reasonable)
    total_file_chars = 0
    max_total = 3000
    for fpath in sorted(files_seen):
        if total_file_chars >= max_total:
            lines.append(f"\n--- File: {fpath} (skipped, budget exceeded) ---")
            continue
        try:
            with open(fpath, "r") as f:
                content = f.read()
            remaining = max_total - total_file_chars
            if len(content) > remaining:
                content = content[:remaining] + "\n... (truncated)"
            total_file_chars += len(content)
            lines.append(f"\n--- File: {fpath} ---\n{content}")
        except (FileNotFoundError, IOError, IsADirectoryError):
            pass

    return "\n".join(lines) if lines else "No work completed yet."
