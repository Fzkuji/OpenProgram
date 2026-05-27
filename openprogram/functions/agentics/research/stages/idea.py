"""
idea — idea generation and novelty evaluation stage.

Generates research ideas from survey gaps, evaluates novelty,
and ranks them by feasibility and impact.
"""

from __future__ import annotations

import os
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.functions.agentics.research._paths import (
    find_project_artifact,
    writable_project_dir,
)
from openprogram.functions.agentics._utils import parse_json


@agentic_function(render_range={"callers": 0})
def generate_ideas(topic: str, gaps: str, runtime: Runtime) -> str:
    """Generate research ideas that address identified gaps.

    Brainstorm novel approaches. For each idea:
    1. Title: concise, descriptive name
    2. Hypothesis: what you believe and why
    3. Approach: high-level method (2-3 sentences)
    4. Expected outcome: what success looks like
    5. Feasibility: resources/time estimate (low/medium/high effort)
    6. Risk: what could go wrong

    Generate 3-5 diverse ideas ranging from incremental to ambitious.
    Each idea should directly address at least one identified gap.
    Prefer ideas that are testable with existing datasets/benchmarks.

    Output: Structured markdown with numbered ideas.
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Research topic: {topic}\n\n"
            f"Identified gaps:\n{gaps}"
        )},
    ])


@agentic_function(render_range={"callers": 0})
def check_novelty(idea: str, runtime: Runtime) -> str:
    """Check if a research idea is novel.

    Search your knowledge for existing work that:
    - Solves the same problem with the same approach
    - Uses a very similar method on the same task
    - Has already been published at a top venue

    Be honest: if the idea is incremental, say so.
    If truly novel, explain what makes it different from closest work.

    Output JSON:
    {"novel": true/false, "confidence": 0.0-1.0,
     "closest_work": "description of most similar existing work",
     "differentiation": "what makes this idea different"}
    """
    return runtime.exec(content=[
        {"type": "text", "text": idea},
    ])


@agentic_function(render_range={"callers": 0})
def rank_ideas(ideas: str, novelty_results: str, runtime: Runtime) -> str:
    """Rank research ideas by overall promise.

    Consider: novelty, feasibility, potential impact, risk.
    Weight novelty and feasibility highest — a brilliant but infeasible
    idea is worse than a solid, executable one.

    Output JSON:
    {"ranking": [{"rank": 1, "title": "...", "score": 8.5,
                  "reasoning": "why this ranks here"}]}
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Ideas:\n{ideas}\n\n"
            f"Novelty assessments:\n{novelty_results}"
        )},
    ])


def run_idea(
    topic: str,
    project_dir: str,
    runtime: Runtime,
) -> dict:
    """Run idea generation stage.

    Reads gaps from literature stage, generates and ranks ideas.

    Args:
        topic:        Research topic.
        project_dir:  Project directory.
        runtime:      LLM runtime.

    Returns:
        dict with ideas, novelty checks, and ranking.
    """
    project_dir = os.path.expanduser(project_dir)

    # Read gaps from literature stage
    gaps_path = find_project_artifact(
        project_dir,
        "related_work/gaps.md",
        "literature review/synthesis/gaps.md",
        "synthesis/gaps.md",
    )
    if gaps_path is not None:
        with open(gaps_path, "r", encoding="utf-8") as f:
            gaps = f.read()
    else:
        import warnings
        warnings.warn(
            "Gaps file not found in the project directory or its deliverables mirror. "
            "Run the 'literature' stage first for better results.",
            stacklevel=2,
        )
        gaps = "No gaps identified yet. Generate ideas based on the topic directly."

    ideas = generate_ideas(topic=topic, gaps=gaps, runtime=runtime)

    # Check novelty for each idea
    novelty = check_novelty(idea=ideas, runtime=runtime)

    # Rank
    ranking = rank_ideas(ideas=ideas, novelty_results=novelty, runtime=runtime)

    # Save
    output_dir = writable_project_dir(project_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "IDEA_REPORT.md", "w", encoding="utf-8") as f:
        f.write(f"# Idea Report: {topic}\n\n")
        f.write(f"## Generated Ideas\n{ideas}\n\n")
        f.write(f"## Novelty Assessment\n{novelty}\n\n")
        f.write(f"## Ranking\n{ranking}\n")

    return {"ideas": ideas, "novelty": novelty, "ranking": ranking}
