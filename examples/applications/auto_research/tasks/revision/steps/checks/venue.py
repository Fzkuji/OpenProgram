"""Venue and repository rules: camera-ready format, authors, file layout, code link.

The values come from paper.rules (defaults in rules/venue.py, overrides in the paper's
workflow/rules.json).
"""

import re
from pathlib import Path

from ...rules import latex as latex_rules
from .base import finding


def format_(paper) -> list[dict]:
    found = []
    for line in paper.rules.required_source:
        if line not in paper.main_source:
            found.append(finding("FAIL", "format", f"Missing required line: {line}"))
    for text in paper.rules.forbidden_source:
        if text in paper.draft_flat:
            found.append(finding("FAIL", "format", f"Forbidden text present: {text}"))
    for section in paper.rules.required_sections:
        if not re.search(r"\\(?:sub)*section\*?\{" + re.escape(section), paper.draft_flat):
            found.append(finding("FAIL", "format", f"Required section missing: {section}"))
    return found


def authors(paper) -> list[dict]:
    block = re.search(r"\\author\{(.*?)\n\}", paper.main_source, re.S)
    text = block.group(1) if block else paper.main_source
    return [finding("FAIL", "authors", f"Author block lacks {item}") for item in paper.rules.required_authors
            if item not in text]


def file_layout(paper) -> list[dict]:
    return [finding("WARN", "file-layout", f"Section file {name} should start with its number")
            for name in re.findall(r"\\input\{([^}]+)\}", paper.main_source)
            if not re.match(r"\d", Path(name).name)]


def code_link(paper) -> list[dict]:
    if re.search(r"github\.com|\\url\{|anonymous\.4open", paper.draft_flat):
        return []
    return [finding("WARN", "code-link", "No code link found; the guide asks to share the code.")]


def comment_macros(paper) -> list[dict]:
    missing = [m for m in latex_rules.COMMENT_MACROS if "\\newcommand{" + m + "}" not in paper.main_source]
    if not missing:
        return []
    return [finding("WARN", "comment-macros", "Define the comment commands " + ", ".join(missing) + " from the guide")]


CHECKS = [format_, authors, file_layout, code_link, comment_macros]
