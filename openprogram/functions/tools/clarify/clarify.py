"""AskUserQuestion — the LLM's tool to ask the user question(s).

Mirrors Claude Code's ``AskUserQuestion``: one tool that asks 1–N
questions at once, each with a short header, 2–4 options (label +
description), single- or multi-select, plus free-text ("Other"). The
agent pauses until the user answers; the answers come back as a
structured dict the model reads.

It is a thin bridge over the unified ``runtime.ask`` user-input base
(``runtime.ask(questions=[...])``) — same ``question.asked`` event,
same composer popup card as ``runtime.ask`` from inside an
@agentic_function. The only difference is the entry point: here the
*model* drives it via tool-use; there *code* calls it directly.

Legacy: this used to be the single-question ``clarify`` tool. The
registry name is now ``ask_user_question``; ``clarify`` stays as an
alias function for any old caller.
"""

from __future__ import annotations

from typing import Any

from ..._runtime import function


NAME = "ask_user_question"

DESCRIPTION = (
    "Ask the user one or more questions and pause until they answer. "
    "Use when you need a decision or clarification you can't infer: "
    "disambiguation, picking between approaches, confirming a risky "
    "action. Each question shows 2–4 options (the user can also type a "
    "custom answer). Returns the user's choices. Returns an error if no "
    "interactive frontend is available (e.g. a non-interactive batch job)."
)


# Schema aligned with Claude Code's AskUserQuestion: a ``questions``
# array (1–4), each with question text, a short header label, 2–4
# options (label + description), and a multiSelect flag.
_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "description": "1–4 questions to ask at once.",
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The full question text shown to the user.",
                    },
                    "header": {
                        "type": "string",
                        "description": "Very short label/chip for this question (≤12 chars).",
                    },
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "2–4 choices. The user may also type a custom answer.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Choice text."},
                                "description": {"type": "string", "description": "What this choice means."},
                            },
                            "required": ["label"],
                        },
                    },
                    "multiSelect": {
                        "type": "boolean",
                        "description": "Allow selecting multiple options (default false).",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
    "required": ["questions"],
}


def _to_runtime_questions(questions: list[dict]) -> list[dict]:
    """Map Claude-Code-shaped questions → runtime.ask's question dicts.

    Each runtime question is ``{prompt, options, multi, allow_custom}``;
    options are the plain labels (descriptions are folded into the
    prompt so the model's intent reaches the user). allow_custom=True
    so the user can always type an "Other" answer.
    """
    out: list[dict] = []
    for q in questions or []:
        opts = q.get("options") or []
        labels = [str(o.get("label", "")) for o in opts if o.get("label")]
        # Fold option descriptions into the prompt as a hint line so the
        # user sees what each choice means (the popup renders only labels).
        desc_lines = [
            f"  • {o.get('label')}: {o.get('description')}"
            for o in opts
            if o.get("label") and o.get("description")
        ]
        prompt = str(q.get("question", ""))
        if desc_lines:
            prompt = prompt + "\n" + "\n".join(desc_lines)
        out.append({
            "prompt": prompt,
            "options": labels,
            "multi": bool(q.get("multiSelect")),
            "allow_custom": True,
        })
    return out


@function(
    name=NAME,
    description=DESCRIPTION,
    parameters=_PARAMETERS,
    toolset=["core"],
    max_result_chars=10_000,
)
def ask_user_question(questions: list | None = None, **kw: Any) -> str:
    if not questions or not isinstance(questions, list):
        return "Error: `questions` must be a non-empty array."

    # Bridge to the unified runtime.ask base. Needs an interactive
    # runtime in the current execution context (webui / channel / TTY).
    try:
        from openprogram.agentic_programming.function import _current_runtime
        from openprogram.agent.questions import UserDeclined, AskTimeout
    except ImportError as e:  # pragma: no cover
        return f"Error: user-input infrastructure not available: {e}"

    rt = _current_runtime.get(None)
    if rt is None or not rt.can_ask():
        return (
            "Error: no interactive frontend is available to ask the user "
            "(no WebUI / channel / TTY in this context)."
        )

    rt_questions = _to_runtime_questions(questions)
    try:
        answers = rt.ask(questions=rt_questions)
    except UserDeclined:
        return "The user declined to answer."
    except AskTimeout:
        return "The user did not answer in time."

    # Pair each answer back with its question header/text so the model
    # reads a clear {question → answer} mapping.
    lines: list[str] = []
    for q, ans in zip(questions, answers or []):
        key = q.get("header") or q.get("question") or "answer"
        val = ", ".join(ans) if isinstance(ans, list) else str(ans)
        lines.append(f"{key}: {val}")
    return "\n".join(lines) if lines else "(no answer)"


# Backward-compat alias: old single-question ``clarify`` callers.
def clarify(question: str | None = None, **kw: Any) -> str:
    """Legacy single-question entry. Forwards to ask_user_question."""
    q = question or kw.get("question") or kw.get("prompt") or kw.get("text")
    if not q:
        return "Error: `question` is required."
    return ask_user_question(questions=[{
        "question": str(q),
        "options": [{"label": "OK"}],
    }])


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": _PARAMETERS,
}


__all__ = ["NAME", "SPEC", "DESCRIPTION", "ask_user_question", "clarify"]
