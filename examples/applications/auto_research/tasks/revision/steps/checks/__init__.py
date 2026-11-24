"""Step 2: deterministic checks. Each check takes the prepared Paper and returns findings."""

from . import citations, floats, latex, output, text, venue

CHECKS = output.CHECKS + venue.CHECKS + floats.CHECKS + text.CHECKS + latex.CHECKS + citations.CHECKS


def run(paper) -> list[dict]:
    return [item for check in CHECKS for item in check(paper)]
