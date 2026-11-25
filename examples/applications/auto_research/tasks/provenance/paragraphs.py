"""The paper's prose as paragraphs of source lines, in reading order. Plain code.

A paragraph is a run of non-blank lines between blank lines, headings, \\input
points and display environments. Each line keeps its raw text and its tokens as a
reader sees them: math, citations and references are shown but not counted, every
other token with letters or digits is a counted word (compared lowercased, without
punctuation, as tasks/overlap compares words).
"""

import re
from pathlib import Path

from ..overlap.prose import ACCENT, COMMAND, COMMENT, HREF, MATH, VERB, norm

INPUT = re.compile(r"\\(?:input|include|subfile)\{([^}]+)\}")
SECTION = re.compile(r"\\(chapter|section|subsection|subsubsection|paragraph)\*?(?:\s*\[[^\]]*\])?\s*"
                     r"\{((?:[^{}]|\{[^{}]*\})*)\}")
DROPPED = ("equation|align|gather|multline|eqnarray|displaymath|math|flalign|table|tabular|tabularx|algorithm|"
           "algorithmic|lstlisting|verbatim|minted|tikzpicture|thebibliography|filecontents")
BEGIN_DROP = re.compile(r"\\begin\{(?:" + DROPPED + r")\*?\}")
END_DROP = re.compile(r"\\end\{(?:" + DROPPED + r")\*?\}")
CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
REF = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref)\*?\{([^}]*)\}")
NO_TEXT = re.compile(r"\\(?:label|url|includegraphics|bibliographystyle|bibliography|vspace|hspace|setlength|"
                     r"addtolength|footnotemark|thispagestyle|pagestyle|addbibresource|graphicspath|begin|end|"
                     r"newtheorem|usepackage|documentclass|maketitle|centering|title|author|date)\*?"
                     r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})*")
BREAKING = re.compile(r"\\(?:bibliography|printbibliography|maketitle|appendix|clearpage|newpage)\b")
HIDE = "\x03"


def shown(raw: str) -> list[tuple[str, str]]:
    """The tokens of one source line as (text shown, compared word); the compared word
    is empty for math, citations, references and punctuation."""
    code = COMMENT.sub("", raw)
    kept: list[str] = []

    def hide(text: str) -> str:
        kept.append(text)
        return f" {HIDE}{len(kept) - 1}{HIDE} "

    code = VERB.sub(lambda m: hide(m.group(0)), code)
    code = MATH.sub(lambda m: hide(m.group(0)), code)
    code = CITE.sub(lambda m: hide("[" + m.group(1).strip() + "]"), code)
    code = REF.sub(lambda m: hide("[" + m.group(1).strip() + "]"), code)
    code = code.replace("``", "“").replace("''", "”")
    code = HREF.sub(" ", code)
    code = NO_TEXT.sub(" ", code)
    code = ACCENT.sub(lambda m: m.group(1) or m.group(2), code)
    code = re.sub(r"\\[%&#_$]", lambda m: m.group(0)[1], code)
    code = re.sub(r"\\[,;:! ]|~|\\\\", " ", code)
    code = COMMAND.sub("", code).replace("{", "").replace("}", "")
    out = []
    for tok in code.split():
        if tok.startswith(HIDE):
            parts = re.split(HIDE + r"(\d+)" + HIDE, tok)
            text = "".join(kept[int(p)] if i % 2 else p for i, p in enumerate(parts))
            out.append((text, ""))
        else:
            out.append((tok, norm(tok)))
    return out


def words(text: str) -> list[str]:
    """The compared words of a piece of LaTeX (several lines allowed)."""
    return [w for line in text.splitlines() for _, w in shown(line) if w]


