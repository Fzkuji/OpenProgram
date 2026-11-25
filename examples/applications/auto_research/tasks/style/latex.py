"""LaTeX that a translator may not touch, hidden behind placeholders. Plain code, no model
calls.

mask() turns a source text into prose with numbered placeholders ⟦n⟧: every command with
its options and arguments, every environment boundary, math, comment, brace, & and
control symbol. The arguments of text commands (\\section, \\caption, \\emph, ...) stay
visible between their placeholders, so their words are translated; the arguments of every
other command (\\cite, \\ref, \\label, \\includegraphics, unknown macros) are hidden whole.
restore() puts the source back, and problems() says what a translation broke.
"""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

OPEN, CLOSE = "⟦", "⟧"
SLOT = re.compile(OPEN + r"(\d+)" + CLOSE)

# Commands whose brace argument is prose: the command and braces are hidden, the words are not.
TEXT_ARG = {"part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph", "caption",
            "title", "textbf", "textit", "textsl", "textsc", "textrm", "textsf", "texttt", "emph", "underline",
            "footnote", "thanks", "text", "mbox", "fbox", "enquote", "textup", "textmd"}
# Environments hidden whole: math, code, drawings, the bibliography.
HIDDEN_ENVS = {"equation", "align", "gather", "multline", "eqnarray", "displaymath", "math", "flalign", "alignat",
               "split", "verbatim", "Verbatim", "lstlisting", "minted", "tikzpicture", "algorithmic", "algorithm2e",
               "thebibliography", "filecontents", "comment", "pgfpicture", "axis"}
SPECIAL = set("\\{}$&#^_%")          # a translation must not add any of these outside placeholders
LETTERS = re.compile(r"[A-Za-z]{2}")
INCLUDE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


def mask(text: str, start: int = 0) -> tuple[str, list[str]]:
    """(masked text, slots): slot k (numbered from start + 1) is the source behind ⟦k⟧.
    Adjacent hidden pieces share one slot."""
    if OPEN in text or CLOSE in text:
        raise ValueError(f"the source already contains {OPEN} or {CLOSE}")
    out: list[str] = []
    slots: list[str] = []
    last = [False]                   # was the previous piece a slot (then the next hidden piece joins it)

    def hide(s: str):
        if last[0]:
            slots[-1] += s
        else:
            slots.append(s)
            out.append(f"{OPEN}{start + len(slots)}{CLOSE}")
        last[0] = True

    def show(s: str):
        out.append(s)
        last[0] = False

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "%":
            j = text.find("\n", i)
            j = n if j < 0 else j
            hide(text[i:j])
            i = j
        elif text.startswith("\\begin{", i):
            m = re.match(r"\\begin\{([^}]*)\}", text[i:])
            name = m.group(1)
            if name.rstrip("*") in HIDDEN_ENVS:
                end = text.find("\\end{" + name + "}", i + m.end())
                j = n if end < 0 else end + len("\\end{" + name + "}")
            else:
                j = groups(text, i + m.end())
            hide(text[i:j])
            i = j
        elif text.startswith("$$", i):
            end = text.find("$$", i + 2)
            j = n if end < 0 else end + 2
            hide(text[i:j])
            i = j
        elif c == "$":
            m = re.compile(r"(?<!\\)\$").search(text, i + 1)
            j = n if not m else m.end()
            hide(text[i:j])
            i = j
        elif text.startswith("\\\\", i):
            j = i + 2 + (1 if text.startswith("*", i + 2) else 0)
            j = bracket(text, j) if text.startswith("[", j) else j
            hide(text[i:j])
            i = j
        elif text.startswith("\\[", i) or text.startswith("\\(", i):
            close = "\\]" if text[i + 1] == "[" else "\\)"
            end = text.find(close, i + 2)
            j = n if end < 0 else end + 2
            hide(text[i:j])
            i = j
        elif c == "\\":
            m = re.match(r"\\([A-Za-z@]+)\*?", text[i:])
            if not m:
                hide(text[i:i + 2])
                i += 2
                continue
            name, j = m.group(1), i + m.end()
            if name in TEXT_ARG:
                while text.startswith("[", j):
                    j = bracket(text, j)
                if text.startswith("{", j):
                    hide(text[i:j + 1])
                    i = j + 1
                    continue
                hide(text[i:j])
                i = j
            else:
                j = groups(text, j)
                hide(text[i:j])
                i = j
        elif c in "{}&#^_":
            hide(c)
            i += 1
        else:
            show(c)
            i += 1
    return "".join(out), slots


def bracket(text: str, i: int) -> int:
    """The end of the [...] group starting at i (nested brackets and braces balanced)."""
    depth, j = 0, i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(text)


def groups(text: str, j: int) -> int:
    """After a command name: the end of the [...] and {...} groups that follow at once."""
    while j < len(text) and text[j] in "[{":
        j = bracket(text, j)
    return j


def restore(masked: str, slots: dict[int, str]) -> str:
    return SLOT.sub(lambda m: slots[int(m.group(1))], masked)


def placeholders(masked: str) -> Counter:
    return Counter(int(k) for k in SLOT.findall(masked))


def translatable(masked: str) -> bool:
    """Text worth sending: at least two letters in a row outside the placeholders."""
    return bool(LETTERS.search(SLOT.sub(" ", masked)))


