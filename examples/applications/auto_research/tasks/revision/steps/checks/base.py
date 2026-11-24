"""Shared helpers: the finding record and the prose view of the LaTeX source."""

import re

from ..prepare.source import body

MATH = re.compile(r"\$[^$]*\$|\\\[.*?\\\]|\\begin\{(equation|align|gather|multline)\*?\}.*?\\end\{\1\*?\}", re.S)
VERBATIM = re.compile(r"\\(?:texttt|code|url|href|includegraphics|label|ref|eqref|cite[pt]?|input|usepackage)"
                      r"(?:\[[^\]]*\])?\{[^}]*\}")
TIKZ = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)


def finding(level: str, check: str, message: str) -> dict:
    """level is FAIL (blocks the change), WARN (must be read) or REVIEW (from the reviewer)."""
    return {"level": level, "check": check, "message": message}


def prose(flat: str) -> str:
    """Running text and captions, without math, code, tikz and commands with literal arguments."""
    text = TIKZ.sub(" ", body(flat))
    text = MATH.sub(" MATH ", text)
    return VERBATIM.sub(" CMD ", text)


def excerpt(text: str, at: int, width: int = 70) -> str:
    return " ".join(text[max(0, at - 30):at + width].split())


def matches(level: str, check: str, pattern: str, text: str, message: str, flags=0) -> list[dict]:
    return [finding(level, check, f"{message}: …{excerpt(text, m.start())}…")
            for m in re.finditer(pattern, text, flags)]