class _Walk:
    def __init__(self, root: Path | None, follow: bool):
        self.root, self.follow = root, follow
        self.section, self.drop, self.display = "", 0, False
        self.out: list[dict] = []
        self.current: list[dict] = []
        self.current_file = ""

    def flush(self) -> None:
        if self.current and any(w for line in self.current for _, w in line["tokens"]):
            self.out.append({"file": self.current_file, "section": self.section or "Front matter",
                             "start": self.current[0]["line"], "end": self.current[-1]["line"],
                             "lines": self.current})
        self.current = []

    def add(self, rel: str, n: int, raw: str) -> None:
        if self.current_file != rel:
            self.flush()
            self.current_file = rel
        self.current.append({"line": n, "raw": raw, "tokens": shown(raw)})

    def text(self, text: str, rel: str, main: bool, depth: int = 0, seen: tuple = ()) -> None:
        lines = text.splitlines()
        start, stop = 0, len(lines)
        if main:
            begin = next((i for i, l in enumerate(lines) if r"\begin{document}" in COMMENT.sub("", l)), -1)
            if begin >= 0:
                start = begin + 1
                stop = next((i for i in range(start, len(lines)) if r"\end{document}" in COMMENT.sub("", lines[i])),
                            len(lines))
        for i in range(start, stop):
            self.line(rel, i + 1, lines[i], depth, seen)
        self.flush()

    def line(self, rel: str, n: int, raw: str, depth: int, seen: tuple) -> None:
        code = COMMENT.sub("", raw)
        if self.drop or self.display:
            if self.display and "\\]" in code:
                self.display = False
            elif self.drop:
                self.drop = max(0, self.drop - len(END_DROP.findall(code)) + len(BEGIN_DROP.findall(code)))
            if "\\caption" in code:
                self.add(rel, n, raw)
            return
        if not raw.strip():
            self.flush()
            return
        if not code.strip():
            return                      # a comment line does not end a paragraph
        found = INPUT.search(code)
        if found:
            self.flush()
            name = found.group(1).strip()
            if self.follow and self.root is not None and depth < 20:
                path = (self.root / name)
                path = path if path.suffix else path.with_suffix(".tex")
                child = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else ""
                if child and path.is_file() and child not in seen:
                    self.text(path.read_text(encoding="utf-8", errors="ignore"), child, False, depth + 1,
                              seen + (child,))
            return
        heading = SECTION.search(code)
        if heading:
            self.flush()
            if heading.group(1) in ("chapter", "section"):
                self.section = " ".join(t for t, _ in shown(heading.group(2))) or heading.group(2).strip()
            rest = code[heading.end():]
            if rest.strip():
                self.add(rel, n, rest)
            return
        if re.search(r"\\begin\{abstract\}", code):
            self.flush()
            self.section = "Abstract"
            rest = code.split("\\begin{abstract}", 1)[1]
            if rest.strip():
                self.add(rel, n, raw)
            return
        if re.search(r"\\end\{abstract\}", code):
            before = code.split("\\end{abstract}", 1)[0]
            if before.strip():
                self.add(rel, n, raw)
            self.flush()
            return
        opened = len(BEGIN_DROP.findall(code)) - len(END_DROP.findall(code))
        if opened > 0:
            self.flush()
            self.drop = opened
            if "\\caption" in code:
                self.add(rel, n, raw)
            return
        if code.strip().startswith("\\[") and "\\]" not in code:
            self.flush()
            self.display = True
            return
        if BREAKING.search(code):
            self.flush()
            return
        self.add(rel, n, raw)


def read(root: Path, main_tex: str) -> list[dict]:
    """Every paragraph of the paper in reading order, \\input files followed: file,
    section, start and end line, and its lines with their tokens."""
    walk = _Walk(root, follow=True)
    path = root / main_tex
    walk.text(path.read_text(encoding="utf-8", errors="ignore"), main_tex, True, 0, (main_tex,))
    for k, p in enumerate(walk.out, 1):
        p["id"] = f"P{k}"
    return walk.out


def split(text: str, rel: str, main: bool) -> list[dict]:
    """The paragraphs of one version of one file, \\input not followed (for the replay)."""
    walk = _Walk(None, follow=False)
    walk.text(text, rel, main)
    return walk.out


def text_of(paragraph: dict) -> str:
    """The paragraph as a reader sees it, one space between tokens."""
    return " ".join(t for line in paragraph["lines"] for t, _ in line["tokens"])


def norm_words(paragraph: dict) -> list[str]:
    return [w for line in paragraph["lines"] for _, w in line["tokens"] if w]


__all__ = ["norm_words", "read", "shown", "split", "text_of", "words"]
