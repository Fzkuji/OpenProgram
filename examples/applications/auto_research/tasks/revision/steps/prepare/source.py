"""Read LaTeX sources: flatten inputs, find floats, references, sections and paragraphs."""

import re
from pathlib import Path

INPUT = re.compile(r"\\input\{([^}]+)\}")
FLOAT = re.compile(r"\\begin\{(figure\*?|table\*?)\}(?:\[([^\]]*)\])?")
LABEL = re.compile(r"\\label\{((?:fig|tab):[^}]+)\}")
REF = re.compile(r"\\(?:ref|autoref|cref)\{((?:fig|tab):[^}]+)\}")
GRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
CAPTION = re.compile(r"\\caption\{")


def strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def flatten(root: Path, name: str, depth: int = 0) -> str:
    """Inline every \\input file, as LaTeX would read the document."""
    if depth > 20:
        raise ValueError("\\input nesting is too deep")
    path = root / name
    if not path.suffix:
        path = path.with_suffix(".tex")
    text = strip_comments(path.read_text(encoding="utf-8"))
    return INPUT.sub(lambda m: flatten(root, m.group(1), depth + 1), text)


def body(text: str) -> str:
    """The document between \\begin{document} and the bibliography."""
    start = text.find(r"\begin{document}")
    end = text.find(r"\bibliography{")
    return text[start if start >= 0 else 0:end if end >= 0 else len(text)]


def floats(text: str) -> list[dict]:
    """Each figure or table: kind, placement option, label, graphics, caption."""
    found = []
    for match in FLOAT.finditer(text):
        env = match.group(1)
        close = text.find(r"\end{" + env + "}", match.end())
        block = text[match.end():close if close >= 0 else len(text)]
        labels = LABEL.findall(block)
        found.append({
            "kind": "table" if env.startswith("table") else "figure",
            "placement": match.group(2),
            "label": labels[0] if labels else None,
            "labels": labels,
            "graphics": GRAPHICS.findall(block),
            "caption": caption(block),
        })
    return found


def caption(block: str) -> str:
    """The caption text with balanced braces, or an empty string."""
    match = CAPTION.search(block)
    if not match:
        return ""
    depth, start = 1, match.end()
    for index in range(start, len(block)):
        if block[index] == "{":
            depth += 1
        elif block[index] == "}":
            depth -= 1
            if depth == 0:
                return block[start:index].strip()
    return block[start:].strip()


def main_body(text: str) -> str:
    """The document before the appendix, which the page-top rule covers."""
    end = text.find(r"\appendix")
    return body(text) if end < 0 else body(text[:end])


# A blank line, a list item or a list boundary starts a new paragraph.
PARAGRAPH_BREAK = r"\n\s*\n|\\item\b|\\(?:begin|end)\{(?:itemize|enumerate)\}(?:\[[^\]]*\])?"

LAYOUT = re.compile(r"\\(?:FloatBarrier|clearpage|newpage|noindent|vspace\*?\{[^}]*\})\s*")


def without_floats(text: str) -> str:
    text = LAYOUT.sub("", text)
    return re.sub(r"\\begin\{(figure\*?|table\*?)\}.*?\\end\{\1\}", "", text, flags=re.S)


def first_mentions(text: str) -> list[str]:
    """Float labels in the order the running text first refers to them."""
    order = []
    for label in REF.findall(without_floats(text)):
        if label not in order:
            order.append(label)
    return order


def paragraphs(text: str) -> list[str]:
    """Running-text paragraphs, without floats, normalized for comparison."""
    parts = re.split(PARAGRAPH_BREAK, without_floats(body(text)))
    cleaned = []
    for part in parts:
        line = clean(part)
        if len(line.split()) > 3:
            cleaned.append(line)
    return cleaned


def clean(chunk: str) -> str:
    """One paragraph on one line, without the document and abstract wrappers."""
    chunk = re.sub(r"\\(?:begin|end)\{(?:document|abstract)\}|\\maketitle", " ", chunk)
    return " ".join(chunk.split())


def sentences(paragraph: str) -> list[str]:
    plain = re.sub(r"\\(?:item|paragraph|subsection|section)\*?(?:\{[^}]*\})?", " ", paragraph)
    plain = re.sub(r"(vs|e\.g|i\.e|et al|Fig|Sec|Eq)\.\\?\s", r"\1<dot> ", plain)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\\])", plain)
    return [p.replace("<dot>", ".").strip() for p in parts if p.strip()]


def sectioned_paragraphs(text: str) -> list[tuple[str, str]]:
    """Each running-text paragraph with the title of the section or subsection it is in."""
    result, current = [], "Abstract"
    for part in re.split(r"(\\(?:sub)*section\*?\{[^}]*\})", without_floats(body(text))):
        heading = re.match(r"\\(?:sub)*section\*?\{([^}]*)\}", part)
        if heading:
            current = heading.group(1)
            continue
        for chunk in re.split(PARAGRAPH_BREAK, part):
            line = clean(chunk)
            if len(line.split()) > 3:
                result.append((current, line))
    return result


def added_paragraphs(base_flat: str, draft_flat: str) -> list[str]:
    old = set(paragraphs(base_flat))
    return [p for p in paragraphs(draft_flat) if p not in old]
