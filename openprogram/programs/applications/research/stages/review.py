"""
review — cross-model review and iterative improvement.

Executor and reviewer use different LLM runtimes to avoid self-play
blind spots. The loop: review → fix → re-review → until pass.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json


@agentic_function(render_range={"depth": 0, "siblings": 0})
def review_paper(paper_content: str, venue: str, runtime: Runtime) -> str:
    """Review a paper at the level expected by top CS conferences.

    Evaluate the paper objectively: identify weaknesses AND acknowledge
    strengths. Be rigorous and precise.

    Review dimensions:
    - Community contribution: does this advance the field substantively?
    - Rigor: are claims supported by experiments? Fair baselines? Ablations?
    - Consistency: do intro claims match experimental validation?

    Distinguish fatal flaws from fixable issues — they carry different weight.
    Be specific: not "experiments insufficient" but "missing comparison with
    [specific method] on [specific dataset]".

    Score faithfully: if the paper is solid, give it a high score.
    Skip pleasantries, cut to core judgments.

    After your review, append a JSON block:
    ```json
    {"score": <1-10>, "passed": <true if score>=7>,
     "weaknesses": ["specific issues"],
     "strengths": ["specific strengths"],
     "verdict": "one-line summary"}
    ```
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Target venue: {venue}\n\n"
            f"Paper:\n{paper_content}"
        )},
    ])


@agentic_function(render_range={"siblings": -1})
def fix_paper(paper_content: str, review_feedback: str,
              round_num: int, runtime: Runtime) -> str:
    """Fix the paper based on reviewer feedback.

    Address EVERY weakness mentioned. Do NOT weaken existing strengths.
    Rewrite actual paragraphs — don't just describe what should change.
    Maintain LaTeX formatting.

    Output the COMPLETE fixed paper content.
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Round {round_num}\n\n"
            f"Reviewer feedback:\n{review_feedback}\n\n"
            f"Current paper:\n{paper_content}"
        )},
    ])


def _read_paper(paper_dir: str) -> str:
    """Read all .tex files from paper directory."""
    paper_dir = os.path.expanduser(paper_dir)
    parts = []
    for fname in sorted(os.listdir(paper_dir)):
        if fname.endswith(".tex"):
            with open(os.path.join(paper_dir, fname), "r") as f:
                parts.append(f"% === {fname} ===\n{f.read()}")
    return "\n\n".join(parts)


def _save_review_log(log_path: str, rounds: list):
    """Save review history."""
    lines = ["# Auto Review Log\n"]
    for r in rounds:
        lines.append(f"## Round {r['round']}")
        lines.append(f"- Score: {r.get('score', '?')}/10")
        lines.append(f"- Verdict: {r.get('verdict', '?')}")
        if r.get("weaknesses"):
            for w in r["weaknesses"]:
                lines.append(f"- Weakness: {w}")
        lines.append("")
    with open(log_path, "w") as f:
        f.write("\n".join(lines))


def review_loop(
    paper_dir: str,
    venue: str = "NeurIPS",
    exec_runtime: Runtime = None,
    review_runtime: Runtime = None,
    max_rounds: int = 4,
    pass_threshold: int = 7,
    callback: Optional[callable] = None,
) -> dict:
    """Cross-model review loop until paper passes or max rounds.

    Args:
        paper_dir:       Path to paper/ directory with .tex files.
        venue:           Target venue.
        exec_runtime:    Runtime for fixing (executor).
        review_runtime:  Runtime for reviewing (different model recommended).
        max_rounds:      Max review-fix cycles.
        pass_threshold:  Min score to pass (default: 7/10).
        callback:        Called after each round.

    Returns:
        dict with: passed, rounds, final_score, reviews
    """
    if exec_runtime is None:
        raise ValueError("exec_runtime is required")
    if review_runtime is None:
        review_runtime = exec_runtime

    paper_dir = os.path.expanduser(paper_dir)
    paper_content = _read_paper(paper_dir)
    log_path = os.path.join(os.path.dirname(paper_dir), "AUTO_REVIEW.md")
    reviews = []

    from openprogram.providers.registry import create_runtime

    for round_num in range(1, max_rounds + 1):
        # Review phase — fresh runtime each round
        with create_runtime(model=review_runtime.model) as round_review_rt:
            truncated = paper_content[:15000]
            if len(paper_content) > 15000:
                import warnings
                warnings.warn(
                    f"Paper content truncated from {len(paper_content)} to 15000 chars for review",
                    stacklevel=2,
                )
            reply = review_paper(
                paper_content=truncated,
                venue=venue,
                runtime=round_review_rt,
            )

        try:
            review = parse_json(reply)
        except ValueError:
            review = {"score": 0, "passed": False, "weaknesses": [],
                      "strengths": [], "parse_error": reply[:500]}
        review["round"] = round_num
        review["full_review"] = reply
        reviews.append(review)
        _save_review_log(log_path, reviews)

        if callback and callback({"type": "review", **review}) is False:
            break

        if review.get("score", 0) >= pass_threshold:
            return {"passed": True, "rounds": round_num,
                    "final_score": review["score"], "reviews": reviews}

        # Fix phase — fresh runtime each round
        with create_runtime(model=exec_runtime.model) as round_exec_rt:
            paper_content = fix_paper(
                paper_content=paper_content[:15000],
                review_feedback=reply[:5000],
                round_num=round_num,
                runtime=round_exec_rt,
            )

        if callback:
            callback({"type": "fix", "round": round_num})

    return {
        "passed": False, "rounds": max_rounds,
        "final_score": reviews[-1].get("score", 0) if reviews else 0,
        "reviews": reviews,
    }
