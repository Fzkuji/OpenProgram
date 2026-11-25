"""The prose of a LaTeX paper as sentences with the file and line each starts on.
Plain code, no model calls.

LaTeX commands, math, citations, comments, labels and floats' code are dropped; the
text of \\emph, \\footnote, \\caption and the like stays. Each sentence keeps whether a
\\cite stands in it and which of its words are inside quotation marks or \\enquote.
"""

import re
import unicodedata
from pathlib import Path

INPUT = re.compile(r"\\(?:input|include)\{([^}]+)\}")
COMMENT = re.compile(r"(?<!\\)%.*")
MARK = re.compile(r"\x00\d+\x00")

DROPPED_ENVS = ("equation|align|gather|multline|eqnarray|displaymath|math|flalign|table|tabular|tabularx|"
                "algorithm|algorithmic|lstlisting|verbatim|minted|tikzpicture|thebibliography|filecontents")
ENV = re.compile(r"\\begin\{(" + DROPPED_ENVS + r")(\*?)\}.*?\\end\{\1\2\}", re.S)
MATH = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|(?<!\\)\$.*?(?<!\\)\$", re.S)
VERB = re.compile(r"\\verb\*?(.).*?\1")
CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{[^}]*\}")
ENQUOTE = re.compile(r"\\enquote\*?\{([^{}]*)\}")
WITH_ARG = re.compile(r"\\(?:label|ref|eqref|autoref|cref|Cref|pageref|url|includegraphics|bibliographystyle|"
                      r"bibliography|vspace|hspace|documentclass|usepackage|setlength|addtolength|footnotemark|"
                      r"thispagestyle|pagestyle|addbibresource|graphicspath|begin|end)\*?"
                      r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})?")
