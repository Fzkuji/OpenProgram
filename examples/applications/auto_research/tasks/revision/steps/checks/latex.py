"""LaTeX habits from the AML guide section 4 and 7, checked on the whole draft."""

import re

from ..prepare.source import body
from .base import finding, matches, prose


def punctuation(paper) -> list[dict]:
    """Spaces after brackets, `` '' quotes, e.g., and i.e., with a comma."""
    text = prose(paper.draft_flat)
    return (matches("WARN", "spacing", r"[)\]][A-Za-z]", text, "Add a space or ~ after ) or ]")
            + matches("WARN", "spacing", r"(?<!\\)\b[a-z]+\}[A-Za-z]{2,}", text, "Add a space after }")
            + matches("FAIL", "quotes", r'"', text, 'Write `` and \'\' instead of "')
            + matches("FAIL", "quotes", r"(?:^|\s)'[A-Za-z]", text, "Open single quotes with `")
            + matches("FAIL", "eg-ie", r"\b(?:e\.g|i\.e)\.(?!,)", text, "Write e.g., and i.e., with a comma"))


def math(paper) -> list[dict]:
    """$...$ for inline math, \\boldsymbol for bold, no punctuation closing a display equation."""
    text = body(paper.draft_flat)
    found = (matches("FAIL", "math-delim", r"\\\(", text, "Use $...$ for inline math")
             + matches("WARN", "boldsymbol", r"\\(?:mathbf|bm)\{", text, "Use \\boldsymbol for bold symbols"))
    for m in re.finditer(r"\\begin\{(equation|align|gather)\*?\}(.*?)\\end\{\1\*?\}|\\\[(.*?)\\\]", text, re.S):
        content = re.sub(r"\\label\{[^}]*\}", "", m.group(2) or m.group(3) or "").strip()
        if content.endswith((",", ".")):
            found.append(finding("WARN", "display-punct", f"Display equation ends with punctuation: {content[-40:]}"))
    return found


def itemize(paper) -> list[dict]:
    return [finding("WARN", "itemize", f"{m.group(1)} without [leftmargin=*]")
            for m in re.finditer(r"\\begin\{(itemize|enumerate)\}(\[[^\]]*\])?", body(paper.draft_flat))
            if "leftmargin=*" not in (m.group(2) or "").replace(" ", "")]


CHECKS = [punctuation, math, itemize]