def structure(source: str) -> dict:
    """What a translation must keep: environment boundaries, labels and brace balance."""
    plain = re.sub(r"\\[{}%$&#_]", "", source)
    plain = re.sub(r"(?<!\\)%.*", "", plain)
    return {"begin": Counter(re.findall(r"\\begin\{([^}]*)\}", plain)),
            "end": Counter(re.findall(r"\\end\{([^}]*)\}", plain)),
            "labels": sorted(re.findall(r"\\label\{([^}]*)\}", plain)),
            "braces": plain.count("{") - plain.count("}")}


def problems(original: str, translated: str, slots: dict[int, str]) -> list[str]:
    """Why a translated paragraph cannot replace the original; empty when it can."""
    found = []
    want, got = placeholders(original), placeholders(translated)
    missing = sorted((want - got).elements())
    extra = sorted((got - want).elements())
    if missing:
        found.append("placeholders missing: " + " ".join(f"{OPEN}{k}{CLOSE}" for k in missing))
    if extra:
        found.append("placeholders added or repeated: " + " ".join(f"{OPEN}{k}{CLOSE}" for k in extra))
    raw = sorted({ch for ch in SLOT.sub("", translated) if ch in SPECIAL})
    if raw:
        found.append("LaTeX special characters written outside placeholders: " + " ".join(raw))
    if not translated.strip():
        found.append("empty translation")
    if found:
        return found
    a, b = structure(restore(original, slots)), structure(restore(translated, slots))
    for key in ("begin", "end", "labels", "braces"):
        if a[key] != b[key]:
            found.append(f"{key} differ after restoring")
    return found


def paragraphs(masked: str) -> list[str]:
    """The masked text cut after each blank line; joining the parts gives it back."""
    parts = re.split(r"(\n[ \t]*\n\s*)", masked)
    out = []
    for k in range(0, len(parts), 2):
        out.append(parts[k] + (parts[k + 1] if k + 1 < len(parts) else ""))
    return [p for p in out if p]


def included(root: Path, name: str, seen: list[Path] | None = None, depth: int = 0) -> list[Path]:
    """The main file and every file it reads through \\input or \\include, in order."""
    seen = [] if seen is None else seen
    path = root / name
    if not path.suffix:
        path = path.with_suffix(".tex")
    path = path.resolve()
    if depth > 20 or path in seen or not path.is_file() or not path.is_relative_to(root):
        return seen
    seen.append(path)
    text = re.sub(r"(?<!\\)%.*", "", path.read_text(encoding="utf-8", errors="replace"))
    for m in INCLUDE.finditer(text):
        included(root, m.group(1).strip(), seen, depth + 1)
    return seen


# Preamble for the target script

# Chinese uses ctex with the Fandol fonts TeX Live ships: ctex's default macOS fontset looks
# up system fonts that xelatex can wait on indefinitely. The others take a system font.
CJK = {"zh": "ctex", "zh-TW": "xeCJK", "ja": "xeCJK", "ko": "xeCJK"}


def cjk_font(language: str) -> str:
    """A system font for Japanese or Korean, from fc-list; empty when none is found."""
    if not shutil.which("fc-list"):
        return ""
    try:
        out = subprocess.run(["fc-list", f":lang={language.lower()}", "family"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    names = sorted({line.split(",")[0].strip() for line in out.splitlines() if line.strip()})
    return next((n for n in names if not n.startswith(".")), "")


def with_script_support(main: str, language: str, notes: list[str]) -> str:
    """The main file with ctex (Chinese) or xeCJK (Japanese, Korean) loaded right after
    \\documentclass, unless a CJK package or class is already there."""
    package = CJK.get(language)
    if not package:
        return main
    if re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex|\\usepackage(?:\[[^\]]*\])?\{(?:ctex|xeCJK)\}", main):
        return main
    if package == "ctex":
        line = "\\usepackage[UTF8,fontset=fandol]{ctex}"
    else:
        font = cjk_font(language)
        line = "\\usepackage{xeCJK}" + (f"\\setCJKmainfont{{{font}}}" if font else "")
        if not font:
            notes.append(f"no {language} font found by fc-list: xeCJK uses its default font")
    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]*\}", main)
    if not m:
        notes.append("no \\documentclass in the main file: script support was not added")
        return main
    return main[:m.end()] + "\n" + line + main[m.end():]


# Compiling

LOG_ERROR = re.compile(r"^(.*?\.tex):(\d+): (.+)$", re.M)


def compile_errors(log: str) -> list[dict]:
    """file:line: message errors of a -file-line-error log, then any other ! error."""
    out, seen = [], set()
    for m in LOG_ERROR.finditer(log):
        key = (m.group(1), m.group(2), m.group(3))
        if key not in seen:
            seen.add(key)
            out.append({"file": re.sub(r"^\./", "", m.group(1)), "line": int(m.group(2)), "message": m.group(3).strip()})
    if not out:
        for m in re.finditer(r"^! (.+)$", log, re.M):
            after = re.search(r"^l\.(\d+)", log[m.end():m.end() + 2000], re.M)
            out.append({"file": "", "line": int(after.group(1)) if after else 0, "message": m.group(1).strip()})
    return out[:30]