HREF = re.compile(r"\\href\{[^}]*\}")
HEADING = re.compile(r"\\(?:part|chapter|(?:sub)*section|(?:sub)?paragraph|title|author|date)\*?"
                     r"(?:\s*\[[^\]]*\])?\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
BREAK = re.compile(r"\\(?:item|par|caption|maketitle|newline|linebreak|clearpage|newpage)\b(?:\s*\[[^\]]*\])?|\\\\")
ACCENT = re.compile(r"\\['\"`^~=.](?:\{(\w)\}|(\w))")
COMMAND = re.compile(r"\\[a-zA-Z@]+\*?")
TOKEN = re.compile(r"\x00(\d+)\x00|\x01|\x02|[“”\"]|[^\s\x00\x01\x02“”\"]+")

# Words that end in a full stop without ending the sentence.
ABBREVIATIONS = {"e.g.", "i.e.", "al.", "etc.", "fig.", "figs.", "sec.", "secs.", "eq.", "eqs.", "tab.", "vs.",
                 "cf.", "resp.", "approx.", "no.", "ch.", "app.", "dr.", "mr.", "ms.", "prof.", "st.", "viz."}


def norm(word: str) -> str:
    """One word as compared: ASCII, lowercase, punctuation gone."""
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", word.lower())


def norm_words(text: str) -> list[str]:
    """Plain text (a PDF, an abstract) as compared words."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text or "")       # words hyphenated at a line end
    return [w for w in (norm(t) for t in text.split()) if w]


def read_lines(root: Path, name: str, depth: int = 0) -> list[tuple[str, int, str, bool]]:
    """(file, line, text without its comment, blank) for every line LaTeX reads,
    \\input and \\include followed in place."""
    if depth > 20:
        raise ValueError("\\input nesting is too deep")
    path = root / name
    if not path.suffix:
        path = path.with_suffix(".tex")
    if not path.is_file():
        return []
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    out = []
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = COMMENT.sub("", raw)
        pos = 0
        for m in INPUT.finditer(line):
            out.append((rel, n, line[pos:m.start()], False))
            out += read_lines(root, m.group(1).strip(), depth + 1)
            pos = m.end()
        out.append((rel, n, line[pos:], not raw.strip()))
    return out


def document(lines: list[tuple[str, int, str, bool]]) -> list[tuple[str, int, str, bool]]:
    """The lines between \\begin{document} and the bibliography or \\end{document};
    every line when there is no \\begin{document} (a fragment)."""
    start = next((i for i, l in enumerate(lines) if r"\begin{document}" in l[2]), -1)
    if start < 0:
        return lines
    end = next((i for i, l in enumerate(lines) if i > start and re.search(
        r"\\bibliography\{|\\printbibliography|\\end\{document\}|\\begin\{thebibliography\}", l[2])), len(lines))
    return lines[start:end]


def _keep_marks(pattern, text: str, repl: str = " ") -> str:
    """Drop what pattern matches but keep the line marks inside it."""
    return pattern.sub(lambda m: repl + "".join(MARK.findall(m.group(0))), text)


def _strip(text: str) -> str:
    text = _keep_marks(VERB, text)
    text = _keep_marks(ENV, text, " \x02 ")
    text = _keep_marks(MATH, text)
    text = text.replace("``", "“").replace("''", "”")
    for _ in range(3):
        text = ENQUOTE.sub(r"“\1”", text)
    text = _keep_marks(CITE, text, " \x01 ")
    text = _keep_marks(HEADING, text, " \x02 ")
    text = _keep_marks(HREF, text)
    text = _keep_marks(WITH_ARG, text)
    text = BREAK.sub(" \x02 ", text)
    text = ACCENT.sub(lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"\\[%&#_$]", lambda m: m.group(0)[1], text)
    text = re.sub(r"\\[,;:! ]|~", " ", text)
    text = COMMAND.sub("", text)
    return text.replace("{", "").replace("}", "")


def sentences(lines: list[tuple[str, int, str, bool]]) -> list[dict]:
    """Each sentence: file, line, words (as written), norm (as compared), quoted (per
    compared word) and cited."""
    marked = "".join("\x02" if blank else f"\x00{i}\x00{text}\n" for i, (_, _, text, blank) in enumerate(lines))
    out, here, quoted = [], 0, False
    current = None

    def close():
        nonlocal current
        if current and current["norm"]:
            out.append(current)
        current = None

    for m in TOKEN.finditer(_strip(marked)):
        tok = m.group(0)
        if m.group(1) is not None:
            here = int(m.group(1))
        elif tok == "\x02":
            close()
            quoted = False
        elif tok == "\x01":
            if current is not None:
                current["cited"] = True
            elif out:
                out[-1]["cited"] = True      # "... as shown.~\cite{x}" cites the sentence before
        elif tok in "“”\"":
            quoted = tok == "“" or (tok == '"' and not quoted)
        else:
            w = norm(tok)
            if not w:
                if current is not None and tok[-1:] in ".!?":
                    close()
                continue
            if current is None:
                file, line = (lines[here][0], lines[here][1]) if lines else ("", 0)
                current = {"file": file, "line": line, "words": [], "norm": [], "quoted": [], "cited": False}
            current["words"].append(tok)
            current["norm"].append(w)
            current["quoted"].append(quoted)
            bare = tok.rstrip(")'")
            if bare[-1:] in ".!?" and bare.lower() not in ABBREVIATIONS and not re.fullmatch(r"[A-Z]\.", bare):
                close()
    close()
    return out


def paper_sentences(root: Path, main_tex: str) -> list[dict]:
    return sentences(document(read_lines(root, main_tex)))


def tex_words(path: Path) -> list[str]:
    """The compared words of a .tex file read as a source (its \\input files followed)."""
    return [w for s in sentences(document(read_lines(path.parent, path.name))) for w in s["norm"]]


def title(root: Path, main_tex: str) -> str:
    """The text of \\title{...}, braces balanced; empty when there is none."""
    path = root / main_tex
    text = COMMENT.sub("", path.read_text(encoding="utf-8", errors="ignore")) if path.is_file() else ""
    m = re.search(r"\\title\*?(?:\s*\[[^\]]*\])?\s*\{", text)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    inner = text[m.end():i - 1]
    inner = re.sub(r"\\\\|\\thanks\{[^}]*\}", " ", inner)
    inner = COMMAND.sub("", inner).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", inner).strip()


__all__ = ["norm", "norm_words", "paper_sentences", "sentences", "tex_words", "title"]
