"""
literature — literature survey stage.

Searches for related papers, reads abstracts, and generates
categorized survey notes organized by topic.
"""

from __future__ import annotations

import os

from openprogram.agentic_programming import llm
from openprogram.agentic_programming.function import _current_runtime, agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(render_range={"depth": 0, "siblings": 0})
def survey_topic(topic: str) -> str:
    """Survey the literature for a given research topic.

    Search for and organize the most relevant and recent papers on this
    topic.

    For each paper found:
    - Title, authors, venue, year
    - Core contribution (1-2 sentences)
    - Methodology summary
    - Limitations / gaps

    Organize papers into logical categories/subtopics.
    Prioritize recent work (within 2 years) and top venues.
    Use published versions over arXiv when available.
    Do NOT fabricate papers — only cite real, verifiable work.

    Output: A structured markdown survey organized by subtopic.
    """
    return llm([
        {"type": "text", "text": f"Research topic: {topic}"},
    ])


@agentic_function(render_range={"depth": 0, "siblings": 0})
def identify_gaps(survey: str) -> str:
    """Identify research gaps from a literature survey.

    Analyze the survey and identify:
    1. What problems remain unsolved or underexplored?
    2. What assumptions in existing work are questionable?
    3. Where do methods fail or underperform?
    4. What combinations of approaches haven't been tried?

    Be specific: don't say "more research needed", say exactly what's missing.

    Output: Numbered list of specific, actionable research gaps.
    """
    return llm([
        {"type": "text", "text": survey},
    ])


def run_literature(
    topic: str,
    project_dir: str,
    runtime: Runtime,
) -> dict:
    """Run the literature survey stage.

    Args:
        topic:        Research topic/direction.
        project_dir:  Project directory path.
        runtime:      LLM runtime.

    Returns:
        dict with survey text and identified gaps.
    """
    project_dir = os.path.expanduser(project_dir)

    runtime_token = _current_runtime.set(runtime)
    try:
        survey = survey_topic(topic=topic)
        gaps = identify_gaps(survey=survey)
    finally:
        _current_runtime.reset(runtime_token)

    # Save to project
    rw_dir = os.path.join(project_dir, "related_work")
    os.makedirs(rw_dir, exist_ok=True)

    with open(os.path.join(rw_dir, "survey.md"), "w") as f:
        f.write(f"# Literature Survey: {topic}\n\n{survey}")

    with open(os.path.join(rw_dir, "gaps.md"), "w") as f:
        f.write(f"# Research Gaps\n\n{gaps}")

    return {"survey": survey, "gaps": gaps}
