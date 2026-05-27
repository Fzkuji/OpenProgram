"""
submission — pre-submission checklist stage.

Checks anonymity, page limits, format, references, and other
submission requirements based on venue guidelines.
"""

from __future__ import annotations

import os

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(render_range={"callers": 0})
def check_submission(paper_content: str, venue: str, runtime: Runtime) -> str:
    """Pre-submission checklist for academic paper.

    Run final checks before paper submission. Check for ALL of the
    following:

    1. Anonymity:
       - No author names, affiliations, or institutional info
       - No "our previous work..." or self-identifying references
       - No personal info in code links or supplementary
       - Check for hidden metadata

    2. Format:
       - Page limit compliance (body, references, appendix separately)
       - Correct venue template and meta information
       - Title and abstract match submission system

    3. References:
       - All from Google Scholar (not DBLP or other sources)
       - Published versions preferred over arXiv
       - No duplicate citations (arXiv + published of same paper)
       - Recent baselines (within 2 years)
       - No AI-generated fake references

    4. Figures & Tables:
       - All referenced in text ("Figure X", "Table Y")
       - Order matches first mention in text
       - Vector format (PDF/EPS) for figures, not PNG/JPG
       - Text in figures >= body text size
       - Booktabs style for tables

    5. Writing:
       - Last line of each paragraph has >= 4 words
       - Consistent terminology throughout
       - No absolute claims without hedging (use "generally", "often")
       - Proper label prefixes (sec:, fig:, tab:, equ:, alg:)

    6. Code submission:
       - Anonymous repository link
       - No personal info or hardcoded paths in code
       - No hidden files (.git) with author info

    Output: Checklist with [PASS]/[FAIL]/[WARN] for each item.
    Flag critical issues that could cause desk rejection.
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Target venue: {venue}\n\n"
            f"Paper content:\n{paper_content}"
        )},
    ])


def run_submission_check(
    project_dir: str,
    venue: str,
    runtime: Runtime,
) -> dict:
    """Run pre-submission checks.

    Args:
        project_dir:  Project directory.
        venue:        Target venue.
        runtime:      LLM runtime.

    Returns:
        dict with checklist results.
    """
    project_dir = os.path.expanduser(project_dir)
    paper_dir = os.path.join(project_dir, "paper")

    # Read paper
    parts = []
    for fname in sorted(os.listdir(paper_dir)):
        if fname.endswith(".tex"):
            with open(os.path.join(paper_dir, fname), "r") as f:
                parts.append(f.read())
    paper_content = "\n\n".join(parts)

    result = check_submission(
        paper_content=paper_content[:15000],
        venue=venue,
        runtime=runtime,
    )

    # Save report
    with open(os.path.join(project_dir, "SUBMISSION_CHECKLIST.md"), "w") as f:
        f.write(f"# Submission Checklist — {venue}\n\n{result}")

    return {"checklist": result}
